"""Tests for /plans, /plan, /plan_logs, /interrupt_plan, /pause_plan,
/resume_plan, /triggers, /watch, /unwatch, /cron commands."""

from __future__ import annotations

import pytest

from clawmes.commands import plans as plans_cmd


class TestPlans:
    @pytest.mark.asyncio
    async def test_plans_empty(self):
        out = await plans_cmd.handle_plans("")
        assert "Active plans" in out

    @pytest.mark.asyncio
    async def test_plan_no_id(self):
        out = await plans_cmd.handle_plan("")
        assert "Usage:" in out

    @pytest.mark.asyncio
    async def test_plan_unknown(self):
        out = await plans_cmd.handle_plan("nonexistent-id")
        assert "not found" in out
        assert "nonexistent-id" in out

    @pytest.mark.asyncio
    async def test_plan_logs_stub(self):
        out = await plans_cmd.handle_plan_logs("")
        assert "not yet implemented" in out

    @pytest.mark.asyncio
    async def test_interrupt_plan_stub(self):
        out = await plans_cmd.handle_interrupt_plan("")
        assert "not yet implemented" in out

    @pytest.mark.asyncio
    async def test_pause_plan_stub(self):
        out = await plans_cmd.handle_pause_plan("")
        assert "not yet implemented" in out

    @pytest.mark.asyncio
    async def test_resume_plan_stub(self):
        out = await plans_cmd.handle_resume_plan("")
        assert "not yet implemented" in out

    @pytest.mark.asyncio
    async def test_triggers_empty(self):
        out = await plans_cmd.handle_triggers("")
        assert "Active triggers" in out

    @pytest.mark.asyncio
    async def test_watch_stub(self):
        out = await plans_cmd.handle_watch("")
        assert "not yet implemented" in out

    @pytest.mark.asyncio
    async def test_unwatch_stub(self):
        out = await plans_cmd.handle_unwatch("")
        assert "not yet implemented" in out

    @pytest.mark.asyncio
    async def test_cron(self):
        out = await plans_cmd.handle_cron("")
        assert "clawmes_plan_tick" in out
        assert "every 1m" in out


class TestRegister:
    def test_registers_ten_commands(self):
        recorded = []

        class FakeCtx:
            def register_command(self, **kw):
                recorded.append(kw["name"])

        plans_cmd.register(FakeCtx())
        assert set(recorded) == {
            "plans",
            "plan",
            "plan_logs",
            "interrupt_plan",
            "pause_plan",
            "resume_plan",
            "triggers",
            "watch",
            "unwatch",
            "cron",
        }
