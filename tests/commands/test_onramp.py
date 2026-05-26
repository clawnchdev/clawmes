"""Tests for the /onramp slash command."""

from __future__ import annotations

import pytest

from clawmes.commands import onramp as onramp_mod
from clawmes.wallet.state import WalletState


@pytest.fixture
def fake_wallet(monkeypatch):
    state: dict = {"connected": False, "address": "0x" + "1" * 40}

    def _state():
        if state["connected"]:
            return WalletState.for_chain(mode="local", address=state["address"], chain_id=8453)
        return WalletState.disconnected()

    monkeypatch.setattr(onramp_mod, "get_wallet_state", _state)
    return state


# ── parse_amount ───────────────────────────────────────────────────


class TestParseAmount:
    def test_empty(self):
        assert onramp_mod._parse_amount("") is None
        assert onramp_mod._parse_amount("   ") is None

    def test_valid(self):
        assert onramp_mod._parse_amount("25") == 25.0
        assert onramp_mod._parse_amount("100.50") == 100.50

    def test_invalid_string(self):
        out = onramp_mod._parse_amount("garbage")
        assert isinstance(out, str)
        assert "Invalid amount" in out

    def test_negative(self):
        out = onramp_mod._parse_amount("-5")
        assert isinstance(out, str)
        assert "Must be positive" in out

    def test_zero(self):
        out = onramp_mod._parse_amount("0")
        assert isinstance(out, str)
        assert "Must be positive" in out


# ── _build_onramp_url ──────────────────────────────────────────────


class TestBuildUrl:
    def test_no_app_id_returns_fallback(self, monkeypatch):
        monkeypatch.delenv("CLAWMES_COINBASE_ONRAMP_APP_ID", raising=False)
        url = onramp_mod._build_onramp_url(address="0xabc", amount="25")
        assert url == onramp_mod._FALLBACK_LANDING

    def test_with_app_id_includes_params(self, monkeypatch):
        monkeypatch.setenv("CLAWMES_COINBASE_ONRAMP_APP_ID", "my-app-id")
        url = onramp_mod._build_onramp_url(address="0xabc", amount="50")
        assert url.startswith(onramp_mod._ONRAMP_BASE_URL)
        assert "appId=my-app-id" in url
        assert "0xabc" in url
        assert "50" in url
        assert "base" in url


# ── handle_onramp ──────────────────────────────────────────────────


class TestHandleOnramp:
    async def test_invalid_amount(self, fake_wallet):
        out = await onramp_mod.handle_onramp("garbage")
        assert "Invalid amount" in out

    async def test_no_wallet_shows_fallback(self, fake_wallet):
        fake_wallet["connected"] = False
        out = await onramp_mod.handle_onramp("25")
        assert "No wallet connected" in out
        assert onramp_mod._FALLBACK_LANDING in out

    async def test_wallet_connected_no_app_id(self, fake_wallet, monkeypatch):
        fake_wallet["connected"] = True
        monkeypatch.delenv("CLAWMES_COINBASE_ONRAMP_APP_ID", raising=False)
        out = await onramp_mod.handle_onramp("50")
        assert "Coinbase Onramp link" in out
        assert "50" in out
        # Fallback URL because APP_ID isn't configured
        assert onramp_mod._FALLBACK_LANDING in out
        assert "CLAWMES_COINBASE_ONRAMP_APP_ID" in out

    async def test_wallet_connected_with_app_id(self, fake_wallet, monkeypatch):
        fake_wallet["connected"] = True
        monkeypatch.setenv("CLAWMES_COINBASE_ONRAMP_APP_ID", "test-app")
        out = await onramp_mod.handle_onramp("100")
        assert onramp_mod._ONRAMP_BASE_URL in out
        # No warning about missing APP_ID since it's configured
        assert "not configured" not in out

    async def test_default_amount_from_env(self, fake_wallet, monkeypatch):
        fake_wallet["connected"] = True
        monkeypatch.setenv("CLAWMES_COINBASE_ONRAMP_APP_ID", "test-app")
        monkeypatch.setenv("CLAWMES_COINBASE_ONRAMP_DEFAULT_AMOUNT", "75")
        out = await onramp_mod.handle_onramp("")
        assert "75" in out

    async def test_default_amount_fallback(self, fake_wallet, monkeypatch):
        fake_wallet["connected"] = True
        monkeypatch.setenv("CLAWMES_COINBASE_ONRAMP_APP_ID", "test-app")
        monkeypatch.delenv("CLAWMES_COINBASE_ONRAMP_DEFAULT_AMOUNT", raising=False)
        out = await onramp_mod.handle_onramp("")
        assert onramp_mod._DEFAULT_AMOUNT_USD in out


class TestRecordingBestEffort:
    async def test_recording_failure(self, monkeypatch, fake_wallet):
        from clawmes.services import command_history as ch_mod

        def _boom(*a, **kw):
            raise RuntimeError("history broken")

        monkeypatch.setattr(ch_mod, "record_command_call", _boom)
        out = await onramp_mod.handle_onramp("")
        assert isinstance(out, str)


class TestRegister:
    def test_registers(self):
        captured = []

        class FakeCtx:
            def register_command(self, **kw):
                captured.append(kw["name"])

        onramp_mod.register(FakeCtx())
        assert captured == ["onramp"]
