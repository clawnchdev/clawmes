"""Tests for the ``watch_activity`` tool."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from clawmes.tools.watch_activity import watch_activity

ADDR = "0x" + "a" * 40


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    from clawmes.policy import storage as policy_storage
    from clawmes.services import explorer as explorer_mod

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(explorer_mod, "_instance", None)
    policy_storage.save_policies([])


@pytest.fixture
def fake_explorer(monkeypatch):
    from clawmes.services import explorer as explorer_mod

    svc = MagicMock()
    svc.get_logs.return_value = [{"transactionHash": "0xabc"}]
    monkeypatch.setattr(explorer_mod, "_instance", svc)
    return svc


class TestWatch:
    def test_basic(self):
        out = json.loads(watch_activity({"action": "watch", "address": ADDR, "label": "vitalik"}))
        assert "isError" not in out
        assert len(out["details"]["watched"]) == 1

    def test_invalid_address(self):
        out = json.loads(watch_activity({"action": "watch", "address": "0xshort"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "param_error"

    def test_dedupe(self):
        watch_activity({"action": "watch", "address": ADDR, "chain_id": 1})
        watch_activity({"action": "watch", "address": ADDR, "chain_id": 1})
        out = json.loads(watch_activity({"action": "list"}))
        # Same (address, chain) pair → 1 entry
        assert out["details"]["count"] == 1

    def test_different_chains_separate(self):
        watch_activity({"action": "watch", "address": ADDR, "chain_id": 1})
        watch_activity({"action": "watch", "address": ADDR, "chain_id": 8453})
        out = json.loads(watch_activity({"action": "list"}))
        assert out["details"]["count"] == 2


class TestUnwatch:
    def test_basic(self):
        watch_activity({"action": "watch", "address": ADDR})
        out = json.loads(watch_activity({"action": "unwatch", "address": ADDR}))
        assert "isError" not in out

    def test_not_in_list(self):
        out = json.loads(watch_activity({"action": "unwatch", "address": ADDR}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "not_found"


class TestList:
    def test_empty(self):
        out = json.loads(watch_activity({"action": "list"}))
        assert "isError" not in out
        assert out["details"]["count"] == 0

    def test_with_items(self):
        watch_activity({"action": "watch", "address": ADDR, "label": "alice"})
        out = json.loads(watch_activity({"action": "list"}))
        assert out["details"]["count"] == 1
        assert out["details"]["watched"][0]["label"] == "alice"


class TestRecent:
    def test_basic(self, fake_explorer):
        out = json.loads(watch_activity({"action": "recent", "address": ADDR}))
        assert "isError" not in out
        assert out["details"]["count"] == 1

    def test_invalid_address(self, fake_explorer):
        out = json.loads(watch_activity({"action": "recent", "address": "0xshort"}))
        assert out["isError"] is True

    def test_explorer_error(self, fake_explorer):
        from clawmes.services.explorer import ExplorerError

        fake_explorer.get_logs.side_effect = ExplorerError("api down")
        out = json.loads(watch_activity({"action": "recent", "address": ADDR}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "explorer_error"


class TestPersistence:
    def test_corrupt_file_returns_empty(self, tmp_path, monkeypatch):
        # Pre-write a corrupt watch list
        from clawmes.tools.watch_activity import _watch_path

        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        path = _watch_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not-json", encoding="utf-8")

        out = json.loads(watch_activity({"action": "list"}))
        assert out["details"]["count"] == 0

    def test_non_list_file_returns_empty(self, tmp_path, monkeypatch):
        from clawmes.tools.watch_activity import _watch_path

        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        path = _watch_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"unexpected": "shape"}', encoding="utf-8")

        out = json.loads(watch_activity({"action": "list"}))
        assert out["details"]["count"] == 0


class TestRegister:
    def test_register_calls_ctx(self):
        from clawmes.tools import watch_activity as wa_mod

        recorded = []

        class FakeCtx:
            def register_tool(self, **kw):
                recorded.append(kw)

        wa_mod.register(FakeCtx())
        assert len(recorded) == 1
        assert recorded[0]["name"] == "watch_activity"
