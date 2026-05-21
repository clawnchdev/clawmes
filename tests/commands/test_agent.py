"""Tests for /register_agent slash command."""

from __future__ import annotations

import pytest

from clawmes.commands import agent as agent_mod
from clawmes.services import clawnch as cl_mod
from clawmes.services.clawnch import ClawnchError
from clawmes.wallet.state import WalletState

ADDR = "0x" + "a" * 40


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(cl_mod, "_instance", None)


class _FakeMode:
    name = "fake"

    def __init__(self, signature="0xsig"):
        self.signature = signature
        self.raise_on_sign: Exception | None = None

    def sign_personal_message(self, message):
        if self.raise_on_sign:
            raise self.raise_on_sign
        return self.signature


class _FakeWalletSvc:
    def __init__(self, mode):
        self.active_mode = mode


class _FakeClawnchSvc:
    def __init__(self):
        self.register_calls: list[dict] = []
        self.verify_calls: list[dict] = []
        self.register_return: dict = {"registrationId": "rid", "message": "sign this"}
        self.register_raise: Exception | None = None
        self.verify_return: dict = {"apiKey": "key-x", "agentId": "agent-x"}
        self.verify_raise: Exception | None = None

    def register_agent(self, *, name, wallet, description):
        self.register_calls.append({"name": name, "wallet": wallet, "description": description})
        if self.register_raise:
            raise self.register_raise
        return self.register_return

    def verify_agent(self, *, registration_id, signature):
        self.verify_calls.append({"rid": registration_id, "sig": signature})
        if self.verify_raise:
            raise self.verify_raise
        return self.verify_return


@pytest.fixture
def setup(monkeypatch):
    """Wires fake wallet + fake clawnch service."""
    mode = _FakeMode()
    wallet_svc = _FakeWalletSvc(mode)
    clawnch_svc = _FakeClawnchSvc()
    monkeypatch.setattr("clawmes.services.wallet.get_wallet_service", lambda: wallet_svc)
    monkeypatch.setattr(
        "clawmes.services.wallet.get_wallet_state",
        lambda: WalletState.for_chain(mode="walletconnect", address=ADDR, chain_id=8453),
    )
    monkeypatch.setattr("clawmes.services.clawnch.get_clawnch_service", lambda: clawnch_svc)
    return mode, wallet_svc, clawnch_svc


# ──────────────────────────────────────────────────────────────────────
#  Usage / validation
# ──────────────────────────────────────────────────────────────────────


class TestUsage:
    async def test_no_args_shows_usage(self):
        out = await agent_mod.handle_register_agent("")
        assert "Usage" in out

    async def test_no_pipe_shows_usage(self):
        out = await agent_mod.handle_register_agent("just-a-name")
        assert "Usage" in out

    async def test_empty_name(self):
        out = await agent_mod.handle_register_agent("| desc")
        assert "Both" in out

    async def test_empty_description(self):
        out = await agent_mod.handle_register_agent("name |")
        assert "Both" in out


# ──────────────────────────────────────────────────────────────────────
#  No wallet
# ──────────────────────────────────────────────────────────────────────


class TestNoWallet:
    async def test_no_wallet_connected(self, monkeypatch):
        monkeypatch.setattr(
            "clawmes.services.wallet.get_wallet_state",
            lambda: WalletState.disconnected(),
        )
        out = await agent_mod.handle_register_agent("agent | desc")
        assert "No wallet connected" in out


# ──────────────────────────────────────────────────────────────────────
#  Register step (step 1)
# ──────────────────────────────────────────────────────────────────────


class TestRegisterStep:
    async def test_register_clawnch_error(self, setup):
        _, _, svc = setup
        svc.register_raise = ClawnchError("rate_limited", "too many")
        out = await agent_mod.handle_register_agent("agent | desc")
        assert "step 1 failed (rate_limited)" in out

    async def test_register_unexpected_error(self, setup):
        _, _, svc = setup
        svc.register_raise = RuntimeError("boom")
        out = await agent_mod.handle_register_agent("agent | desc")
        assert "step 1 failed" in out

    async def test_incomplete_challenge_response(self, setup):
        _, _, svc = setup
        svc.register_return = {"registrationId": "rid"}  # missing message
        out = await agent_mod.handle_register_agent("agent | desc")
        assert "incomplete challenge" in out


# ──────────────────────────────────────────────────────────────────────
#  Signing step
# ──────────────────────────────────────────────────────────────────────


class TestSigningStep:
    async def test_no_active_mode(self, setup, monkeypatch):
        _, _, _ = setup
        monkeypatch.setattr(
            "clawmes.services.wallet.get_wallet_service",
            lambda: _FakeWalletSvc(None),
        )
        out = await agent_mod.handle_register_agent("agent | desc")
        assert "Active wallet mode not available" in out

    async def test_signing_raises(self, setup):
        mode, _, _ = setup
        mode.raise_on_sign = RuntimeError("wallet unplugged")
        out = await agent_mod.handle_register_agent("agent | desc")
        assert "Wallet signing failed" in out


# ──────────────────────────────────────────────────────────────────────
#  Verify step (step 2)
# ──────────────────────────────────────────────────────────────────────


class TestVerifyStep:
    async def test_verify_clawnch_error(self, setup):
        _, _, svc = setup
        svc.verify_raise = ClawnchError("bad_request", "invalid sig")
        out = await agent_mod.handle_register_agent("agent | desc")
        assert "step 2 failed (bad_request)" in out

    async def test_verify_unexpected_error(self, setup):
        _, _, svc = setup
        svc.verify_raise = ValueError("boom")
        out = await agent_mod.handle_register_agent("agent | desc")
        assert "step 2 failed" in out

    async def test_no_api_key_in_response(self, setup):
        _, _, svc = setup
        svc.verify_return = {"agentId": "agent-x"}  # missing apiKey
        out = await agent_mod.handle_register_agent("agent | desc")
        assert "no apiKey" in out


# ──────────────────────────────────────────────────────────────────────
#  Happy path
# ──────────────────────────────────────────────────────────────────────


class TestHappyPath:
    async def test_full_registration_succeeds(self, setup):
        mode, _, svc = setup
        out = await agent_mod.handle_register_agent("MyAgent | A token launcher")
        assert "Agent registered" in out
        assert "key-x" in out
        assert "CLAWNCH_API_KEY=key-x" in out
        # Confirms the flow called both endpoints
        assert svc.register_calls[0]["name"] == "MyAgent"
        assert svc.register_calls[0]["wallet"] == ADDR
        assert svc.verify_calls[0]["sig"] == mode.signature


# ──────────────────────────────────────────────────────────────────────
#  Registration
# ──────────────────────────────────────────────────────────────────────


class TestRegister:
    def test_registers_one_command(self):
        captured = []

        class FakeCtx:
            def register_command(self, **kw):
                captured.append(kw["name"])

        agent_mod.register(FakeCtx())
        assert captured == ["register_agent"]


# ──────────────────────────────────────────────────────────────────────
#  Command-history best-effort
# ──────────────────────────────────────────────────────────────────────


class TestCommandHistoryBestEffort:
    async def test_recording_failure_does_not_break(self, setup, monkeypatch):
        from clawmes.services import command_history as ch_mod

        def _boom(*a, **kw):
            raise RuntimeError("history dead")

        monkeypatch.setattr(ch_mod, "record_command_call", _boom)
        out = await agent_mod.handle_register_agent("agent | desc")
        assert isinstance(out, str)
