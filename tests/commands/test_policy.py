"""Tests for /policy, /policy_clear, /safemode, /dangermode, /audit."""

from __future__ import annotations

import pytest

from clawmes.commands import policy as policy_cmd
from clawmes.services import mode_service as mode_module


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    # Reset mode singleton so tests don't bleed
    monkeypatch.setattr(mode_module, "_instance", None)


class TestPolicy:
    @pytest.mark.asyncio
    async def test_show_default_policies(self):
        # First call writes the bundled defaults to disk and lists them
        out = await policy_cmd.handle_policy("")
        assert "Active policies:" in out
        assert "block_unbounded_token_approvals" in out
        assert "confirm_large_transfers" in out
        assert "rate_limit_swaps" in out

    @pytest.mark.asyncio
    async def test_show_empty_after_clear(self):
        await policy_cmd.handle_policy_clear("")
        out = await policy_cmd.handle_policy("")
        assert "(none)" in out

    @pytest.mark.asyncio
    async def test_set_policies_not_yet_implemented(self):
        out = await policy_cmd.handle_policy("approve under 0.05 ETH")
        assert "not yet implemented" in out
        assert "approve under 0.05 ETH" in out

    @pytest.mark.asyncio
    async def test_policy_clear_writes_empty_list(self):
        out = await policy_cmd.handle_policy_clear("")
        assert "cleared" in out.lower()

    @pytest.mark.asyncio
    async def test_safemode_on(self):
        out = await policy_cmd.handle_safemode("")
        assert "ON" in out
        assert mode_module.get_mode_service().mode == "readonly"

    @pytest.mark.asyncio
    async def test_safemode_off(self):
        # Turn it on, then off
        await policy_cmd.handle_safemode("")
        out = await policy_cmd.handle_safemode("off")
        assert "disabled" in out.lower()
        assert mode_module.get_mode_service().mode == "normal"

    @pytest.mark.asyncio
    async def test_dangermode_on(self):
        out = await policy_cmd.handle_dangermode("")
        assert "DANGER MODE ON" in out
        assert mode_module.get_mode_service().mode == "danger"

    @pytest.mark.asyncio
    async def test_dangermode_off(self):
        await policy_cmd.handle_dangermode("")
        out = await policy_cmd.handle_dangermode("off")
        assert "disabled" in out.lower()
        assert mode_module.get_mode_service().mode == "normal"

    @pytest.mark.asyncio
    async def test_audit_stub(self):
        out = await policy_cmd.handle_audit("")
        assert "not yet implemented" in out

    @pytest.mark.asyncio
    async def test_show_includes_chain_ids_when_set(self):
        # Cover the chain_ids rendering branch in _list_policies
        from clawmes.policy.storage import save_policies
        from clawmes.policy.types import Policy

        save_policies(
            [
                Policy(
                    name="chain-scoped",
                    decision="block",
                    applies_to_tools=("transfer",),
                    chain_ids=(8453, 1),
                    description="example",
                )
            ]
        )
        out = await policy_cmd.handle_policy("")
        assert "chains=8453,1" in out


class TestRegister:
    def test_registers_five_commands(self):
        recorded = []

        class FakeCtx:
            def register_command(self, **kw):
                recorded.append(kw["name"])

        policy_cmd.register(FakeCtx())
        assert set(recorded) == {
            "policy",
            "policy_clear",
            "safemode",
            "dangermode",
            "audit",
        }
