"""Tests for /policy, /policy_clear, /safemode, /dangermode, /audit."""

from __future__ import annotations

import pytest

from clawmes.commands import policy as policy_cmd


class TestPolicy:
    @pytest.mark.asyncio
    async def test_show_active_policies_when_empty(self):
        out = await policy_cmd.handle_policy("")
        assert "Active policies:" in out

    @pytest.mark.asyncio
    async def test_set_policies_stub(self):
        out = await policy_cmd.handle_policy("approve under 0.05 ETH")
        assert "not yet implemented" in out
        assert "approve under 0.05 ETH" in out

    @pytest.mark.asyncio
    async def test_policy_clear_stub(self):
        out = await policy_cmd.handle_policy_clear("")
        assert "not yet implemented" in out

    @pytest.mark.asyncio
    async def test_safemode_stub(self):
        out = await policy_cmd.handle_safemode("")
        assert "not yet implemented" in out

    @pytest.mark.asyncio
    async def test_dangermode_stub(self):
        out = await policy_cmd.handle_dangermode("")
        assert "not yet implemented" in out

    @pytest.mark.asyncio
    async def test_audit_stub(self):
        out = await policy_cmd.handle_audit("")
        assert "not yet implemented" in out


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
