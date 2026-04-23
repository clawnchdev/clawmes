"""Tests for clawmes.bridges.process.

The actual subprocess spawn is stubbed at this milestone — start() is a
no-op assignment, call() raises BridgeError(not_implemented). We pin the
public API and the notification queue / parser behavior so callers can
wire the real impl in later without surprise.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from clawmes.bridges.process import BridgeError, BridgeProcess, Notification


@pytest.fixture
def proc():
    return BridgeProcess(name="test", entry=Path("/fake/entry.mjs"))


class TestStartStop:
    def test_start_idempotent(self, proc):
        # start() with no real subprocess just sets self._proc = None
        proc.start()
        proc.start()  # safe to call again
        assert proc._proc is None

    def test_stop_when_not_started(self, proc):
        # No subprocess to terminate — must not raise
        proc.stop()

    def test_stop_kills_running_process(self, proc, monkeypatch):
        # Stand up a fake process that "is running" then dies on terminate
        class FakeProc:
            def __init__(self):
                self._terminated = False

            def poll(self):
                return None if not self._terminated else 0

            def terminate(self):
                self._terminated = True

            def wait(self, timeout=None):
                return 0

            def kill(self):
                self._terminated = True

        proc._proc = FakeProc()
        proc.stop()
        assert proc._proc is None

    def test_stop_force_kills_on_timeout(self, proc):
        class HangingProc:
            def poll(self):
                return None

            def terminate(self):
                pass

            def wait(self, timeout=None):
                raise subprocess.TimeoutExpired("test", 5)

            def kill(self):
                self._killed = True

        hp = HangingProc()
        proc._proc = hp
        proc.stop()
        assert getattr(hp, "_killed", False) is True
        assert proc._proc is None


class TestCall:
    def test_raises_bridge_error(self, proc):
        with pytest.raises(BridgeError) as exc_info:
            proc.call("any", {})
        assert exc_info.value.code == "not_implemented"

    def test_bridge_error_carries_data(self):
        e = BridgeError("custom_code", "msg", data={"x": 1})
        assert e.code == "custom_code"
        assert e.data == {"x": 1}
        assert str(e) == "msg"


class TestNotifications:
    def test_initial_queue_empty(self, proc):
        q = proc.notifications()
        assert q.empty()

    def test_enqueue_valid_notification(self, proc):
        proc._enqueue_notification('{"method": "pairing_approved", "params": {"address": "0xabc"}}')
        q = proc.notifications()
        n = q.get_nowait()
        assert isinstance(n, Notification)
        assert n.method == "pairing_approved"
        assert n.params == {"address": "0xabc"}

    def test_enqueue_response_skipped(self, proc):
        # Response (has id) — not a notification, skip
        proc._enqueue_notification('{"id": "xyz", "result": {}}')
        assert proc.notifications().empty()

    def test_enqueue_malformed_logged_not_raised(self, proc):
        # Bad JSON — logs warning, doesn't raise
        proc._enqueue_notification("not-json")
        assert proc.notifications().empty()

    def test_enqueue_missing_method(self, proc):
        # Missing method key — KeyError caught
        proc._enqueue_notification('{"params": {}}')
        assert proc.notifications().empty()

    def test_enqueue_no_params(self, proc):
        # No params field — defaults to {}
        proc._enqueue_notification('{"method": "ping"}')
        n = proc.notifications().get_nowait()
        assert n.method == "ping"
        assert n.params == {}
