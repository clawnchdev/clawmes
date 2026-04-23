"""Coverage for clawmes/__init__.py — signal handlers and cleanup hook errors."""

from __future__ import annotations

import signal

import pytest

import clawmes


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from clawmes.services.registry import registry

    registry.clear()


class TestCleanupHooks:
    def test_install_signal_handlers_runs(self):
        # Direct invocation — must not raise even though we may not be the main thread
        clawmes._install_signal_handlers()

    def test_signal_handler_value_error_caught(self, monkeypatch):
        """Cover lines 122-125 — signal.signal raises ValueError in non-main thread."""

        def boom(*a, **kw):
            raise ValueError("not in main thread")

        monkeypatch.setattr(signal, "signal", boom)
        # Must not raise
        clawmes._install_signal_handlers()

    def test_signal_handler_invokes_stop_all_and_chains(self, monkeypatch):
        """Cover lines 113-118 — the inner _handler closure."""
        # Capture the handler that gets installed by patching signal.signal
        captured = {}

        original_signal = signal.signal

        def fake_signal(sig, handler):
            captured[sig] = handler
            return original_signal(sig, signal.SIG_DFL)  # don't actually install

        # Track stop_all calls
        stops = []
        from clawmes import services

        monkeypatch.setattr(services, "stop_all", lambda: stops.append(True))
        monkeypatch.setattr(signal, "signal", fake_signal)

        clawmes._install_signal_handlers()

        # We installed handlers for SIGTERM and SIGINT
        assert signal.SIGTERM in captured
        # Invoke the captured handler
        captured[signal.SIGTERM](signal.SIGTERM, None)
        assert stops, "stop_all was not invoked from signal handler"

    def test_signal_handler_chains_to_previous(self, monkeypatch):
        """Cover line 117-118 — previous handler chain when callable."""
        captured = {}

        def fake_signal(sig, handler):
            captured[sig] = handler
            return None

        monkeypatch.setattr(signal, "signal", fake_signal)

        # Pre-set a callable previous handler. _install_signal_handlers
        # captures it via signal.getsignal and re-calls it.
        prev_called = []

        def fake_get(sig):
            return lambda signum, frame: prev_called.append(signum)

        monkeypatch.setattr(signal, "getsignal", fake_get)

        # Don't really stop services
        from clawmes import services

        monkeypatch.setattr(services, "stop_all", lambda: None)

        clawmes._install_signal_handlers()
        captured[signal.SIGTERM](signal.SIGTERM, None)
        assert signal.SIGTERM in prev_called

    def test_register_continues_when_atexit_install_fails(self, monkeypatch, mock_ctx_factory):
        """Cover lines 83-84 — atexit register raises."""
        import atexit

        def boom(*a, **kw):
            raise RuntimeError("simulated atexit failure")

        monkeypatch.setattr(atexit, "register", boom)
        # Must not raise — exception is caught and logged
        clawmes.register(mock_ctx_factory())


@pytest.fixture
def mock_ctx_factory():
    from tests.conftest import FakePluginContext

    def make():
        return FakePluginContext()

    return make
