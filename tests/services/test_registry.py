"""Tests for clawmes.services.registry."""

from __future__ import annotations

import pytest

from clawmes.services._base import Service
from clawmes.services.registry import _Registry, registry, tick_all


class FakeService(Service):
    def __init__(self, sid="test", ticking=False, fail_tick=False):
        self._id = sid
        self._ticking = ticking
        self._fail_tick = fail_tick
        self.tick_count = 0
        self.started = False
        self.stopped = False

    @property
    def id(self):
        return self._id

    @property
    def ticking(self):
        return self._ticking

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def tick(self):
        self.tick_count += 1
        if self._fail_tick:
            raise RuntimeError("simulated tick failure")


@pytest.fixture
def fresh_registry():
    """Provide a fresh registry instance scoped to the test."""
    return _Registry()


class TestRegister:
    def test_register_and_get(self, fresh_registry):
        svc = FakeService(sid="alpha")
        fresh_registry.register(svc)
        assert fresh_registry.get("alpha") is svc

    def test_get_missing_returns_none(self, fresh_registry):
        assert fresh_registry.get("nonexistent") is None

    def test_double_register_skipped(self, fresh_registry):
        svc1 = FakeService(sid="dup")
        svc2 = FakeService(sid="dup")
        fresh_registry.register(svc1)
        fresh_registry.register(svc2)
        # First registration wins
        assert fresh_registry.get("dup") is svc1

    def test_empty_id_raises(self, fresh_registry):
        bad = FakeService(sid="")
        with pytest.raises(ValueError, match="empty id"):
            fresh_registry.register(bad)

    def test_iter_services(self, fresh_registry):
        a = FakeService(sid="a")
        b = FakeService(sid="b")
        fresh_registry.register(a)
        fresh_registry.register(b)
        ids = {svc.id for svc in fresh_registry.iter_services()}
        assert ids == {"a", "b"}

    def test_clear(self, fresh_registry):
        fresh_registry.register(FakeService(sid="x"))
        fresh_registry.clear()
        assert list(fresh_registry.iter_services()) == []


class TestTickAll:
    @pytest.fixture(autouse=True)
    def _isolate_global_registry(self):
        registry.clear()
        yield
        registry.clear()

    def test_tick_calls_only_ticking_services(self):
        a = FakeService(sid="ticker", ticking=True)
        b = FakeService(sid="non-ticker", ticking=False)
        registry.register(a)
        registry.register(b)

        tick_all()

        assert a.tick_count == 1
        assert b.tick_count == 0

    def test_tick_failure_does_not_propagate(self):
        a = FakeService(sid="failing", ticking=True, fail_tick=True)
        b = FakeService(sid="working", ticking=True)
        registry.register(a)
        registry.register(b)

        # Should not raise even though `a.tick()` raises
        tick_all()
        # But the working service still ticked
        assert b.tick_count == 1

    def test_tick_with_no_services_is_noop(self):
        tick_all()  # must not raise
