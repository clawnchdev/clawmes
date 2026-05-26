"""Tests for clawmes.services.base_account."""

from __future__ import annotations

import pytest

from clawmes.services import base_account as ba_mod
from clawmes.services.base_account import BaseAccountError, BaseAccountService


@pytest.fixture
def svc(monkeypatch):
    # Fresh singleton per test
    monkeypatch.setattr(ba_mod, "_instance", None)
    monkeypatch.delenv("CLAWMES_BASE_ACCOUNT_CLIENT_ID", raising=False)
    monkeypatch.delenv("CLAWMES_BASE_ACCOUNT_AUTH_URL", raising=False)
    monkeypatch.delenv("CLAWMES_BASE_ACCOUNT_TOKEN_URL", raising=False)
    monkeypatch.delenv("CLAWMES_BASE_ACCOUNT_API_URL", raising=False)
    s = BaseAccountService()
    s.start()
    return s


@pytest.fixture
def svc_configured(monkeypatch):
    monkeypatch.setattr(ba_mod, "_instance", None)
    monkeypatch.setenv("CLAWMES_BASE_ACCOUNT_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("CLAWMES_BASE_ACCOUNT_AUTH_URL", "https://auth.test/authorize")
    monkeypatch.setenv("CLAWMES_BASE_ACCOUNT_TOKEN_URL", "https://auth.test/token")
    monkeypatch.setenv("CLAWMES_BASE_ACCOUNT_API_URL", "https://api.test")
    s = BaseAccountService()
    s.start()
    return s


# ── lifecycle ─────────────────────────────────────────────────────


class TestLifecycle:
    def test_start_without_client_id(self, svc):
        # No client id is fine; just means connect will error later
        assert svc._client_id is None

    def test_start_with_env_overrides(self, svc_configured):
        assert svc_configured._client_id == "test-client-id"
        assert svc_configured._auth_url == "https://auth.test/authorize"

    def test_stop_clears_tokens(self, svc_configured):
        svc_configured._access_token = "tok"
        svc_configured._user_address = "0xabc"
        svc_configured.stop()
        assert svc_configured._access_token is None
        assert svc_configured._user_address is None


# ── auth URL ──────────────────────────────────────────────────────


class TestGetAuthUrl:
    def test_not_configured(self, svc):
        with pytest.raises(BaseAccountError) as exc_info:
            svc.get_auth_url()
        assert exc_info.value.code == "not_configured"

    def test_basic(self, svc_configured):
        url = svc_configured.get_auth_url()
        assert "https://auth.test/authorize" in url
        assert "client_id=test-client-id" in url
        assert "response_type=code" in url

    def test_with_state(self, svc_configured):
        url = svc_configured.get_auth_url(state="abc123")
        assert "state=abc123" in url


# ── exchange_code ─────────────────────────────────────────────────


class TestExchangeCode:
    def test_not_configured(self, svc):
        with pytest.raises(BaseAccountError) as exc_info:
            svc.exchange_code("abc")
        assert exc_info.value.code == "not_configured"

    def test_empty_code(self, svc_configured):
        with pytest.raises(BaseAccountError) as exc_info:
            svc_configured.exchange_code("")
        assert exc_info.value.code == "oauth_error"

    def test_http_failure(self, svc_configured, monkeypatch):
        def _boom(*args, **kwargs):
            raise RuntimeError("network down")

        monkeypatch.setattr(ba_mod, "http_post", _boom)
        with pytest.raises(BaseAccountError) as exc_info:
            svc_configured.exchange_code("code")
        assert exc_info.value.code == "oauth_error"

    def test_no_access_token_in_response(self, svc_configured, monkeypatch):
        monkeypatch.setattr(ba_mod, "http_post", lambda *a, **k: {"error": "invalid"})
        with pytest.raises(BaseAccountError) as exc_info:
            svc_configured.exchange_code("code")
        assert exc_info.value.code == "oauth_error"

    def test_non_dict_response(self, svc_configured, monkeypatch):
        monkeypatch.setattr(ba_mod, "http_post", lambda *a, **k: ["not", "a", "dict"])
        with pytest.raises(BaseAccountError) as exc_info:
            svc_configured.exchange_code("code")
        assert exc_info.value.code == "oauth_error"

    def test_happy_path_stores_tokens(self, svc_configured, monkeypatch):
        monkeypatch.setattr(
            ba_mod,
            "http_post",
            lambda *a, **k: {
                "access_token": "acc",
                "refresh_token": "ref",
                "expires_in": 3600,
                "address": "0x" + "1" * 40,
            },
        )
        out = svc_configured.exchange_code("code")
        assert out["access_token"] == "acc"
        assert svc_configured._access_token == "acc"
        assert svc_configured._refresh_token == "ref"
        assert svc_configured._user_address == "0x" + "1" * 40
        assert svc_configured._token_expires_at is not None

    def test_happy_path_without_refresh_token(self, svc_configured, monkeypatch):
        monkeypatch.setattr(
            ba_mod,
            "http_post",
            lambda *a, **k: {"access_token": "acc"},
        )
        svc_configured.exchange_code("code")
        assert svc_configured._refresh_token is None
        assert svc_configured._token_expires_at is None


# ── get_user_address ──────────────────────────────────────────────


class TestGetUserAddress:
    def test_cached(self, svc_configured):
        svc_configured._access_token = "tok"
        svc_configured._user_address = "0xabc"
        assert svc_configured.get_user_address() == "0xabc"

    def test_no_token(self, svc_configured):
        with pytest.raises(BaseAccountError) as exc_info:
            svc_configured.get_user_address()
        assert exc_info.value.code == "not_connected"

    def test_fetches_from_api(self, svc_configured, monkeypatch):
        svc_configured._access_token = "tok"
        monkeypatch.setattr(
            ba_mod,
            "http_get",
            lambda *a, **k: {"address": "0xdef"},
        )
        assert svc_configured.get_user_address() == "0xdef"
        # Cached on subsequent calls
        assert svc_configured._user_address == "0xdef"

    def test_primary_address_field(self, svc_configured, monkeypatch):
        svc_configured._access_token = "tok"
        monkeypatch.setattr(
            ba_mod,
            "http_get",
            lambda *a, **k: {"primary_address": "0xpri"},
        )
        assert svc_configured.get_user_address() == "0xpri"

    def test_api_error(self, svc_configured, monkeypatch):
        svc_configured._access_token = "tok"

        def _boom(*a, **k):
            raise RuntimeError("503")

        monkeypatch.setattr(ba_mod, "http_get", _boom)
        with pytest.raises(BaseAccountError) as exc_info:
            svc_configured.get_user_address()
        assert exc_info.value.code == "api_error"

    def test_no_address_field(self, svc_configured, monkeypatch):
        svc_configured._access_token = "tok"
        monkeypatch.setattr(ba_mod, "http_get", lambda *a, **k: {})
        with pytest.raises(BaseAccountError) as exc_info:
            svc_configured.get_user_address()
        assert exc_info.value.code == "api_error"


# ── submit_request + poll_request ────────────────────────────────


class TestSubmitRequest:
    def test_not_connected(self, svc_configured):
        with pytest.raises(BaseAccountError) as exc_info:
            svc_configured.submit_request(method="eth_sendTransaction", params=[])
        assert exc_info.value.code == "not_connected"

    def test_http_failure(self, svc_configured, monkeypatch):
        svc_configured._access_token = "tok"

        def _boom(*a, **k):
            raise RuntimeError("network")

        monkeypatch.setattr(ba_mod, "http_post", _boom)
        with pytest.raises(BaseAccountError) as exc_info:
            svc_configured.submit_request(method="m", params=[])
        assert exc_info.value.code == "request_failed"

    def test_bad_response_shape(self, svc_configured, monkeypatch):
        svc_configured._access_token = "tok"
        monkeypatch.setattr(ba_mod, "http_post", lambda *a, **k: {"no_request_id": True})
        with pytest.raises(BaseAccountError) as exc_info:
            svc_configured.submit_request(method="m", params=[])
        assert exc_info.value.code == "request_failed"

    def test_happy_path(self, svc_configured, monkeypatch):
        svc_configured._access_token = "tok"
        monkeypatch.setattr(
            ba_mod,
            "http_post",
            lambda *a, **k: {
                "request_id": "req-123",
                "approval_url": "https://base.app/approve/req-123",
            },
        )
        out = svc_configured.submit_request(method="eth_sendTransaction", params=[{}])
        assert out["request_id"] == "req-123"
        assert "approval_url" in out


class TestPollRequest:
    def test_confirmed(self, svc_configured, monkeypatch):
        svc_configured._access_token = "tok"
        monkeypatch.setattr(
            ba_mod,
            "http_get",
            lambda *a, **k: {"status": "confirmed", "result": "0xtx"},
        )
        out = svc_configured.poll_request("req-1", timeout=1.0, interval=0.01)
        assert out["status"] == "confirmed"

    def test_rejected(self, svc_configured, monkeypatch):
        svc_configured._access_token = "tok"
        monkeypatch.setattr(
            ba_mod,
            "http_get",
            lambda *a, **k: {"status": "rejected"},
        )
        with pytest.raises(BaseAccountError) as exc_info:
            svc_configured.poll_request("req-1", timeout=1.0, interval=0.01)
        assert exc_info.value.code == "request_failed"

    def test_api_error(self, svc_configured, monkeypatch):
        svc_configured._access_token = "tok"

        def _boom(*a, **k):
            raise RuntimeError("503")

        monkeypatch.setattr(ba_mod, "http_get", _boom)
        with pytest.raises(BaseAccountError) as exc_info:
            svc_configured.poll_request("req-1", timeout=1.0, interval=0.01)
        assert exc_info.value.code == "api_error"

    def test_timeout(self, svc_configured, monkeypatch):
        svc_configured._access_token = "tok"
        # Status keeps returning "pending" — should hit timeout
        monkeypatch.setattr(ba_mod, "http_get", lambda *a, **k: {"status": "pending"})
        with pytest.raises(BaseAccountError) as exc_info:
            svc_configured.poll_request("req-1", timeout=0.05, interval=0.01)
        assert exc_info.value.code == "approval_timeout"


# ── is_connected / _require_token ────────────────────────────────


class TestConnectionHelpers:
    def test_is_connected_false(self, svc):
        assert not svc.is_connected()

    def test_is_connected_true(self, svc_configured):
        svc_configured._access_token = "tok"
        assert svc_configured.is_connected()


class TestSingleton:
    def test_get_returns_singleton(self, monkeypatch):
        monkeypatch.setattr(ba_mod, "_instance", None)
        first = ba_mod.get_base_account_service()
        second = ba_mod.get_base_account_service()
        assert first is second
