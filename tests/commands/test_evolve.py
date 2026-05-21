"""Tests for /evolve, /stable, /evolution slash commands."""

from __future__ import annotations

import pytest

from clawmes.commands import evolve as evolve_cmd
from clawmes.services import evolution_mode as evo_mod


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(evo_mod, "_instance", None)


class TestHandleEvolve:
    async def test_enables_evolution_mode(self):
        assert evo_mod.get_evolution_mode_service().is_evolving() is False
        out = await evolve_cmd.handle_evolve("")
        assert "ENABLED" in out
        assert evo_mod.get_evolution_mode_service().is_evolving() is True

    async def test_idempotent_when_already_enabled(self):
        evo_mod.get_evolution_mode_service().set_evolving(True)
        out = await evolve_cmd.handle_evolve("")
        assert "ENABLED" in out
        assert evo_mod.get_evolution_mode_service().is_evolving() is True

    async def test_unexpected_failure_path(self, monkeypatch):
        # Force set_evolving to return False even when asked to enable
        # (defensive coverage for the "should never reach here" branch).
        monkeypatch.setattr(
            evo_mod.EvolutionModeService,
            "set_evolving",
            lambda self, enabled: False,
        )
        out = await evolve_cmd.handle_evolve("")
        assert "toggle failed" in out


class TestHandleStable:
    async def test_disables_evolution_mode(self):
        evo_mod.get_evolution_mode_service().set_evolving(True)
        out = await evolve_cmd.handle_stable("")
        assert "DISABLED" in out
        assert evo_mod.get_evolution_mode_service().is_evolving() is False

    async def test_idempotent_when_already_disabled(self):
        out = await evolve_cmd.handle_stable("")
        assert "DISABLED" in out
        assert evo_mod.get_evolution_mode_service().is_evolving() is False


class TestHandleEvolution:
    async def test_reports_disabled(self):
        out = await evolve_cmd.handle_evolution("")
        assert "DISABLED" in out
        assert "/evolve" in out

    async def test_reports_enabled(self):
        evo_mod.get_evolution_mode_service().set_evolving(True)
        out = await evolve_cmd.handle_evolution("")
        assert "ENABLED" in out
        assert "agent_memory" in out
        assert "skill_evolve" in out


class TestRegister:
    def test_registers_three_commands(self):
        captured = []

        class FakeCtx:
            def register_command(self, **kw):
                captured.append(kw["name"])

        evolve_cmd.register(FakeCtx())
        assert set(captured) == {"evolve", "stable", "evolution"}
