"""Tests for the /burn slash command."""

from __future__ import annotations

import pytest

from clawmes.commands import burn as burn_mod
from clawmes.wallet.state import WalletState


@pytest.fixture(autouse=True)
def _isolate():
    burn_mod._burn_state.clear()
    yield
    burn_mod._burn_state.clear()


@pytest.fixture
def fake_wallet(monkeypatch):
    state: dict = {"connected": False, "mode": None}

    def _state():
        if state["connected"]:
            return WalletState.for_chain(mode="local", address="0x" + "1" * 40, chain_id=8453)
        return WalletState.disconnected()

    class _FakeMode:
        def __init__(self):
            self.calls: list[dict] = []
            self.next_hash = "0xburntx"
            self.raise_exc: Exception | None = None

        def send_transaction(self, **kwargs):
            self.calls.append(kwargs)
            if self.raise_exc:
                raise self.raise_exc
            return self.next_hash

    class _SvcStub:
        def __init__(self, mode):
            self._mode = mode

        def active_mode(self):
            return self._mode

    state["mode"] = _FakeMode()
    svc_stub = _SvcStub(state["mode"])

    import clawmes.services.wallet as ws_mod

    monkeypatch.setattr(ws_mod, "get_wallet_state", _state)
    monkeypatch.setattr(ws_mod, "get_wallet_service", lambda: svc_stub)
    return state


@pytest.fixture
def fake_clawnch_burn_config(monkeypatch):
    class _Svc:
        def get_burn_config(self):
            return {
                "token_address": "0x" + "c" * 40,
                "burn_address": "0x" + "d" * 40,
                "min_burn_tokens": 1_000_000,
            }

    import clawmes.services.clawnch as cl_mod

    monkeypatch.setattr(cl_mod, "get_clawnch_service", lambda: _Svc())
    return _Svc


# ── parsing ─────────────────────────────────────────────────────────


class TestParseAmount:
    def test_valid(self):
        assert burn_mod._parse_amount("1000000") == 1_000_000

    def test_with_underscore(self):
        assert burn_mod._parse_amount("1_000_000") == 1_000_000

    def test_with_comma(self):
        assert burn_mod._parse_amount("1,000,000") == 1_000_000

    def test_invalid(self):
        out = burn_mod._parse_amount("notanumber")
        assert isinstance(out, str)
        assert "Invalid amount" in out

    def test_too_low(self):
        out = burn_mod._parse_amount("500000")
        assert isinstance(out, str)
        assert "too low" in out

    def test_too_high(self):
        out = burn_mod._parse_amount("11000000")
        assert isinstance(out, str)
        assert "above cap" in out


# ── usage / last ────────────────────────────────────────────────────


class TestUsage:
    async def test_empty_shows_help(self):
        out = await burn_mod.handle_burn("")
        assert "Burn $CLAWNCH" in out
        assert "1M CLAWNCH" in out

    async def test_empty_with_prior_burn_shows_it(self):
        burn_mod._remember_burn("alice", "0xprior", 1_000_000)
        out = await burn_mod.handle_burn("", sender_id="alice")
        assert "0xprior" in out
        assert "1,000,000" in out

    async def test_last_no_prior(self):
        out = await burn_mod.handle_burn("last", sender_id="alice")
        assert "No burn recorded" in out

    async def test_last_with_prior(self):
        burn_mod._remember_burn("alice", "0xprior", 2_000_000)
        out = await burn_mod.handle_burn("last", sender_id="alice")
        assert "0xprior" in out
        assert "2,000,000" in out
        assert "basescan.org/tx/0xprior" in out

    async def test_default_sender(self):
        out = await burn_mod.handle_burn("")
        assert isinstance(out, str)


# ── submit path ─────────────────────────────────────────────────────


class TestSubmit:
    async def test_no_wallet(self, fake_wallet, fake_clawnch_burn_config):
        fake_wallet["connected"] = False
        out = await burn_mod.handle_burn("1000000", sender_id="alice")
        assert "No wallet connected" in out

    async def test_no_active_mode(self, monkeypatch, fake_clawnch_burn_config):
        from clawmes.wallet.state import WalletState

        def _state():
            return WalletState.for_chain(mode="local", address="0x" + "1" * 40, chain_id=8453)

        class _Svc:
            def active_mode(self):
                return None

        import clawmes.services.wallet as ws_mod

        monkeypatch.setattr(ws_mod, "get_wallet_state", _state)
        monkeypatch.setattr(ws_mod, "get_wallet_service", lambda: _Svc())
        out = await burn_mod.handle_burn("1000000", sender_id="alice")
        assert "No active wallet mode" in out

    async def test_submission_failure(self, fake_wallet, fake_clawnch_burn_config):
        fake_wallet["connected"] = True
        fake_wallet["mode"].raise_exc = RuntimeError("rpc down")
        out = await burn_mod.handle_burn("1000000", sender_id="alice")
        assert "Burn tx submission failed" in out
        assert "rpc down" in out

    async def test_success(self, fake_wallet, fake_clawnch_burn_config):
        fake_wallet["connected"] = True
        out = await burn_mod.handle_burn("1000000", sender_id="alice")
        assert "Burn submitted" in out
        assert "0xburntx" in out
        # State persists for /burn last
        last = burn_mod._recall_burn("alice")
        assert last == {"tx_hash": "0xburntx", "amount": 1_000_000}
        # send_transaction shape
        call = fake_wallet["mode"].calls[0]
        assert call["to"] == "0x" + "c" * 40
        assert call["value"] == 0
        assert call["chain_id"] == 8453

    async def test_invalid_amount(self, fake_clawnch_burn_config):
        out = await burn_mod.handle_burn("notanumber", sender_id="alice")
        assert "Invalid amount" in out

    async def test_too_low(self, fake_clawnch_burn_config):
        out = await burn_mod.handle_burn("500000", sender_id="alice")
        assert "too low" in out

    async def test_too_high(self, fake_clawnch_burn_config):
        out = await burn_mod.handle_burn("11000000", sender_id="alice")
        assert "above cap" in out


# ── command_history best-effort ────────────────────────────────────


class TestRecordingBestEffort:
    async def test_recording_failure(self, monkeypatch):
        from clawmes.services import command_history as ch_mod

        def _boom(*a, **kw):
            raise RuntimeError("history broken")

        monkeypatch.setattr(ch_mod, "record_command_call", _boom)
        out = await burn_mod.handle_burn("")
        assert isinstance(out, str)


class TestRegister:
    def test_registers(self):
        captured = []

        class FakeCtx:
            def register_command(self, **kw):
                captured.append(kw["name"])

        burn_mod.register(FakeCtx())
        assert captured == ["burn"]
