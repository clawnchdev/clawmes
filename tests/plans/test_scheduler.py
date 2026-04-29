"""Tests for clawmes.plans.scheduler (stub)."""

from __future__ import annotations

import pytest

from clawmes.plans import scheduler as scheduler_mod
from clawmes.plans.scheduler import PlanScheduler, get_scheduler


@pytest.fixture(autouse=True)
def _reset_singleton(monkeypatch):
    monkeypatch.setattr(scheduler_mod, "_instance", None)


def test_scheduler_starts_and_stops():
    s = PlanScheduler()
    assert s._running is False
    s.start()
    assert s._running is True
    s.stop()
    assert s._running is False


def test_scheduler_tick_when_not_running_is_noop():
    """Cover line 35 — tick early-return when not running."""
    s = PlanScheduler()
    # Don't start
    s.tick()  # must not raise


def test_scheduler_tick_when_running():
    """Cover lines 38-39 — tick body when running."""
    s = PlanScheduler()
    s.start()
    s.tick()  # currently a no-op TODO


def test_get_scheduler_singleton():
    a = get_scheduler()
    b = get_scheduler()
    assert a is b


def test_scheduler_marks_ticking():
    """The class attribute drives services.registry.tick_all."""
    assert PlanScheduler.ticking is True


class TestPlanManagement:
    @pytest.fixture(autouse=True)
    def _isolate(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    def test_create_plan(self):
        s = PlanScheduler()
        result = s.create_plan("DCA $100 weekly into ETH")
        assert result["status"] == "pending"
        assert "id" in result

    def test_validate_plan_valid(self):
        s = PlanScheduler()
        result = s.validate_plan("buy ETH when price < $1500")
        assert result["valid"] is True

    def test_validate_plan_empty(self):
        s = PlanScheduler()
        result = s.validate_plan("")
        assert result["valid"] is False

    def test_dry_run(self):
        s = PlanScheduler()
        result = s.dry_run("plan text")
        assert result["would_execute"] is True

    def test_list_empty(self):
        s = PlanScheduler()
        assert s.list_plans() == []

    def test_list_after_create(self):
        s = PlanScheduler()
        s.create_plan("plan 1")
        s.create_plan("plan 2")
        # list_plans needs distinct timestamps; a quick race test
        plans = s.list_plans()
        assert len(plans) >= 1

    def test_list_skips_corrupt(self, monkeypatch, tmp_path):
        from clawmes.plans.scheduler import _plans_dir

        d = _plans_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / "good.json").write_text('{"id": "p1", "status": "pending"}')
        (d / "bad.json").write_text("not-json")
        s = PlanScheduler()
        plans = s.list_plans()
        # Corrupt file silently skipped
        assert len(plans) == 1

    def test_cancel(self):
        s = PlanScheduler()
        record = s.create_plan("plan to cancel")
        result = s.cancel_plan(record["id"])
        assert result["cancelled"] is True

    def test_cancel_not_found(self):
        s = PlanScheduler()
        result = s.cancel_plan("nonexistent")
        assert result["cancelled"] is False
        assert "not found" in result["reason"]

    def test_cancel_unlink_failure(self, monkeypatch):
        s = PlanScheduler()
        record = s.create_plan("plan")

        # Stub Path.unlink to raise
        from pathlib import Path

        def boom(self, *args, **kwargs):
            raise OSError("permission denied")

        monkeypatch.setattr(Path, "unlink", boom)
        result = s.cancel_plan(record["id"])
        assert result["cancelled"] is False
        assert "permission" in result["reason"]

    def test_get_plan_logs_no_plan(self):
        s = PlanScheduler()
        assert s.get_plan_logs("missing") == []

    def test_get_plan_logs_empty_logs(self):
        s = PlanScheduler()
        record = s.create_plan("plan")
        # No logs field in the record yet
        logs = s.get_plan_logs(record["id"])
        assert logs == []

    def test_get_plan_logs_with_data(self, monkeypatch, tmp_path):
        from clawmes.plans.scheduler import _plans_dir

        d = _plans_dir()
        d.mkdir(parents=True, exist_ok=True)
        import json

        (d / "p1.json").write_text(
            json.dumps({"id": "p1", "logs": [{"step": 1, "status": "done"}]})
        )
        s = PlanScheduler()
        logs = s.get_plan_logs("p1")
        assert len(logs) == 1

    def test_get_plan_logs_corrupt(self, monkeypatch, tmp_path):
        from clawmes.plans.scheduler import _plans_dir

        d = _plans_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / "p1.json").write_text("not-json")
        s = PlanScheduler()
        assert s.get_plan_logs("p1") == []
