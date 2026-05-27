"""Tests for the limit-order scheduler service."""

from __future__ import annotations

import pytest

from clawmes.services import limit_order_scheduler


@pytest.fixture(autouse=True)
def _reset_singleton():
    limit_order_scheduler._reset_for_tests()
    yield
    limit_order_scheduler._reset_for_tests()


class TestSingleton:
    def test_same_instance(self):
        a = limit_order_scheduler.get_limit_order_scheduler_service()
        b = limit_order_scheduler.get_limit_order_scheduler_service()
        assert a is b

    def test_reset(self):
        a = limit_order_scheduler.get_limit_order_scheduler_service()
        limit_order_scheduler._reset_for_tests()
        b = limit_order_scheduler.get_limit_order_scheduler_service()
        assert a is not b


class TestLifecycle:
    def test_start_stop(self):
        svc = limit_order_scheduler.get_limit_order_scheduler_service()
        assert svc._running is False
        svc.start()
        assert svc._running is True
        svc.stop()
        assert svc._running is False

    def test_health(self):
        svc = limit_order_scheduler.get_limit_order_scheduler_service()
        h = svc.health()
        assert h["id"] == "clawmes.limit_order_scheduler"
        assert h["status"] == "stopped"
        svc.start()
        assert svc.health()["status"] == "running"


class TestTick:
    def test_skipped_when_not_running(self, monkeypatch):
        from clawmes.commands import limit_order as mod

        called = {"n": 0}

        def _spy():
            called["n"] += 1
            return 0

        monkeypatch.setattr(mod, "_run_due_sync", _spy)
        svc = limit_order_scheduler.get_limit_order_scheduler_service()
        svc.tick()
        assert called["n"] == 0

    def test_runs_when_started(self, monkeypatch):
        from clawmes.commands import limit_order as mod

        monkeypatch.setattr(mod, "_run_due_sync", lambda: 3)
        svc = limit_order_scheduler.get_limit_order_scheduler_service()
        svc.start()
        svc.tick()
        assert svc._ticks == 1
        assert svc._last_runs == 3
        assert svc._total_runs == 3

    def test_swallows_errors(self, monkeypatch):
        from clawmes.commands import limit_order as mod

        def _boom():
            raise RuntimeError("kaboom")

        monkeypatch.setattr(mod, "_run_due_sync", _boom)
        svc = limit_order_scheduler.get_limit_order_scheduler_service()
        svc.start()
        svc.tick()
        assert svc._ticks == 1
        assert svc._last_runs == 0

    def test_accumulates(self, monkeypatch):
        from clawmes.commands import limit_order as mod

        runs = iter([2, 1, 0])
        monkeypatch.setattr(mod, "_run_due_sync", lambda: next(runs))
        svc = limit_order_scheduler.get_limit_order_scheduler_service()
        svc.start()
        for _ in range(3):
            svc.tick()
        assert svc._total_runs == 3
