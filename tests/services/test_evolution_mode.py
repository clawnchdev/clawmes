"""Tests for clawmes.services.evolution_mode."""

from __future__ import annotations

import json

import pytest

from clawmes.services import evolution_mode as evo_mod
from clawmes.services.evolution_mode import (
    EvolutionModeService,
    get_evolution_mode_service,
    is_evolving,
)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(evo_mod, "_instance", None)


class TestLifecycle:
    def test_start_is_noop(self):
        EvolutionModeService().start()

    def test_stop_resets_to_safe_default(self):
        svc = EvolutionModeService()
        svc.set_evolving(True)
        svc.stop()
        assert svc.is_evolving() is False

    def test_default_is_disabled(self):
        assert EvolutionModeService().is_evolving() is False

    def test_health_reports_status(self):
        svc = EvolutionModeService()
        assert svc.health()["status"] == "stable"
        svc.set_evolving(True)
        assert svc.health()["status"] == "evolving"


class TestSetEvolving:
    def test_enable(self):
        svc = EvolutionModeService()
        assert svc.set_evolving(True) is True
        assert svc.is_evolving() is True

    def test_disable(self):
        svc = EvolutionModeService()
        svc.set_evolving(True)
        assert svc.set_evolving(False) is False
        assert svc.is_evolving() is False

    def test_coerces_truthy_values(self):
        svc = EvolutionModeService()
        assert svc.set_evolving(1) is True  # type: ignore[arg-type]
        assert svc.set_evolving(0) is False  # type: ignore[arg-type]

    def test_idempotent_enable(self):
        svc = EvolutionModeService()
        svc.set_evolving(True)
        # Setting twice in the same state shouldn't crash or change state.
        assert svc.set_evolving(True) is True


class TestSingleton:
    def test_returns_same_instance(self):
        a = get_evolution_mode_service()
        b = get_evolution_mode_service()
        assert a is b

    def test_module_level_helper(self):
        assert is_evolving() is False
        get_evolution_mode_service().set_evolving(True)
        assert is_evolving() is True


# --- gating behavior on agent_memory + skill_evolve ---------------------


class TestAgentMemoryGate:
    @pytest.fixture(autouse=True)
    def _setup(self, monkeypatch, tmp_path):
        # Isolate filesystem + ensure evolution is OFF.
        from clawmes.policy import storage as policy_storage

        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        policy_storage.save_policies([])
        # Default evolution state is off, so explicit set_evolving(False)
        # isn't strictly necessary, but be explicit.
        get_evolution_mode_service().set_evolving(False)

    def test_add_blocked_when_disabled(self):
        from clawmes.tools.agent_memory import agent_memory

        out = json.loads(agent_memory({"action": "add", "key": "k", "content": "v"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "evolution_gate"

    def test_replace_blocked_when_disabled(self):
        from clawmes.tools.agent_memory import agent_memory

        out = json.loads(agent_memory({"action": "replace", "key": "k", "content": "v"}))
        assert out["details"]["error_code"] == "evolution_gate"

    def test_remove_blocked_when_disabled(self):
        from clawmes.tools.agent_memory import agent_memory

        out = json.loads(agent_memory({"action": "remove", "key": "k"}))
        assert out["details"]["error_code"] == "evolution_gate"

    def test_query_allowed_when_disabled(self):
        # Read action — must work even with evolution disabled.
        from clawmes.tools.agent_memory import agent_memory

        out = json.loads(agent_memory({"action": "query", "query": "x"}))
        # Either succeeds (provider returned data) or returns not_available
        # because no Hermes memory provider is loaded — both are fine, the
        # important thing is the action wasn't gated.
        if out.get("isError"):
            assert out["details"]["error_code"] != "evolution_gate"

    def test_add_allowed_when_enabled(self, monkeypatch):
        from clawmes.tools import agent_memory as am_mod

        # Stub Hermes' memory provider so we can verify the call reaches it.
        fake_provider = type(
            "FakeProvider",
            (),
            {"add": lambda self, k, v: {"ok": True, "key": k, "value": v}},
        )()
        monkeypatch.setattr(am_mod, "_resolve_provider", lambda: fake_provider)

        get_evolution_mode_service().set_evolving(True)
        out = json.loads(am_mod.agent_memory({"action": "add", "key": "k", "content": "v"}))
        assert "isError" not in out
        assert out["details"]["result"]["ok"] is True


class TestSkillEvolveGate:
    @pytest.fixture(autouse=True)
    def _setup(self, monkeypatch, tmp_path):
        from clawmes.policy import storage as policy_storage

        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        policy_storage.save_policies([])
        get_evolution_mode_service().set_evolving(False)

    def test_propose_blocked_when_disabled(self):
        from clawmes.tools.skill_evolve import skill_evolve

        out = json.loads(skill_evolve({"action": "propose", "skill": "s1", "change": "add x"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "evolution_gate"

    def test_update_blocked_when_disabled(self):
        from clawmes.tools.skill_evolve import skill_evolve

        out = json.loads(skill_evolve({"action": "update", "proposal_id": "p1"}))
        assert out["details"]["error_code"] == "evolution_gate"

    def test_revert_blocked_when_disabled(self):
        from clawmes.tools.skill_evolve import skill_evolve

        out = json.loads(skill_evolve({"action": "revert", "proposal_id": "p1"}))
        assert out["details"]["error_code"] == "evolution_gate"

    def test_list_allowed_when_disabled(self):
        from clawmes.tools.skill_evolve import skill_evolve

        out = json.loads(skill_evolve({"action": "list"}))
        assert "isError" not in out
        assert "proposals" in out["details"]

    def test_propose_allowed_when_enabled(self):
        from clawmes.tools.skill_evolve import skill_evolve

        get_evolution_mode_service().set_evolving(True)
        out = json.loads(skill_evolve({"action": "propose", "skill": "s1", "change": "add x"}))
        assert "isError" not in out
        assert out["details"]["skill"] == "s1"
