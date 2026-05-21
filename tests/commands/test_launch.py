"""Tests for the /launch slash command."""

from __future__ import annotations

import pytest

from clawmes.commands import launch as launch_mod
from clawmes.services import clawnch as cl_mod
from clawmes.services.clawnch import ClawnchError


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    # Reset per-sender state between tests
    monkeypatch.setattr(launch_mod, "_log_state", {})
    monkeypatch.setattr(cl_mod, "_instance", None)


class _FakeSvc:
    def __init__(self):
        self.deploys: list[dict] = []
        self.deploy_return: dict = {"txHash": "0xtx", "tokenAddress": "0xtok"}
        self.deploy_raise: Exception | None = None

    def deploy(self, *, token_params, bypass_tx_hash=None):
        self.deploys.append({"params": token_params, "bypass": bypass_tx_hash})
        if self.deploy_raise:
            raise self.deploy_raise
        return self.deploy_return

    def get_bypass_recipient(self):
        return {"recipient": "0xbypass", "fee_eth": "0.001"}


@pytest.fixture
def fake_svc(monkeypatch):
    s = _FakeSvc()
    monkeypatch.setattr("clawmes.services.clawnch.get_clawnch_service", lambda: s)
    return s


class TestUsageAndStatus:
    async def test_no_args_shows_usage(self):
        out = await launch_mod.handle_launch("")
        assert "Launch a token on Clawnch" in out
        assert "CLAWNCH_API_KEY" in out

    async def test_usage_shows_existing_draft(self):
        await launch_mod.handle_launch("name MyCoin", sender_id="alice")
        out = await launch_mod.handle_launch("", sender_id="alice")
        assert "Current draft" in out
        assert "MyCoin" in out

    async def test_status_empty(self):
        out = await launch_mod.handle_launch("status", sender_id="alice")
        assert "No launch draft" in out

    async def test_status_with_draft(self):
        await launch_mod.handle_launch("name MyCoin", sender_id="alice")
        out = await launch_mod.handle_launch("status", sender_id="alice")
        assert "MyCoin" in out

    async def test_unknown_arg(self):
        out = await launch_mod.handle_launch("explode")
        assert "Unknown /launch arg" in out


class TestDraftBuilding:
    async def test_name_set(self):
        out = await launch_mod.handle_launch("name MyCoin", sender_id="alice")
        assert "Name set" in out
        assert launch_mod._log_state["alice"]["name"] == "MyCoin"

    async def test_name_requires_value(self):
        out = await launch_mod.handle_launch("name", sender_id="alice")
        assert "Usage" in out

    async def test_symbol_uppercases(self):
        await launch_mod.handle_launch("symbol foo", sender_id="alice")
        assert launch_mod._log_state["alice"]["symbol"] == "FOO"

    async def test_symbol_requires_value(self):
        out = await launch_mod.handle_launch("symbol", sender_id="alice")
        assert "Usage" in out

    async def test_description_set(self):
        await launch_mod.handle_launch("description a cool coin", sender_id="alice")
        assert launch_mod._log_state["alice"]["description"] == "a cool coin"

    async def test_description_requires_value(self):
        out = await launch_mod.handle_launch("description", sender_id="alice")
        assert "Usage" in out

    async def test_bypass_set(self):
        await launch_mod.handle_launch("bypass 0xbeef", sender_id="alice")
        assert launch_mod._log_state["alice"]["bypass_tx_hash"] == "0xbeef"

    async def test_bypass_requires_value(self):
        out = await launch_mod.handle_launch("bypass", sender_id="alice")
        assert "Usage" in out

    async def test_cancel_clears(self):
        await launch_mod.handle_launch("name X", sender_id="alice")
        out = await launch_mod.handle_launch("cancel", sender_id="alice")
        assert "cleared" in out
        assert "alice" not in launch_mod._log_state


class TestPerSenderIsolation:
    async def test_two_senders_independent(self):
        await launch_mod.handle_launch("name AliceCoin", sender_id="alice")
        await launch_mod.handle_launch("name BobCoin", sender_id="bob")
        assert launch_mod._log_state["alice"]["name"] == "AliceCoin"
        assert launch_mod._log_state["bob"]["name"] == "BobCoin"

    async def test_default_sender(self):
        # No sender_id kwarg = "default"
        await launch_mod.handle_launch("name DefCoin")
        assert launch_mod._log_state["default"]["name"] == "DefCoin"


class TestConfirm:
    async def test_missing_name(self, fake_svc):
        out = await launch_mod.handle_launch("confirm", sender_id="alice")
        assert "needs at minimum a name" in out

    async def test_missing_symbol(self, fake_svc):
        await launch_mod.handle_launch("name X", sender_id="alice")
        out = await launch_mod.handle_launch("confirm", sender_id="alice")
        assert "needs at minimum" in out

    async def test_successful_confirm_clears_draft(self, fake_svc):
        await launch_mod.handle_launch("name MyCoin", sender_id="alice")
        await launch_mod.handle_launch("symbol MC", sender_id="alice")
        out = await launch_mod.handle_launch("confirm", sender_id="alice")
        assert "Launched" in out
        assert "0xtok" in out
        assert "0xtx" in out
        # Draft cleared
        assert "alice" not in launch_mod._log_state

    async def test_with_description_passed_to_service(self, fake_svc):
        await launch_mod.handle_launch("name MyCoin", sender_id="alice")
        await launch_mod.handle_launch("symbol MC", sender_id="alice")
        await launch_mod.handle_launch("description a thing", sender_id="alice")
        await launch_mod.handle_launch("confirm", sender_id="alice")
        params = fake_svc.deploys[0]["params"]
        assert params["description"] == "a thing"

    async def test_with_bypass_passed_to_service(self, fake_svc):
        await launch_mod.handle_launch("name MyCoin", sender_id="alice")
        await launch_mod.handle_launch("symbol MC", sender_id="alice")
        await launch_mod.handle_launch("bypass 0xb", sender_id="alice")
        await launch_mod.handle_launch("confirm", sender_id="alice")
        assert fake_svc.deploys[0]["bypass"] == "0xb"

    async def test_no_credentials_error_shows_hint(self, fake_svc):
        fake_svc.deploy_raise = ClawnchError("no_credentials", "no key")
        await launch_mod.handle_launch("name MyCoin", sender_id="alice")
        await launch_mod.handle_launch("symbol MC", sender_id="alice")
        out = await launch_mod.handle_launch("confirm", sender_id="alice")
        assert "no_credentials" in out
        assert "/register_agent" in out

    async def test_rate_limited_error_shows_bypass(self, fake_svc):
        fake_svc.deploy_raise = ClawnchError("rate_limited", "wait 24h")
        await launch_mod.handle_launch("name MyCoin", sender_id="alice")
        await launch_mod.handle_launch("symbol MC", sender_id="alice")
        out = await launch_mod.handle_launch("confirm", sender_id="alice")
        assert "0xbypass" in out
        assert "0.001 ETH" in out
        assert "/launch bypass" in out

    async def test_other_clawnch_error(self, fake_svc):
        fake_svc.deploy_raise = ClawnchError("api_error", "boom")
        await launch_mod.handle_launch("name MyCoin", sender_id="alice")
        await launch_mod.handle_launch("symbol MC", sender_id="alice")
        out = await launch_mod.handle_launch("confirm", sender_id="alice")
        assert "api_error" in out
        # No /register_agent or /bypass hints for unrelated codes
        assert "/register_agent" not in out
        assert "0xbypass" not in out

    async def test_unexpected_error(self, fake_svc):
        fake_svc.deploy_raise = RuntimeError("boom")
        await launch_mod.handle_launch("name MyCoin", sender_id="alice")
        await launch_mod.handle_launch("symbol MC", sender_id="alice")
        out = await launch_mod.handle_launch("confirm", sender_id="alice")
        assert "Launch failed" in out

    async def test_response_with_only_tx_hash(self, fake_svc):
        fake_svc.deploy_return = {"txHash": "0xtx"}
        await launch_mod.handle_launch("name MyCoin", sender_id="alice")
        await launch_mod.handle_launch("symbol MC", sender_id="alice")
        out = await launch_mod.handle_launch("confirm", sender_id="alice")
        assert "0xtx" in out


class TestRegister:
    def test_registers_one_command(self):
        captured = []

        class FakeCtx:
            def register_command(self, **kw):
                captured.append(kw["name"])

        launch_mod.register(FakeCtx())
        assert captured == ["launch"]


class TestCommandHistoryBestEffort:
    async def test_recording_failure_does_not_break_command(self, monkeypatch):
        from clawmes.services import command_history as ch_mod

        def _boom(*a, **kw):
            raise RuntimeError("history broken")

        monkeypatch.setattr(ch_mod, "record_command_call", _boom)
        out = await launch_mod.handle_launch("")
        assert isinstance(out, str)
