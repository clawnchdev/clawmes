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
