"""Tests for clawmes.services.__init__ start_all / stop_all behavior."""

from __future__ import annotations

import pytest

from clawmes.services import start_all, stop_all
from clawmes.services._base import Service
from clawmes.services.registry import registry


class FailingStartService(Service):
    id = "test.failing-start"

    def __init__(self):
        self.start_called = False
        self.stop_called = False

    def start(self):
        self.start_called = True
        raise RuntimeError("start crashed")

    def stop(self):
        self.stop_called = True


class FailingStopService(Service):
    id = "test.failing-stop"

    def __init__(self):
        self.stopped = False

    def start(self):
        pass

    def stop(self):
        self.stopped = True
        raise RuntimeError("stop crashed")


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    registry.clear()
    yield
    registry.clear()


def test_start_all_continues_past_service_exception(monkeypatch):
    """If one service's start() raises, start_all logs and moves on."""
    # Pre-register a failing service so start_all sees it during registry iter.
    failing = FailingStartService()
    registry.register(failing)
    failing.start  # noqa: B018  -- ensure attr access works

    # Patch the factories list inside start_all to include our failing service
    # by inserting it into the registry pre-call. start_all will skip its
    # factory-based setup but the failure path is exercised by the patches
    # below — easier route: monkey-patch one of the real services' start().

    # Cleanest: patch get_wallet_service to return a service whose start() raises.
    def _get_failing_wallet():
        return failing

    monkeypatch.setattr("clawmes.services.wallet.get_wallet_service", _get_failing_wallet)

    # start_all uses the failing wallet service first → exception is logged
    # and the rest still get started.
    start_all()
    assert failing.start_called
    # Other services should still be in the registry
    ids = {svc.id for svc in registry.iter_services()}
    assert "clawmes.coingecko" in ids


def test_stop_all_continues_past_service_exception():
    """If one service's stop() raises, stop_all logs and moves on to the rest."""
    a = FailingStopService()
    b = FailingStopService()
    b.id = "test.also-failing"
    registry.register(a)
    registry.register(b)

    stop_all()
    # Both stops were attempted despite the first one raising
    assert a.stopped and b.stopped


def test_stop_all_empty_registry():
    """No services → no-op."""
    stop_all()  # must not raise
