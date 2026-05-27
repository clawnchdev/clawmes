"""Tests for the copy-trader scheduler service."""

from __future__ import annotations

import pytest

from clawmes.services import copy_trader


@pytest.fixture(autouse=True)
def _reset_singleton():
    copy_trader._reset_for_tests()
    yield
    copy_trader._reset_for_tests()


class TestSingleton:
    def test_same_instance(self):
        a = copy_trader.get_copy_trader_service()
        b = copy_trader.get_copy_trader_service()
        assert a is b

    def test_reset(self):
        a = copy_trader.get_copy_trader_service()
        copy_trader._reset_for_tests()
        b = copy_trader.get_copy_trader_service()
        assert a is not b


class TestLifecycle:
    def test_start_marks_running(self):
        svc = copy_trader.get_copy_trader_service()
        assert svc._running is False
        svc.start()
        assert svc._running is True

    def test_stop_marks_stopped(self):
        svc = copy_trader.get_copy_trader_service()
        svc.start()
        svc.stop()
        assert svc._running is False

    def test_health_shape(self):
        svc = copy_trader.get_copy_trader_service()
        h = svc.health()
        assert h["id"] == "clawmes.copy_trader"
        assert h["status"] == "stopped"
        assert h["ticks"] == 0

    def test_health_running(self):
        svc = copy_trader.get_copy_trader_service()
        svc.start()
        assert svc.health()["status"] == "running"


class TestTick:
    def test_skipped_when_not_running(self, monkeypatch):
        from clawmes.commands import copy as copy_mod

        called = {"n": 0}

        def _spy():
            called["n"] += 1
            return 0

        monkeypatch.setattr(copy_mod, "_run_due_sync", _spy)
        svc = copy_trader.get_copy_trader_service()
        svc.tick()
        assert called["n"] == 0

    def test_runs_when_started(self, monkeypatch):
        from clawmes.commands import copy as copy_mod

        monkeypatch.setattr(copy_mod, "_run_due_sync", lambda: 4)
        svc = copy_trader.get_copy_trader_service()
        svc.start()
        svc.tick()
        assert svc._ticks == 1
        assert svc._last_runs == 4
        assert svc._total_runs == 4

    def test_swallows_errors(self, monkeypatch):
        from clawmes.commands import copy as copy_mod

        def _boom():
            raise RuntimeError("kaboom")

        monkeypatch.setattr(copy_mod, "_run_due_sync", _boom)
        svc = copy_trader.get_copy_trader_service()
        svc.start()
        svc.tick()
        assert svc._ticks == 1
        # Counters didn't advance because the exception preempted them.
        assert svc._last_runs == 0
        assert svc._total_runs == 0

    def test_accumulates(self, monkeypatch):
        from clawmes.commands import copy as copy_mod

        runs = iter([2, 5, 0])
        monkeypatch.setattr(copy_mod, "_run_due_sync", lambda: next(runs))
        svc = copy_trader.get_copy_trader_service()
        svc.start()
        for _ in range(3):
            svc.tick()
        assert svc._ticks == 3
        assert svc._total_runs == 7
        assert svc._last_runs == 0
