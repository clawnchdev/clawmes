"""Tests for ``hermes clawmes init`` (skeleton)."""

from __future__ import annotations

import argparse

from clawmes.cli import init as init_mod


def _ns(**kwargs):
    base = {
        "reconfigure": False,
        "skip_wallet": False,
        "check": False,
        "non_interactive": False,
    }
    base.update(kwargs)
    return argparse.Namespace(**base)


def test_run_returns_zero(capsys):
    rc = init_mod.run(_ns())
    assert rc == 0
    out = capsys.readouterr().out
    assert "Welcome to clawmes" in out
    assert "Setup wizard not yet implemented" in out


def test_check_flag_mentioned(capsys):
    init_mod.run(_ns(check=True))
    out = capsys.readouterr().out
    assert "--check" in out or "dry-run" in out


def test_non_interactive_flag_mentioned(capsys):
    init_mod.run(_ns(non_interactive=True))
    out = capsys.readouterr().out
    assert "non-interactive" in out


def test_no_flags_no_special_mention(capsys):
    init_mod.run(_ns())
    out = capsys.readouterr().out
    assert "dry-run" not in out
