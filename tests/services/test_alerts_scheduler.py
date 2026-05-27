"""Tests for the alerts-scheduler service."""

from __future__ import annotations

import pytest

from clawmes.services import alerts_scheduler


@pytest.fixture(autouse=True)
def _reset_singleton():
    alerts_scheduler._reset_for_tests()
    yield
    alerts_scheduler._reset_for_tests()


class TestSingleton:
    def test_same_instance(self):
        a = alerts_scheduler.get_alerts_scheduler_service()
        b = alerts_scheduler.get_alerts_scheduler_service()
        assert a is b

    def test_reset(self):
        a = alerts_scheduler.get_alerts_scheduler_service()
        alerts_scheduler._reset_for_tests()
        b = alerts_scheduler.get_alerts_scheduler_service()
        assert a is not b


class TestLifecycle:
    def test_start_stop(self):
        svc = alerts_scheduler.get_alerts_scheduler_service()
        assert svc._running is False
        svc.start()
        assert svc._running is True
        svc.stop()
        assert svc._running is False

    def test_health_shape(self):
        svc = alerts_scheduler.get_alerts_scheduler_service()
        h = svc.health()
        assert h["id"] == "clawmes.alerts_scheduler"
        assert h["status"] == "stopped"
        assert h["ticks"] == 0

    def test_health_running(self):
        svc = alerts_scheduler.get_alerts_scheduler_service()
        svc.start()
        assert svc.health()["status"] == "running"


class TestTick:
    def test_skipped_when_not_running(self, monkeypatch):
        from clawmes.commands import alerts as alerts_mod

        called = {"n": 0}

        def _spy():
            called["n"] += 1
            return 0

        monkeypatch.setattr(alerts_mod, "_run_due_sync", _spy)
        svc = alerts_scheduler.get_alerts_scheduler_service()
        svc.tick()
        assert called["n"] == 0

    def test_runs_when_started(self, monkeypatch):
        from clawmes.commands import alerts as alerts_mod

        monkeypatch.setattr(alerts_mod, "_run_due_sync", lambda: 2)
        svc = alerts_scheduler.get_alerts_scheduler_service()
        svc.start()
        svc.tick()
        assert svc._ticks == 1
        assert svc._last_runs == 2
        assert svc._total_runs == 2

    def test_swallows_errors(self, monkeypatch):
        from clawmes.commands import alerts as alerts_mod

        def _boom():
            raise RuntimeError("kaboom")

        monkeypatch.setattr(alerts_mod, "_run_due_sync", _boom)
        svc = alerts_scheduler.get_alerts_scheduler_service()
        svc.start()
        svc.tick()
        assert svc._ticks == 1
        assert svc._last_runs == 0
        assert svc._total_runs == 0

    def test_accumulates(self, monkeypatch):
        from clawmes.commands import alerts as alerts_mod

        runs = iter([1, 0, 3])
        monkeypatch.setattr(alerts_mod, "_run_due_sync", lambda: next(runs))
        svc = alerts_scheduler.get_alerts_scheduler_service()
        svc.start()
        for _ in range(3):
            svc.tick()
        assert svc._total_runs == 4
        assert svc._last_runs == 3
