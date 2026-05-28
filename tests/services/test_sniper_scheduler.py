"""Tests for the sniper scheduler service."""

from __future__ import annotations

import pytest

from clawmes.services import sniper_scheduler


@pytest.fixture(autouse=True)
def _reset_singleton():
    sniper_scheduler._reset_for_tests()
    yield
    sniper_scheduler._reset_for_tests()


class TestSingleton:
    def test_same_instance(self):
        a = sniper_scheduler.get_sniper_scheduler_service()
        b = sniper_scheduler.get_sniper_scheduler_service()
        assert a is b

    def test_reset(self):
        a = sniper_scheduler.get_sniper_scheduler_service()
        sniper_scheduler._reset_for_tests()
        b = sniper_scheduler.get_sniper_scheduler_service()
        assert a is not b


class TestLifecycle:
    def test_start_stop(self):
        svc = sniper_scheduler.get_sniper_scheduler_service()
        assert svc._running is False
        svc.start()
        assert svc._running is True
        svc.stop()
        assert svc._running is False

    def test_health(self):
        svc = sniper_scheduler.get_sniper_scheduler_service()
        h = svc.health()
        assert h["id"] == "clawmes.sniper_scheduler"
        assert h["status"] == "stopped"
        svc.start()
        assert svc.health()["status"] == "running"


class TestTick:
    def test_skipped_when_not_running(self, monkeypatch):
        from clawmes.commands import sniper

        called = {"n": 0}

        def _spy():
            called["n"] += 1
            return 0

        monkeypatch.setattr(sniper, "_run_due_sync", _spy)
        svc = sniper_scheduler.get_sniper_scheduler_service()
        svc.tick()
        assert called["n"] == 0

    def test_runs_when_started(self, monkeypatch):
        from clawmes.commands import sniper

        monkeypatch.setattr(sniper, "_run_due_sync", lambda: 4)
        svc = sniper_scheduler.get_sniper_scheduler_service()
        svc.start()
        svc.tick()
        assert svc._ticks == 1
        assert svc._last_runs == 4

    def test_swallows_errors(self, monkeypatch):
        from clawmes.commands import sniper

        def _boom():
            raise RuntimeError("kaboom")

        monkeypatch.setattr(sniper, "_run_due_sync", _boom)
        svc = sniper_scheduler.get_sniper_scheduler_service()
        svc.start()
        svc.tick()
        assert svc._ticks == 1
        assert svc._last_runs == 0

    def test_accumulates(self, monkeypatch):
        from clawmes.commands import sniper

        runs = iter([3, 1])
        monkeypatch.setattr(sniper, "_run_due_sync", lambda: next(runs))
        svc = sniper_scheduler.get_sniper_scheduler_service()
        svc.start()
        svc.tick()
        svc.tick()
        assert svc._total_runs == 4
