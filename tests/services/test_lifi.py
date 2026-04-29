"""Tests for clawmes.services.lifi."""

from __future__ import annotations

import pytest

from clawmes.services import lifi as lifi_module
from clawmes.services.lifi import LifiError, LifiService, get_lifi_service


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(lifi_module, "_instance", None)
    monkeypatch.delenv("LIFI_API_KEY", raising=False)


@pytest.fixture
def fake_http(monkeypatch):
    class FakeHttp:
        def __init__(self):
            self.calls: list[dict] = []
            self.responses: list = []

        def __call__(self, url, *, params=None, headers=None, timeout=30.0, **kw):
            self.calls.append({"url": url, "params": params, "headers": headers})
            if not self.responses:
                raise AssertionError("no fake response queued")
            response = self.responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response

    fake = FakeHttp()
    monkeypatch.setattr(lifi_module, "http_get", fake)
    return fake


@pytest.fixture
def svc():
    s = LifiService()
    s.start()
    return s


class TestStartStop:
    def test_no_key(self, svc):
        assert svc._api_key is None

    def test_with_key(self, monkeypatch):
        monkeypatch.setenv("LIFI_API_KEY", "lifi-test")
        s = LifiService()
        s.start()
        assert s._api_key == "lifi-test"

    def test_stop_clears_key(self, monkeypatch):
        monkeypatch.setenv("LIFI_API_KEY", "k")
        s = LifiService()
        s.start()
        s.stop()
        assert s._api_key is None


class TestGetQuote:
    def test_basic(self, svc, fake_http):
        fake_http.responses.append(
            {"id": "abc", "tool": "stargate", "estimate": {"toAmount": "1000"}}
        )
        result = svc.get_quote(
            from_chain=1,
            to_chain=8453,
            from_token="0x" + "a" * 40,
            to_token="0x" + "b" * 40,
            from_amount=10**18,
            from_address="0x" + "c" * 40,
        )
        assert result["tool"] == "stargate"
        params = fake_http.calls[0]["params"]
        assert params["fromChain"] == "1"
        assert params["toChain"] == "8453"
        assert params["fromAddress"] == "0x" + "c" * 40
        assert params["slippage"] == "0.005"

    def test_with_to_address(self, svc, fake_http):
        fake_http.responses.append({"id": "x"})
        svc.get_quote(
            from_chain=1,
            to_chain=8453,
            from_token="0x" + "a" * 40,
            to_token="0x" + "b" * 40,
            from_amount=1,
            from_address="0x" + "c" * 40,
            to_address="0x" + "d" * 40,
        )
        params = fake_http.calls[0]["params"]
        assert params["toAddress"] == "0x" + "d" * 40

    def test_api_key_in_headers(self, monkeypatch, fake_http):
        monkeypatch.setenv("LIFI_API_KEY", "secret-lifi")
        s = LifiService()
        s.start()
        fake_http.responses.append({"id": "x"})
        s.get_quote(
            from_chain=1,
            to_chain=8453,
            from_token="0x",
            to_token="0x",
            from_amount=1,
            from_address="0x",
        )
        headers = fake_http.calls[0]["headers"]
        assert headers["x-lifi-api-key"] == "secret-lifi"

    def test_no_api_key_no_header(self, svc, fake_http):
        fake_http.responses.append({"id": "x"})
        svc.get_quote(
            from_chain=1,
            to_chain=8453,
            from_token="0x",
            to_token="0x",
            from_amount=1,
            from_address="0x",
        )
        headers = fake_http.calls[0]["headers"]
        assert "x-lifi-api-key" not in headers


class TestGetStatus:
    def test_basic(self, svc, fake_http):
        fake_http.responses.append({"status": "DONE", "substatus": "COMPLETED"})
        result = svc.get_status(tx_hash="0xabc")
        assert result["status"] == "DONE"
        assert fake_http.calls[0]["params"]["txHash"] == "0xabc"

    def test_with_bridge_hint(self, svc, fake_http):
        fake_http.responses.append({"status": "PENDING"})
        svc.get_status(tx_hash="0xabc", bridge="across")
        assert fake_http.calls[0]["params"]["bridge"] == "across"


class TestGetConnections:
    def test_no_filter(self, svc, fake_http):
        fake_http.responses.append({"connections": []})
        svc.get_connections()
        # No params
        assert fake_http.calls[0]["params"] == {}

    def test_filter_from_to(self, svc, fake_http):
        fake_http.responses.append({"connections": []})
        svc.get_connections(from_chain=1, to_chain=8453)
        params = fake_http.calls[0]["params"]
        assert params["fromChain"] == "1"
        assert params["toChain"] == "8453"


class TestErrorClassification:
    def test_rate_limit(self, svc, fake_http):
        fake_http.responses.append(RuntimeError("HTTP 429"))
        with pytest.raises(LifiError) as exc_info:
            svc.get_status(tx_hash="0xabc")
        assert exc_info.value.code == "rate_limited"

    def test_generic_failure(self, svc, fake_http):
        fake_http.responses.append(RuntimeError("connection reset"))
        with pytest.raises(LifiError) as exc_info:
            svc.get_status(tx_hash="0xabc")
        assert exc_info.value.code == "api_error"

    def test_no_route_envelope(self, svc, fake_http):
        fake_http.responses.append(
            {"message": "No route available", "code": 1006, "type": "RoutesError"}
        )
        with pytest.raises(LifiError) as exc_info:
            svc.get_quote(
                from_chain=1,
                to_chain=8453,
                from_token="0x",
                to_token="0x",
                from_amount=1,
                from_address="0x",
            )
        assert exc_info.value.code == "no_route"

    def test_unsupported_envelope(self, svc, fake_http):
        fake_http.responses.append(
            {"message": "Chain not supported", "code": 1001, "type": "ValidationError"}
        )
        with pytest.raises(LifiError) as exc_info:
            svc.get_quote(
                from_chain=999,
                to_chain=8453,
                from_token="0x",
                to_token="0x",
                from_amount=1,
                from_address="0x",
            )
        assert exc_info.value.code == "unsupported"

    def test_generic_envelope(self, svc, fake_http):
        fake_http.responses.append(
            {"message": "Internal server error", "code": 500, "type": "ServerError"}
        )
        with pytest.raises(LifiError) as exc_info:
            svc.get_status(tx_hash="0xabc")
        assert exc_info.value.code == "api_error"

    def test_non_dict_response(self, svc, fake_http):
        fake_http.responses.append("not a dict")
        with pytest.raises(LifiError) as exc_info:
            svc.get_status(tx_hash="0xabc")
        assert exc_info.value.code == "api_error"


class TestSingleton:
    def test_returns_same_instance(self):
        a = get_lifi_service()
        b = get_lifi_service()
        assert a is b
