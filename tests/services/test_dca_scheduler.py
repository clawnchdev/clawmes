"""Tests for the DCA scheduler service."""

from __future__ import annotations

import pytest

from clawmes.services import dca_scheduler


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Each test starts with a fresh service instance."""
    dca_scheduler._reset_for_tests()
    yield
    dca_scheduler._reset_for_tests()


class TestSingleton:
    def test_returns_same_instance(self):
        a = dca_scheduler.get_dca_scheduler_service()
        b = dca_scheduler.get_dca_scheduler_service()
        assert a is b

    def test_reset_creates_new(self):
        a = dca_scheduler.get_dca_scheduler_service()
        dca_scheduler._reset_for_tests()
        b = dca_scheduler.get_dca_scheduler_service()
        assert a is not b


class TestLifecycle:
    def test_start_marks_running(self):
        svc = dca_scheduler.get_dca_scheduler_service()
        assert svc._running is False
        svc.start()
        assert svc._running is True

    def test_stop_marks_stopped(self):
        svc = dca_scheduler.get_dca_scheduler_service()
        svc.start()
        svc.stop()
        assert svc._running is False

    def test_health_shape(self):
        svc = dca_scheduler.get_dca_scheduler_service()
        h = svc.health()
        assert h["id"] == "clawmes.dca_scheduler"
        assert h["status"] == "stopped"
        assert h["ticks"] == 0
        assert h["total_runs"] == 0

    def test_health_running(self):
        svc = dca_scheduler.get_dca_scheduler_service()
        svc.start()
        assert svc.health()["status"] == "running"


class TestTick:
    def test_tick_skipped_when_not_running(self, monkeypatch):
        """A stopped service must not call _run_due_sync."""
        called = {"n": 0}

        from clawmes.commands import dca as dca_mod

        def _spy():
            called["n"] += 1
            return 0

        monkeypatch.setattr(dca_mod, "_run_due_sync", _spy)

        svc = dca_scheduler.get_dca_scheduler_service()
        svc.tick()  # not started
        assert called["n"] == 0
        assert svc._ticks == 0

    def test_tick_calls_dca_runner(self, monkeypatch):
        from clawmes.commands import dca as dca_mod

        def _spy():
            return 3  # pretend three schedules fired

        monkeypatch.setattr(dca_mod, "_run_due_sync", _spy)

        svc = dca_scheduler.get_dca_scheduler_service()
        svc.start()
        svc.tick()
        assert svc._ticks == 1
        assert svc._last_runs == 3
        assert svc._total_runs == 3

    def test_tick_swallows_exceptions(self, monkeypatch):
        """A runner that raises must not crash the cron loop."""
        from clawmes.commands import dca as dca_mod

        def _boom():
            raise RuntimeError("kaboom")

        monkeypatch.setattr(dca_mod, "_run_due_sync", _boom)

        svc = dca_scheduler.get_dca_scheduler_service()
        svc.start()
        # No exception should escape.
        svc.tick()
        assert svc._ticks == 1
        # Counters didn't advance (the increment is after the call).
        assert svc._last_runs == 0
        assert svc._total_runs == 0

    def test_tick_accumulates_across_calls(self, monkeypatch):
        from clawmes.commands import dca as dca_mod

        runs = iter([1, 2, 0])
        monkeypatch.setattr(dca_mod, "_run_due_sync", lambda: next(runs))

        svc = dca_scheduler.get_dca_scheduler_service()
        svc.start()
        svc.tick()
        svc.tick()
        svc.tick()
        assert svc._ticks == 3
        assert svc._total_runs == 3
        assert svc._last_runs == 0  # the most recent tick fired 0
