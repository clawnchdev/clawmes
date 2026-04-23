"""Tests for clawmes.lib.logger."""

from __future__ import annotations

import logging

import pytest

from clawmes.lib import logger as logger_mod


@pytest.fixture(autouse=True)
def _reset_initialized(tmp_path, monkeypatch):
    """Reset the module-level _INITIALIZED flag and HERMES_HOME."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    # Reset initialization between tests so the file handler is created fresh
    monkeypatch.setattr(logger_mod, "_INITIALIZED", False)
    # Clear handlers so we don't accumulate across tests
    root = logging.getLogger("clawmes")
    for h in list(root.handlers):
        root.removeHandler(h)


class TestLoggerFor:
    def test_returns_child_logger(self, tmp_path):
        log = logger_mod.logger_for("test")
        assert log.name == "clawmes.test"

    def test_initialization_creates_file_handler(self, tmp_path):
        log = logger_mod.logger_for("test")
        log.info("hello")
        # Log file written
        assert (tmp_path / "clawmes" / "logs" / "clawmes.log").exists()

    def test_initialization_idempotent(self):
        # Calling logger_for multiple times only initializes once
        logger_mod.logger_for("a")
        handlers_after_first = list(logging.getLogger("clawmes").handlers)
        logger_mod.logger_for("b")
        handlers_after_second = list(logging.getLogger("clawmes").handlers)
        assert len(handlers_after_first) == len(handlers_after_second)

    def test_readonly_filesystem_falls_back_to_stderr_only(self, monkeypatch, tmp_path):
        """Cover lines 48-50: OSError opening the file handler."""
        # Patch FileHandler so it raises OSError to simulate read-only fs
        original = logging.FileHandler

        class FailingFileHandler:
            def __init__(self, *a, **kw):
                raise OSError("read-only filesystem")

        monkeypatch.setattr(logging, "FileHandler", FailingFileHandler)
        try:
            # Should not raise; just skip the file handler
            log = logger_mod.logger_for("test")
            log.info("still works")
        finally:
            monkeypatch.setattr(logging, "FileHandler", original)


class TestAddFileHandler:
    def test_attaches_handler(self, tmp_path):
        target = tmp_path / "extra.log"
        logger_mod.add_file_handler("custom", target)
        log = logging.getLogger("clawmes.custom")
        log.error("test message")
        # Force flush
        for h in log.handlers:
            h.flush()
        assert target.exists()
        content = target.read_text(encoding="utf-8")
        assert "test message" in content

    def test_with_explicit_level(self, tmp_path):
        target = tmp_path / "warn.log"
        logger_mod.add_file_handler("custom2", target, level=logging.WARNING)
        log = logging.getLogger("clawmes.custom2")
        log.debug("ignored")
        log.warning("captured")
        for h in log.handlers:
            h.flush()
        content = target.read_text(encoding="utf-8")
        assert "captured" in content
        assert "ignored" not in content
