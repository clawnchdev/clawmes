"""Tests for the ``farcaster`` tool."""

from __future__ import annotations

import json

import pytest

from clawmes.tools.farcaster import farcaster


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    from clawmes.policy import storage as policy_storage

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("NEYNAR_API_KEY", "test-key")
    monkeypatch.setenv("NEYNAR_SIGNER_UUID", "test-signer")
    policy_storage.save_policies([])


@pytest.fixture
def fake_get(monkeypatch):
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
    monkeypatch.setattr("clawmes.tools.farcaster.http_get", fake)
    return fake


@pytest.fixture
def fake_post(monkeypatch):
    class FakeHttp:
        def __init__(self):
            self.calls: list[dict] = []
            self.responses: list = []

        def __call__(self, url, *, json=None, headers=None, timeout=30.0, **kw):
            self.calls.append({"url": url, "json": json})
            if not self.responses:
                raise AssertionError("no fake response queued")
            response = self.responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response

    fake = FakeHttp()
    monkeypatch.setattr("clawmes.tools.farcaster.http_post", fake)
    return fake


class TestNoApiKey:
    def test_rejects(self, monkeypatch):
        monkeypatch.delenv("NEYNAR_API_KEY", raising=False)
        out = json.loads(farcaster({"action": "search", "query": "test"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "no_credentials"


class TestCast:
    def test_basic(self, fake_post):
        fake_post.responses.append({"cast": {"hash": "0xabc", "thread_hash": "0xabc"}})
        out = json.loads(farcaster({"action": "cast", "text": "Hello"}))
        assert "isError" not in out
        assert out["details"]["hash"] == "0xabc"

    def test_no_signer(self, monkeypatch, fake_post):
        monkeypatch.delenv("NEYNAR_SIGNER_UUID", raising=False)
        out = json.loads(farcaster({"action": "cast", "text": "Hello"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "no_credentials"

    def test_text_too_long(self, fake_post):
        out = json.loads(farcaster({"action": "cast", "text": "x" * 321}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "param_error"

    def test_with_channel(self, fake_post):
        fake_post.responses.append({"cast": {"hash": "0x1"}})
        farcaster({"action": "cast", "text": "Hi", "channel": "crypto"})
        sent = fake_post.calls[0]["json"]
        assert sent["channel_id"] == "crypto"

    def test_api_error(self, fake_post):
        fake_post.responses.append(RuntimeError("network"))
        out = json.loads(farcaster({"action": "cast", "text": "Hi"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "api_error"

    def test_non_dict_response(self, fake_post):
        fake_post.responses.append("garbage")
        out = json.loads(farcaster({"action": "cast", "text": "Hi"}))
        assert out["isError"] is True


class TestReply:
    def test_basic(self, fake_post):
        fake_post.responses.append({"cast": {"hash": "0xreply"}})
        out = json.loads(
            farcaster(
                {
                    "action": "reply",
                    "text": "Reply",
                    "parent_hash": "0xparent",
                }
            )
        )
        assert "isError" not in out
        sent = fake_post.calls[0]["json"]
        assert sent["parent"] == "0xparent"


class TestSearch:
    def test_basic(self, fake_get):
        fake_get.responses.append({"result": {"casts": [{"hash": "0x1"}, {"hash": "0x2"}]}})
        out = json.loads(farcaster({"action": "search", "query": "ethereum"}))
        assert "isError" not in out
        assert out["details"]["count"] == 2

    def test_api_error(self, fake_get):
        fake_get.responses.append(RuntimeError("rate limit"))
        out = json.loads(farcaster({"action": "search", "query": "x"}))
        assert out["isError"] is True

    def test_non_dict(self, fake_get):
        fake_get.responses.append("garbage")
        out = json.loads(farcaster({"action": "search", "query": "x"}))
        assert out["isError"] is True


class TestFeed:
    def test_basic(self, fake_get):
        fake_get.responses.append({"casts": [{"hash": "0x1"}]})
        out = json.loads(farcaster({"action": "feed", "fid": 12345}))
        assert "isError" not in out
        assert out["details"]["count"] == 1

    def test_no_fid(self, fake_get):
        out = json.loads(farcaster({"action": "feed"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "param_error"

    def test_api_error(self, fake_get):
        fake_get.responses.append(RuntimeError("network"))
        out = json.loads(farcaster({"action": "feed", "fid": 1}))
        assert out["isError"] is True

    def test_non_dict(self, fake_get):
        fake_get.responses.append("garbage")
        out = json.loads(farcaster({"action": "feed", "fid": 1}))
        assert out["isError"] is True


class TestNotifications:
    def test_basic(self, fake_get):
        fake_get.responses.append({"notifications": [{"type": "mention"}]})
        out = json.loads(farcaster({"action": "notifications", "fid": 1}))
        assert "isError" not in out
        assert out["details"]["count"] == 1

    def test_no_fid(self, fake_get):
        out = json.loads(farcaster({"action": "notifications"}))
        assert out["isError"] is True

    def test_api_error(self, fake_get):
        fake_get.responses.append(RuntimeError("rate limit"))
        out = json.loads(farcaster({"action": "notifications", "fid": 1}))
        assert out["isError"] is True

    def test_non_dict(self, fake_get):
        fake_get.responses.append("garbage")
        out = json.loads(farcaster({"action": "notifications", "fid": 1}))
        assert out["isError"] is True


class TestRegister:
    def test_register_calls_ctx(self):
        from clawmes.tools import farcaster as fc_mod

        recorded = []

        class FakeCtx:
            def register_tool(self, **kw):
                recorded.append(kw)

        fc_mod.register(FakeCtx())
        assert len(recorded) == 1
        assert recorded[0]["name"] == "farcaster"
