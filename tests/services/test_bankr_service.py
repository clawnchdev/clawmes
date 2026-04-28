"""Tests for clawmes.services.bankr_service."""

from __future__ import annotations

from typing import Any

import pytest

from clawmes.services import bankr_service as bs_mod
from clawmes.services.bankr_service import (
    BankrError,
    BankrService,
    get_bankr_service,
)


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(bs_mod, "_instance", None)


@pytest.fixture
def fake_http(monkeypatch):
    """Replace http_get / http_post with recorders."""

    class FakeHttp:
        def __init__(self):
            self.gets: list[dict] = []
            self.posts: list[dict] = []
            self.get_response: Any = None
            self.post_response: Any = None
            self.get_exception: Exception | None = None
            self.post_exception: Exception | None = None

        def get(self, url, *, headers=None, timeout=30.0, **kw):
            self.gets.append({"url": url, "headers": headers})
            if self.get_exception is not None:
                raise self.get_exception
            return self.get_response

        def post(self, url, *, json=None, headers=None, timeout=30.0, **kw):
            self.posts.append({"url": url, "json": json, "headers": headers})
            if self.post_exception is not None:
                raise self.post_exception
            return self.post_response

    fake = FakeHttp()
    monkeypatch.setattr(bs_mod, "http_get", fake.get)
    monkeypatch.setattr(bs_mod, "http_post", fake.post)
    return fake


# --- Lifecycle ------------------------------------------------------------


class TestLifecycle:
    def test_start_no_key(self, monkeypatch):
        monkeypatch.delenv("BANKR_API_KEY", raising=False)
        svc = BankrService()
        svc.start()
        assert svc.has_credentials is False

    def test_start_with_key(self, monkeypatch):
        monkeypatch.setenv("BANKR_API_KEY", "test-key")
        svc = BankrService()
        svc.start()
        assert svc.has_credentials is True

    def test_stop_clears(self, monkeypatch):
        monkeypatch.setenv("BANKR_API_KEY", "test-key")
        svc = BankrService()
        svc.start()
        svc.stop()
        assert svc.has_credentials is False


# --- Auth -----------------------------------------------------------------


class TestAuth:
    def test_no_key_raises_no_credentials(self, monkeypatch):
        monkeypatch.delenv("BANKR_API_KEY", raising=False)
        svc = BankrService()
        svc.start()
        with pytest.raises(BankrError, match="BANKR_API_KEY not set"):
            svc.get_account()

    def test_post_no_key_raises_no_credentials(self, monkeypatch):
        # Cover the _post BankrError re-raise branch
        monkeypatch.delenv("BANKR_API_KEY", raising=False)
        svc = BankrService()
        svc.start()
        with pytest.raises(BankrError, match="BANKR_API_KEY not set"):
            svc.send_transaction(chain_id=1, to="0x", value=0)

    def test_bearer_header_sent(self, fake_http, monkeypatch):
        monkeypatch.setenv("BANKR_API_KEY", "abc-123")
        svc = BankrService()
        svc.start()
        fake_http.get_response = {"user_id": "u1"}
        svc.get_account()
        headers = fake_http.gets[0]["headers"]
        assert headers["Authorization"] == "Bearer abc-123"


# --- Methods --------------------------------------------------------------


class TestGetAccount:
    def test_basic(self, fake_http, monkeypatch):
        monkeypatch.setenv("BANKR_API_KEY", "k")
        svc = BankrService()
        svc.start()
        fake_http.get_response = {
            "user_id": "u1",
            "tier": "pro",
            "addresses": {"8453": "0xabc"},
        }
        result = svc.get_account()
        assert result["user_id"] == "u1"
        assert fake_http.gets[0]["url"].endswith("/v1/account")

    def test_network_failure_wraps(self, fake_http, monkeypatch):
        monkeypatch.setenv("BANKR_API_KEY", "k")
        svc = BankrService()
        svc.start()
        fake_http.get_exception = RuntimeError("relay down")
        with pytest.raises(BankrError, match="failed:"):
            svc.get_account()


class TestSendTransaction:
    def test_basic(self, fake_http, monkeypatch):
        monkeypatch.setenv("BANKR_API_KEY", "k")
        svc = BankrService()
        svc.start()
        fake_http.post_response = {"tx_hash": "0xabc"}
        result = svc.send_transaction(chain_id=8453, to="0x" + "1" * 40, value=10**18)
        assert result == "0xabc"
        body = fake_http.posts[0]["json"]
        assert body["chain_id"] == 8453
        assert body["value"] == hex(10**18)

    def test_with_data_bytes(self, fake_http, monkeypatch):
        monkeypatch.setenv("BANKR_API_KEY", "k")
        svc = BankrService()
        svc.start()
        fake_http.post_response = {"tx_hash": "0xabc"}
        svc.send_transaction(chain_id=8453, to="0x", value=0, data=b"\xab\xcd")
        body = fake_http.posts[0]["json"]
        assert body["data"] == "0xabcd"

    def test_with_data_string(self, fake_http, monkeypatch):
        monkeypatch.setenv("BANKR_API_KEY", "k")
        svc = BankrService()
        svc.start()
        fake_http.post_response = {"tx_hash": "0xabc"}
        svc.send_transaction(chain_id=1, to="0x", value=0, data="0xdeadbeef")
        body = fake_http.posts[0]["json"]
        assert body["data"] == "0xdeadbeef"

    def test_with_gas(self, fake_http, monkeypatch):
        monkeypatch.setenv("BANKR_API_KEY", "k")
        svc = BankrService()
        svc.start()
        fake_http.post_response = {"tx_hash": "0xabc"}
        svc.send_transaction(chain_id=1, to="0x", value=0, gas=50000)
        body = fake_http.posts[0]["json"]
        assert body["gas"] == hex(50000)

    def test_missing_tx_hash_in_response(self, fake_http, monkeypatch):
        monkeypatch.setenv("BANKR_API_KEY", "k")
        svc = BankrService()
        svc.start()
        fake_http.post_response = {"some_other_field": "x"}
        with pytest.raises(BankrError, match="did not return|returned no signature"):
            svc.send_transaction(chain_id=1, to="0x", value=0)

    def test_network_failure_wraps(self, fake_http, monkeypatch):
        monkeypatch.setenv("BANKR_API_KEY", "k")
        svc = BankrService()
        svc.start()
        fake_http.post_exception = RuntimeError("network")
        with pytest.raises(BankrError, match="failed:"):
            svc.send_transaction(chain_id=1, to="0x", value=0)


class TestSigning:
    def test_sign_typed_data(self, fake_http, monkeypatch):
        monkeypatch.setenv("BANKR_API_KEY", "k")
        svc = BankrService()
        svc.start()
        fake_http.post_response = {"signature": "0xsig"}
        result = svc.sign_typed_data_v4({"types": {}}, chain_id=8453)
        assert result == "0xsig"

    def test_sign_typed_data_missing_signature(self, fake_http, monkeypatch):
        monkeypatch.setenv("BANKR_API_KEY", "k")
        svc = BankrService()
        svc.start()
        fake_http.post_response = {"x": "y"}
        with pytest.raises(BankrError, match="did not return|returned no signature"):
            svc.sign_typed_data_v4({"types": {}})

    def test_sign_personal_string(self, fake_http, monkeypatch):
        monkeypatch.setenv("BANKR_API_KEY", "k")
        svc = BankrService()
        svc.start()
        fake_http.post_response = {"signature": "0xsig"}
        sig = svc.sign_personal_message("hi", chain_id=1)
        assert sig == "0xsig"
        # String passed through unchanged
        assert fake_http.posts[0]["json"]["message"] == "hi"

    def test_sign_personal_bytes(self, fake_http, monkeypatch):
        monkeypatch.setenv("BANKR_API_KEY", "k")
        svc = BankrService()
        svc.start()
        fake_http.post_response = {"signature": "0xsig"}
        svc.sign_personal_message(b"\xab\xcd", chain_id=1)
        # Bytes are hex-encoded with 0x prefix
        assert fake_http.posts[0]["json"]["message"] == "0xabcd"

    def test_sign_personal_missing_signature(self, fake_http, monkeypatch):
        monkeypatch.setenv("BANKR_API_KEY", "k")
        svc = BankrService()
        svc.start()
        fake_http.post_response = {}
        with pytest.raises(BankrError, match="did not return|returned no signature"):
            svc.sign_personal_message("hi")


# --- Singleton ------------------------------------------------------------


class TestSingleton:
    def test_get_returns_same_instance(self):
        a = get_bankr_service()
        b = get_bankr_service()
        assert a is b


# --- BankrError -----------------------------------------------------------


class TestBankrError:
    def test_carries_status(self):
        err = BankrError("auth", "bad", status=401)
        assert err.code == "auth"
        assert err.message == "bad"
        assert err.status == 401
