"""Tests for clawmes.bridges.process.

Unit tests use a fake Popen so the suite stays fast and doesn't require
Node. The integration test (``tests/bridges/test_process_integration.py``)
exercises the real subprocess and is gated on ``RUN_BRIDGE_INTEGRATION=1``.
"""

from __future__ import annotations

import io
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from clawmes.bridges.process import (
    BridgeError,
    BridgeProcess,
    Notification,
)

# --- Fakes ----------------------------------------------------------------


class FakeStream:
    """File-like that supports threaded readline + close."""

    def __init__(self) -> None:
        self._buffer: list[str] = []
        self._lock = threading.Condition()
        self._closed = False

    def feed(self, line: str) -> None:
        with self._lock:
            self._buffer.append(line)
            self._lock.notify_all()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._lock.notify_all()

    def readline(self) -> str:
        with self._lock:
            while not self._buffer and not self._closed:
                self._lock.wait()
            if self._buffer:
                return self._buffer.pop(0)
            return ""  # EOF

    # Python's file iterators rely on __iter__/__next__ + readline
    def __iter__(self):
        return self

    def __next__(self):
        line = self.readline()
        if not line:
            raise StopIteration
        return line


class FakePopen:
    """Drop-in for subprocess.Popen with fake stdin/stdout/stderr."""

    def __init__(self) -> None:
        self.stdin = io.StringIO()
        self.stdout = FakeStream()
        self.stderr = FakeStream()
        self._returncode: int | None = None
        self._terminated = False

    def poll(self) -> int | None:
        return self._returncode

    def terminate(self) -> None:
        self._terminated = True
        self._returncode = 0

    def kill(self) -> None:
        self._returncode = -9

    def wait(self, timeout: float | None = None) -> int:  # noqa: ARG002
        return self._returncode if self._returncode is not None else 0

    # Helpers for tests
    def push_response(self, line: str) -> None:
        self.stdout.feed(line + "\n")

    def close_stdout(self) -> None:
        self.stdout.close()


@pytest.fixture
def fake_proc(monkeypatch):
    """Patch subprocess.Popen to return a FakePopen + return both."""
    fake = FakePopen()

    def fake_popen(*args: Any, **kwargs: Any) -> FakePopen:
        return fake

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    return fake


@pytest.fixture
def proc():
    return BridgeProcess(name="test", entry=Path("/fake/entry.mjs"))


# --- Lifecycle ------------------------------------------------------------


class TestStartStop:
    def test_start_spawns_subprocess(self, proc, fake_proc):
        proc.start()
        assert proc.is_running() is True
        proc.stop()

    def test_start_idempotent(self, proc, fake_proc):
        proc.start()
        proc.start()  # second call no-op while still running
        assert proc.is_running() is True
        proc.stop()

    def test_stop_when_not_started(self, proc):
        proc.stop()  # safe

    def test_stop_terminates_running_process(self, proc, fake_proc):
        proc.start()
        proc.stop()
        assert fake_proc._terminated is True

    def test_stop_force_kills_on_timeout(self, proc, monkeypatch):
        # terminate() doesn't actually exit the process; wait() times out;
        # then kill() runs and sets returncode to -9.
        class HangingPopen(FakePopen):
            def terminate(self):
                self._terminated = True  # don't set returncode

            def wait(self, timeout=None):
                if self._returncode is None:
                    raise subprocess.TimeoutExpired("test", timeout or 0)
                return self._returncode

        hp = HangingPopen()
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: hp)
        proc.start()
        proc.stop()
        assert hp._returncode == -9

    def test_stop_kill_timeout_swallowed(self, proc, monkeypatch):
        # terminate fails to exit; kill is called but ALSO doesn't return
        # within the second wait — verify the second TimeoutExpired
        # is caught and stop() returns cleanly.
        class TotallyHangingPopen(FakePopen):
            def terminate(self):
                self._terminated = True  # don't set returncode

            def kill(self):
                pass  # don't set returncode either — pretend kill is broken

            def wait(self, timeout=None):
                if self._returncode is None:
                    raise subprocess.TimeoutExpired("test", timeout or 0)
                return self._returncode

        hp = TotallyHangingPopen()
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: hp)
        proc.start()
        # Should not raise even though both waits time out
        proc.stop()


# --- Call / response ------------------------------------------------------


class TestCall:
    def test_raises_when_not_running(self, proc):
        with pytest.raises(BridgeError) as exc_info:
            proc.call("anything", {})
        assert exc_info.value.code == "not_running"

    def test_basic_round_trip(self, proc, fake_proc):
        proc.start()

        # Background thread that observes stdin and writes a matching
        # response to stdout.
        def responder():
            time.sleep(0.05)
            payload = fake_proc.stdin.getvalue().strip()
            assert payload  # the call() wrote a request
            import json

            req = json.loads(payload)
            fake_proc.push_response(json.dumps({"id": req["id"], "result": {"version": "test"}}))

        t = threading.Thread(target=responder, daemon=True)
        t.start()

        result = proc.call("health", {}, timeout=2.0)
        assert result == {"version": "test"}
        proc.stop()

    def test_error_response_raises(self, proc, fake_proc):
        proc.start()

        def responder():
            time.sleep(0.05)
            payload = fake_proc.stdin.getvalue().strip()
            import json

            req = json.loads(payload)
            fake_proc.push_response(
                json.dumps(
                    {
                        "id": req["id"],
                        "error": {"code": "boom", "message": "kaboom", "data": {"x": 1}},
                    }
                )
            )

        threading.Thread(target=responder, daemon=True).start()

        with pytest.raises(BridgeError) as exc_info:
            proc.call("anything", {}, timeout=2.0)
        assert exc_info.value.code == "boom"
        assert exc_info.value.data == {"x": 1}
        proc.stop()

    def test_non_dict_error_field(self, proc, fake_proc):
        proc.start()

        def responder():
            time.sleep(0.05)
            payload = fake_proc.stdin.getvalue().strip()
            import json

            req = json.loads(payload)
            fake_proc.push_response(json.dumps({"id": req["id"], "error": "string-not-dict"}))

        threading.Thread(target=responder, daemon=True).start()
        with pytest.raises(BridgeError) as exc_info:
            proc.call("any", {}, timeout=2.0)
        assert exc_info.value.code == "unknown"
        proc.stop()

    def test_timeout_raises(self, proc, fake_proc):
        proc.start()
        with pytest.raises(BridgeError) as exc_info:
            proc.call("never-responds", {}, timeout=0.1)
        assert exc_info.value.code == "timeout"
        proc.stop()

    def test_write_failure_raises(self, proc, fake_proc):
        proc.start()
        # Replace stdin with one that raises on write
        broken = io.StringIO()

        def fail_write(*a, **kw):
            raise BrokenPipeError("simulated")

        broken.write = fail_write  # type: ignore[method-assign]
        fake_proc.stdin = broken
        with pytest.raises(BridgeError) as exc_info:
            proc.call("any", {})
        assert exc_info.value.code == "write_failed"
        proc.stop()

    def test_stop_fails_pending_calls(self, proc, fake_proc):
        proc.start()

        # Caller blocks waiting for response
        results: dict[str, Any] = {}

        def caller():
            try:
                proc.call("never-resolves", {}, timeout=10.0)
            except BridgeError as exc:
                results["error"] = exc

        t = threading.Thread(target=caller, daemon=True)
        t.start()
        time.sleep(0.05)  # let caller register
        proc.stop()
        t.join(timeout=2)
        assert "error" in results
        assert results["error"].code == "bridge_stopped"


# --- Reader behavior ------------------------------------------------------


class TestReader:
    def test_response_for_unknown_id_logged_not_raised(self, proc, fake_proc, caplog):
        proc.start()
        # Push a response with no matching pending call
        import json

        fake_proc.push_response(json.dumps({"id": "unknown-uuid", "result": "x"}))
        time.sleep(0.05)
        # No exception, no crash
        assert proc.is_running()
        proc.stop()

    def test_malformed_line_skipped(self, proc, fake_proc):
        proc.start()
        fake_proc.push_response("not json at all")
        time.sleep(0.05)
        assert proc.is_running()
        proc.stop()

    def test_non_dict_payload_skipped(self, proc, fake_proc):
        proc.start()
        fake_proc.push_response('"a string, not an object"')
        time.sleep(0.05)
        assert proc.is_running()
        proc.stop()

    def test_empty_line_skipped(self, proc, fake_proc):
        # Cover the `if not line: continue` branch in the reader.
        proc.start()
        fake_proc.push_response("")  # empty line
        fake_proc.push_response("   ")  # whitespace-only
        time.sleep(0.05)
        assert proc.is_running()
        proc.stop()

    def test_stdout_close_fails_pending_calls(self, proc, fake_proc):
        proc.start()
        results: dict[str, Any] = {}

        def caller():
            try:
                proc.call("waiting", {}, timeout=10.0)
            except BridgeError as exc:
                results["error"] = exc

        t = threading.Thread(target=caller, daemon=True)
        t.start()
        time.sleep(0.05)
        # Close stdout from underneath — simulates bridge crash
        fake_proc.close_stdout()
        t.join(timeout=2)
        assert "error" in results
        assert results["error"].code == "bridge_crashed"
        proc.stop()


class TestReaderDefensiveBranches:
    def test_read_stdout_returns_when_proc_none(self, proc):
        # Cover the early-return branch — call _read_stdout directly
        # with proc not started.
        proc._read_stdout()  # should not raise

    def test_read_stderr_returns_when_proc_none(self, proc):
        proc._read_stderr()  # should not raise


class TestStderrCapture:
    def test_stderr_lines_logged_as_warnings(self, proc, fake_proc):
        # The clawmes logger has propagate=False, so caplog can't catch it
        # automatically. Attach a list-handler directly.
        import logging

        captured: list[str] = []

        class ListHandler(logging.Handler):
            def emit(self, record):
                captured.append(record.getMessage())

        h = ListHandler()
        clawmes_logger = logging.getLogger("clawmes.bridges.process")
        clawmes_logger.addHandler(h)
        try:
            proc.start()
            fake_proc.stderr.feed("simulated bridge stderr line\n")
            time.sleep(0.1)
            assert any("simulated bridge stderr" in m for m in captured)
            proc.stop()
        finally:
            clawmes_logger.removeHandler(h)


# --- Notifications --------------------------------------------------------


class TestNotifications:
    def test_initial_queue_empty(self, proc):
        assert proc.notifications().empty()

    def test_notification_enqueued(self, proc, fake_proc):
        proc.start()
        import json

        fake_proc.push_response(
            json.dumps({"method": "pairing_approved", "params": {"address": "0xabc"}})
        )
        # Wait for reader to process
        notif = proc.notifications().get(timeout=2)
        assert isinstance(notif, Notification)
        assert notif.method == "pairing_approved"
        assert notif.params == {"address": "0xabc"}
        proc.stop()

    def test_notification_missing_method_skipped(self, proc, fake_proc):
        proc.start()
        import json

        fake_proc.push_response(json.dumps({"params": {}}))
        time.sleep(0.05)
        assert proc.notifications().empty()
        proc.stop()

    def test_notification_no_params_defaults_empty(self, proc, fake_proc):
        proc.start()
        import json

        fake_proc.push_response(json.dumps({"method": "ping"}))
        notif = proc.notifications().get(timeout=2)
        assert notif.method == "ping"
        assert notif.params == {}
        proc.stop()

    def test_notification_non_dict_params_coerced_empty(self, proc, fake_proc):
        proc.start()
        import json

        fake_proc.push_response(json.dumps({"method": "ping", "params": "not-a-dict"}))
        notif = proc.notifications().get(timeout=2)
        assert notif.params == {}
        proc.stop()


# --- BridgeError ---------------------------------------------------------


class TestBridgeError:
    def test_carries_data(self):
        e = BridgeError("custom_code", "msg", data={"x": 1})
        assert e.code == "custom_code"
        assert e.data == {"x": 1}
        assert str(e) == "msg"

    def test_default_data_none(self):
        e = BridgeError("code", "msg")
        assert e.data is None
