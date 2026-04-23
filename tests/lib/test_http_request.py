"""Tests for clawmes.lib.http request paths.

The allowlist is tested separately in test_http.py. These tests cover
the actual HTTP transport (mocked via monkeypatching ``httpx``) and the
retry behavior driven by tenacity.
"""

from __future__ import annotations

import pytest

from clawmes.lib.http import http_get, http_post


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError("error", request=None, response=self)


class FakeClient:
    """Drop-in for httpx.Client used as a context manager."""

    def __init__(self, responses):
        self._responses = responses
        self._calls = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def request(self, method, url, *, params=None, json=None, headers=None):
        self._calls.append(
            {"method": method, "url": url, "params": params, "json": json, "headers": headers}
        )
        resp = self._responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp


class TestHttpGet:
    def test_get_round_trip(self, monkeypatch):
        """Cover lines 119-120 + the _request body."""
        import httpx

        responses = [FakeResponse({"price": 3500})]
        client = FakeClient(responses)

        def fake_client_factory(*a, **kw):
            return client

        monkeypatch.setattr(httpx, "Client", fake_client_factory)

        result = http_get("https://api.0x.org/quote", params={"sym": "ETH"})
        assert result == {"price": 3500}
        assert client._calls[0]["method"] == "GET"
        assert "User-Agent" in client._calls[0]["headers"]
        assert client._calls[0]["headers"]["Accept"] == "application/json"

    def test_get_with_extra_headers(self, monkeypatch):
        import httpx

        responses = [FakeResponse({"ok": 1})]
        client = FakeClient(responses)
        monkeypatch.setattr(httpx, "Client", lambda *a, **kw: client)

        http_get("https://api.0x.org/quote", headers={"x-api-key": "abc"})
        # Custom headers merged in
        assert client._calls[0]["headers"]["x-api-key"] == "abc"


class TestHttpPost:
    def test_post_round_trip(self, monkeypatch):
        """Cover lines 132-133."""
        import httpx

        responses = [FakeResponse({"ok": True})]
        client = FakeClient(responses)
        monkeypatch.setattr(httpx, "Client", lambda *a, **kw: client)

        result = http_post("https://api.0x.org/quote", json={"a": 1})
        assert result == {"ok": True}
        assert client._calls[0]["method"] == "POST"
        assert client._calls[0]["json"] == {"a": 1}

    def test_post_disallowed_host(self):
        # Allowlist guard fires before the transport is even reached
        from clawmes.lib.http import NetworkAllowlistError

        with pytest.raises(NetworkAllowlistError):
            http_post("https://malicious.example.com/api", json={})


class TestRetry:
    def test_retries_on_transport_error(self, monkeypatch):
        import httpx

        # Two transport errors followed by a success
        responses = [
            httpx.ConnectError("boom"),
            httpx.ReadTimeout("again"),
            FakeResponse({"finally": True}),
        ]
        client = FakeClient(responses)
        monkeypatch.setattr(httpx, "Client", lambda *a, **kw: client)

        result = http_get("https://api.0x.org/quote")
        assert result == {"finally": True}
        assert len(client._calls) == 3

    def test_retries_exhausted(self, monkeypatch):
        import httpx

        # Three transport errors → all retries exhausted → raises
        responses = [httpx.ConnectError("err") for _ in range(3)]
        client = FakeClient(responses)
        monkeypatch.setattr(httpx, "Client", lambda *a, **kw: client)

        with pytest.raises(httpx.ConnectError):
            http_get("https://api.0x.org/quote")

    def test_does_not_retry_on_value_error(self, monkeypatch):
        """Non-transport, non-status errors are not retried."""
        import httpx

        responses = [ValueError("not a transport error")]
        client = FakeClient(responses)
        monkeypatch.setattr(httpx, "Client", lambda *a, **kw: client)

        with pytest.raises(ValueError):
            http_get("https://api.0x.org/quote")
        # No retry — only one attempt
        assert len(client._calls) == 1
