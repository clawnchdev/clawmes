"""Tests for agent_memory, skill_evolve, session_recall, privacy, herd_intelligence."""

from __future__ import annotations

import json
import sys
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    from clawmes.policy import storage as policy_storage
    from clawmes.services import evolution_mode as evo_mod

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    for k in ("HERD_ACCESS_TOKEN", "LOBSTER_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    policy_storage.save_policies([])
    # The agent_memory and skill_evolve write actions are now gated by
    # evolution_mode (default off). Enable it for the existing tests
    # so write-action assertions continue to work; gating behavior is
    # tested separately in test_evolution_mode tests.
    monkeypatch.setattr(evo_mod, "_instance", None)
    evo_mod.get_evolution_mode_service().set_evolving(True)


def _stub(monkeypatch, module_path: str, attr: str, response):
    def fake(*args, **kwargs):
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(f"{module_path}.{attr}", fake)


# --- agent_memory -----


class TestAgentMemory:
    def test_no_provider(self):
        # plugins.memory not importable
        from clawmes.tools.agent_memory import agent_memory

        out = json.loads(agent_memory({"action": "query", "query": "x"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "not_available"

    def test_with_fake_provider(self, monkeypatch):
        # Inject a fake plugins.memory module
        fake_provider = MagicMock()
        fake_provider.add.return_value = {"id": "m1"}
        fake_provider.replace.return_value = {"id": "m1", "updated": True}
        fake_provider.remove.return_value = {"removed": True}
        fake_provider.query.return_value = [{"key": "k", "content": "v"}]

        fake_module = MagicMock()
        fake_module.get_active_provider.return_value = fake_provider
        monkeypatch.setitem(sys.modules, "plugins.memory", fake_module)

        from clawmes.tools.agent_memory import agent_memory

        out = json.loads(agent_memory({"action": "add", "key": "k", "content": "v"}))
        assert "isError" not in out

        out = json.loads(agent_memory({"action": "replace", "key": "k", "content": "v2"}))
        assert "isError" not in out

        out = json.loads(agent_memory({"action": "remove", "key": "k"}))
        assert "isError" not in out

        out = json.loads(agent_memory({"action": "query", "query": "v"}))
        assert "isError" not in out

    def test_provider_raises(self, monkeypatch):
        fake_provider = MagicMock()
        fake_provider.query.side_effect = RuntimeError("backend down")
        fake_module = MagicMock()
        fake_module.get_active_provider.return_value = fake_provider
        monkeypatch.setitem(sys.modules, "plugins.memory", fake_module)

        from clawmes.tools.agent_memory import agent_memory

        out = json.loads(agent_memory({"action": "query", "query": "x"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "provider_error"


# --- skill_evolve -----


class TestSkillEvolve:
    def test_propose_creates_file(self, tmp_path, monkeypatch):
        from clawmes.tools.skill_evolve import skill_evolve

        out = json.loads(skill_evolve({"action": "propose", "skill": "test", "change": "add x"}))
        assert "isError" not in out
        proposal_id = out["details"]["id"]
        assert proposal_id.startswith("prop-")

    def test_list_empty(self):
        from clawmes.tools.skill_evolve import skill_evolve

        out = json.loads(skill_evolve({"action": "list"}))
        assert out["details"]["proposals"] == []

    def test_propose_then_update(self, tmp_path, monkeypatch):
        from clawmes.tools.skill_evolve import skill_evolve

        propose_out = json.loads(
            skill_evolve({"action": "propose", "skill": "s1", "change": "add y"})
        )
        proposal_id = propose_out["details"]["id"]

        update_out = json.loads(skill_evolve({"action": "update", "proposal_id": proposal_id}))
        assert "isError" not in update_out

        # List should now show in applied
        list_out = json.loads(skill_evolve({"action": "list"}))
        assert len(list_out["details"]["applied"]) == 1

    def test_update_not_found(self):
        from clawmes.tools.skill_evolve import skill_evolve

        out = json.loads(skill_evolve({"action": "update", "proposal_id": "nonexistent"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "not_found"

    def test_revert(self):
        from clawmes.tools.skill_evolve import skill_evolve

        propose_out = json.loads(skill_evolve({"action": "propose", "skill": "s1", "change": "x"}))
        pid = propose_out["details"]["id"]
        skill_evolve({"action": "update", "proposal_id": pid})

        revert_out = json.loads(skill_evolve({"action": "revert", "proposal_id": pid}))
        assert "isError" not in revert_out

        list_out = json.loads(skill_evolve({"action": "list"}))
        assert len(list_out["details"]["reverted"]) == 1
        assert len(list_out["details"]["applied"]) == 0

    def test_revert_not_found(self):
        from clawmes.tools.skill_evolve import skill_evolve

        out = json.loads(skill_evolve({"action": "revert", "proposal_id": "nope"}))
        assert out["isError"] is True

    def test_list_handles_corrupt_files(self, monkeypatch):
        from clawmes.tools.skill_evolve import (
            _evolution_dir,
            skill_evolve,
        )

        d = _evolution_dir() / "applied"
        d.mkdir(parents=True, exist_ok=True)
        (d / "corrupt.json").write_text("not-json", encoding="utf-8")

        out = json.loads(skill_evolve({"action": "list"}))
        # Corrupt file silently skipped
        assert out["details"]["applied"] == []


# --- session_recall -----


class TestSessionRecall:
    def test_no_dir(self):
        from clawmes.tools.session_recall import session_recall

        out = json.loads(session_recall({"action": "recent"}))
        # No sessions dir → empty result
        assert out["details"]["sessions"] == []

    def test_recent(self, tmp_path, monkeypatch):
        from clawmes.tools.session_recall import (
            _sessions_dir,
            session_recall,
        )

        d = _sessions_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / "s1.json").write_text(
            json.dumps({"started_at": "2026-01-01", "title": "Test"}),
            encoding="utf-8",
        )
        out = json.loads(session_recall({"action": "recent"}))
        assert "isError" not in out
        assert out["details"]["count"] == 1

    def test_recent_skips_corrupt(self, tmp_path, monkeypatch):
        from clawmes.tools.session_recall import (
            _sessions_dir,
            session_recall,
        )

        d = _sessions_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / "good.json").write_text(json.dumps({"title": "ok"}))
        (d / "bad.json").write_text("not-json")
        out = json.loads(session_recall({"action": "recent"}))
        assert out["details"]["count"] == 1

    def test_summarize(self, tmp_path, monkeypatch):
        from clawmes.tools.session_recall import (
            _sessions_dir,
            session_recall,
        )

        d = _sessions_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / "s1.json").write_text(
            json.dumps({"title": "T", "messages": [{}, {}]}),
            encoding="utf-8",
        )
        out = json.loads(session_recall({"action": "summarize", "session_id": "s1"}))
        assert "isError" not in out

    def test_summarize_not_found(self):
        from clawmes.tools.session_recall import session_recall

        out = json.loads(session_recall({"action": "summarize", "session_id": "missing"}))
        assert out["isError"] is True

    def test_summarize_corrupt_file(self, tmp_path, monkeypatch):
        from clawmes.tools.session_recall import (
            _sessions_dir,
            session_recall,
        )

        d = _sessions_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / "s1.json").write_text("not-json", encoding="utf-8")
        out = json.loads(session_recall({"action": "summarize", "session_id": "s1"}))
        assert out["isError"] is True

    def test_search(self, tmp_path, monkeypatch):
        from clawmes.tools.session_recall import (
            _sessions_dir,
            session_recall,
        )

        d = _sessions_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / "s1.json").write_text(json.dumps({"messages": [{"text": "discussing ethereum"}]}))
        (d / "s2.json").write_text(json.dumps({"messages": [{"text": "about solana"}]}))
        out = json.loads(session_recall({"action": "search", "query": "ethereum"}))
        assert out["details"]["count"] == 1

    def test_search_limit_break(self, tmp_path, monkeypatch):
        from clawmes.tools.session_recall import (
            _sessions_dir,
            session_recall,
        )

        d = _sessions_dir()
        d.mkdir(parents=True, exist_ok=True)
        # Create 5 files all matching the query; limit=2 should stop early
        for i in range(5):
            (d / f"s{i}.json").write_text(json.dumps({"text": "ethereum"}), encoding="utf-8")
        out = json.loads(session_recall({"action": "search", "query": "ethereum", "limit": 2}))
        assert out["details"]["count"] == 2

    def test_search_skips_unreadable(self, tmp_path, monkeypatch):
        from clawmes.tools.session_recall import (
            _sessions_dir,
            session_recall,
        )

        d = _sessions_dir()
        d.mkdir(parents=True, exist_ok=True)
        good_path = d / "good.json"
        good_path.write_text(json.dumps({"text": "match"}))

        # Mock Path.read_text to raise OSError for one specific file
        original_read = type(good_path).read_text

        def patched_read(self, *args, **kwargs):
            if self.name == "good.json":
                raise OSError("permission denied")
            return original_read(self, *args, **kwargs)

        monkeypatch.setattr(type(good_path), "read_text", patched_read)
        out = json.loads(session_recall({"action": "search", "query": "match"}))
        # File raises → skipped → no matches
        assert out["details"]["count"] == 0


# --- privacy -----


class TestPrivacy:
    def test_transfer_not_implemented(self):
        from clawmes.tools.privacy import privacy

        out = json.loads(privacy({"action": "transfer", "amount": "1"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "not_implemented"

    def test_deposit_routes_to_lobster(self, monkeypatch):
        # Stub out lobster's HTTP layer
        monkeypatch.setenv("LOBSTER_API_KEY", "k")
        _stub(
            monkeypatch,
            "clawmes.tools.lobster_cash",
            "http_post",
            {"note": "0xn"},
        )
        from clawmes.tools.privacy import privacy

        out = json.loads(privacy({"action": "deposit", "amount": "100"}))
        assert "isError" not in out

    def test_withdraw_routes_to_lobster(self, monkeypatch):
        monkeypatch.setenv("LOBSTER_API_KEY", "k")
        _stub(
            monkeypatch,
            "clawmes.tools.lobster_cash",
            "http_post",
            {"tx": "0xtx"},
        )
        from clawmes.tools.privacy import privacy

        out = json.loads(
            privacy(
                {
                    "action": "withdraw",
                    "note": "0xn",
                    "destination": "0x" + "a" * 40,
                }
            )
        )
        assert "isError" not in out

    def test_info_no_key(self):
        from clawmes.tools.privacy import privacy

        out = json.loads(privacy({"action": "info"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "no_credentials"

    def test_info_with_key(self, monkeypatch):
        monkeypatch.setenv("LOBSTER_API_KEY", "k")
        _stub(
            monkeypatch,
            "clawmes.tools.privacy",
            "http_get",
            {"anonymity_set": 1000},
        )
        from clawmes.tools.privacy import privacy

        out = json.loads(privacy({"action": "info"}))
        assert "isError" not in out

    def test_info_with_pool(self, monkeypatch):
        monkeypatch.setenv("LOBSTER_API_KEY", "k")
        _stub(monkeypatch, "clawmes.tools.privacy", "http_get", {"pool": "x"})
        from clawmes.tools.privacy import privacy

        out = json.loads(privacy({"action": "info", "pool": "ETH-1"}))
        assert "isError" not in out

    def test_info_api_error(self, monkeypatch):
        monkeypatch.setenv("LOBSTER_API_KEY", "k")
        _stub(
            monkeypatch,
            "clawmes.tools.privacy",
            "http_get",
            RuntimeError("network"),
        )
        from clawmes.tools.privacy import privacy

        out = json.loads(privacy({"action": "info"}))
        assert out["isError"] is True


# --- herd_intelligence -----


class TestHerdIntelligence:
    def test_no_token(self):
        from clawmes.tools.herd_intelligence import herd_intelligence

        out = json.loads(herd_intelligence({"action": "swaps"}))
        assert out["isError"] is True

    def test_swaps(self, monkeypatch):
        monkeypatch.setenv("HERD_ACCESS_TOKEN", "t")
        _stub(
            monkeypatch,
            "clawmes.tools.herd_intelligence",
            "http_get",
            {"swaps": []},
        )
        from clawmes.tools.herd_intelligence import herd_intelligence

        out = json.loads(herd_intelligence({"action": "swaps"}))
        assert "isError" not in out

    def test_wallet_activity(self, monkeypatch):
        monkeypatch.setenv("HERD_ACCESS_TOKEN", "t")
        _stub(
            monkeypatch,
            "clawmes.tools.herd_intelligence",
            "http_get",
            {"activity": []},
        )
        from clawmes.tools.herd_intelligence import herd_intelligence

        out = json.loads(
            herd_intelligence({"action": "wallet_activity", "address": "0x" + "a" * 40})
        )
        assert "isError" not in out

    def test_whale_alerts(self, monkeypatch):
        monkeypatch.setenv("HERD_ACCESS_TOKEN", "t")
        _stub(
            monkeypatch,
            "clawmes.tools.herd_intelligence",
            "http_get",
            {"alerts": []},
        )
        from clawmes.tools.herd_intelligence import herd_intelligence

        out = json.loads(herd_intelligence({"action": "whale_alerts", "min_usd": 50000}))
        assert "isError" not in out

    def test_subscribe(self, monkeypatch):
        monkeypatch.setenv("HERD_ACCESS_TOKEN", "t")
        _stub(
            monkeypatch,
            "clawmes.tools.herd_intelligence",
            "http_post",
            {"subscription_id": "s1"},
        )
        from clawmes.tools.herd_intelligence import herd_intelligence

        out = json.loads(herd_intelligence({"action": "subscribe", "filters": {"min_usd": 100000}}))
        assert "isError" not in out

    def test_subscribe_non_dict_filters(self, monkeypatch):
        monkeypatch.setenv("HERD_ACCESS_TOKEN", "t")
        _stub(
            monkeypatch,
            "clawmes.tools.herd_intelligence",
            "http_post",
            {"id": "s1"},
        )
        from clawmes.tools.herd_intelligence import herd_intelligence

        out = json.loads(herd_intelligence({"action": "subscribe", "filters": "garbage"}))
        assert "isError" not in out

    def test_api_error(self, monkeypatch):
        monkeypatch.setenv("HERD_ACCESS_TOKEN", "t")
        _stub(
            monkeypatch,
            "clawmes.tools.herd_intelligence",
            "http_get",
            RuntimeError("rate limit"),
        )
        from clawmes.tools.herd_intelligence import herd_intelligence

        out = json.loads(herd_intelligence({"action": "swaps"}))
        assert out["isError"] is True


# --- registers -----


class TestRegister:
    @pytest.mark.parametrize(
        "module_path,name",
        [
            ("clawmes.tools.agent_memory", "agent_memory"),
            ("clawmes.tools.skill_evolve", "skill_evolve"),
            ("clawmes.tools.session_recall", "session_recall"),
            ("clawmes.tools.privacy", "privacy"),
            ("clawmes.tools.herd_intelligence", "herd_intelligence"),
        ],
    )
    def test_register(self, module_path, name):
        import importlib

        mod = importlib.import_module(module_path)
        recorded = []

        class FakeCtx:
            def register_tool(self, **kw):
                recorded.append(kw)

        mod.register(FakeCtx())
        assert recorded[0]["name"] == name
