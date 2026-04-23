"""Tests for ``hermes clawmes version``."""

from __future__ import annotations

import argparse
import sys

from clawmes._version import __version__
from clawmes.cli import version as version_mod


def test_run(capsys):
    rc = version_mod.run(argparse.Namespace())
    assert rc == 0
    out = capsys.readouterr().out
    assert f"clawmes {__version__}" in out
    assert "python" in out
    assert "hermes" in out


def test_run_with_hermes_installed(monkeypatch, capsys):
    """Cover the branch where hermes_agent is importable."""
    fake = type(sys)("hermes_agent")
    fake.__version__ = "2026.4.99"
    monkeypatch.setitem(sys.modules, "hermes_agent", fake)

    rc = version_mod.run(argparse.Namespace())
    assert rc == 0
    out = capsys.readouterr().out
    assert "2026.4.99" in out


def test_run_with_hermes_no_version_attr(monkeypatch, capsys):
    """Cover the ``getattr(..., '(unknown)')`` fallback."""
    fake = type(sys)("hermes_agent")
    monkeypatch.setitem(sys.modules, "hermes_agent", fake)

    rc = version_mod.run(argparse.Namespace())
    assert rc == 0
    out = capsys.readouterr().out
    assert "(unknown)" in out
