"""Tests for ``hermes clawmes doctor`` and ``status``."""

from __future__ import annotations

import argparse
import json

import pytest

from clawmes.cli import doctor as doctor_mod  # noqa: F401  -- ensure module imported
from clawmes.wallet.state import WalletState


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))


@pytest.fixture
def node_available(monkeypatch):
    """Force shutil.which('node') to return a path so Node-available passes."""
    from clawmes.cli import doctor as doc

    monkeypatch.setattr(doc.shutil, "which", lambda *a: "/usr/local/bin/node")
    return None


@pytest.fixture
def hermes_available(monkeypatch):
    """Insert a fake hermes_cli module so the doctor's import check passes."""
    import sys
    import types

    fake = types.ModuleType("hermes_cli")
    monkeypatch.setitem(sys.modules, "hermes_cli", fake)
    return fake


@pytest.fixture
def all_green(node_available, hermes_available):
    """Combined: Node + Hermes both visible."""
    return None


def _ns(**kw):
    base = {"fix": False, "json": False}
    base.update(kw)
    return argparse.Namespace(**base)


class TestRun:
    def test_run_prints_checks(self, capsys, monkeypatch, all_green):
        # Patch wallet state so we hit the connected branch
        from clawmes.cli import doctor as doc

        monkeypatch.setattr(
            doc,
            "get_wallet_state",
            lambda: WalletState.for_chain(mode="walletconnect", address="0xabc", chain_id=8453),
        )
        rc = doc.run(_ns())
        out = capsys.readouterr().out
        assert "Plugin loaded" in out
        assert "Hermes available" in out
        assert "Wallet connected" in out
        # All passing → exit 0
        assert rc == 0

    def test_run_when_disconnected(self, capsys, monkeypatch, all_green):
        from clawmes.cli import doctor as doc

        monkeypatch.setattr(doc, "get_wallet_state", lambda: WalletState.disconnected())
        rc = doc.run(_ns())
        out = capsys.readouterr().out
        assert "no wallet" in out.lower()
        # No fail, only warns → exit 0
        assert rc == 0

    def test_run_with_no_node(self, capsys, monkeypatch):
        from clawmes.cli import doctor as doc

        monkeypatch.setattr(doc.shutil, "which", lambda *a: None)
        monkeypatch.setattr(doc, "get_wallet_state", lambda: WalletState.disconnected())
        rc = doc.run(_ns())
        out = capsys.readouterr().out
        assert "Node.js available" in out
        # Node missing → fail count > 0 → exit 1
        assert rc == 1

    def test_run_with_fix_flag(self, capsys, monkeypatch, all_green):
        from clawmes.cli import doctor as doc

        monkeypatch.setattr(doc, "get_wallet_state", lambda: WalletState.disconnected())
        rc = doc.run(_ns(fix=True))
        out = capsys.readouterr().out
        assert "--fix not yet implemented" in out
        assert rc == 0  # no failures with disconnected wallet (all warns/oks)


class TestRunStatus:
    def test_returns_one_line_human(self, capsys, monkeypatch, all_green):
        from clawmes.cli import doctor as doc

        monkeypatch.setattr(
            doc,
            "get_wallet_state",
            lambda: WalletState.for_chain(mode="walletconnect", address="0xabc", chain_id=8453),
        )
        rc = doc.run_status(_ns())
        out = capsys.readouterr().out
        assert "clawmes" in out
        assert "/" in out  # "X/Y ok"
        assert rc == 0

    def test_json_format(self, capsys, monkeypatch, all_green):
        from clawmes.cli import doctor as doc

        monkeypatch.setattr(
            doc,
            "get_wallet_state",
            lambda: WalletState.for_chain(mode="walletconnect", address="0xabc", chain_id=8453),
        )
        rc = doc.run_status(_ns(json=True))
        out = capsys.readouterr().out
        data = json.loads(out)
        assert "overall" in data
        assert "version" in data
        assert "checks" in data
        assert isinstance(data["checks"], list)
        assert rc == 0

    def test_overall_fail_when_no_node(self, capsys, monkeypatch):
        # Node missing → "fail" → overall = "fail" → return 1
        from clawmes.cli import doctor as doc

        monkeypatch.setattr(doc.shutil, "which", lambda *a: None)
        monkeypatch.setattr(doc, "get_wallet_state", lambda: WalletState.disconnected())
        rc = doc.run_status(_ns(json=True))
        data = json.loads(capsys.readouterr().out)
        assert data["overall"] == "fail"
        assert rc == 1

    def test_status_warn(self, capsys, monkeypatch, all_green):
        # No fail, but a warn (disconnected wallet) → overall = warn, exit 0
        from clawmes.cli import doctor as doc

        monkeypatch.setattr(doc, "get_wallet_state", lambda: WalletState.disconnected())
        rc = doc.run_status(_ns(json=True))
        data = json.loads(capsys.readouterr().out)
        assert data["overall"] == "warn"
        assert rc == 0

    def test_soul_md_present(self, tmp_path, capsys, monkeypatch, all_green):
        # Create SOUL.md so the doctor sees the "ok" branch
        (tmp_path / "SOUL.md").write_text("hi", encoding="utf-8")
        from clawmes.cli import doctor as doc

        monkeypatch.setattr(doc, "get_wallet_state", lambda: WalletState.disconnected())
        doc.run(_ns())
        out = capsys.readouterr().out
        assert "SOUL.md installed" in out

    def test_bridges_present(self, tmp_path, capsys, monkeypatch, all_green):
        # Create the .installed-hash files to hit the green branch
        wc = tmp_path / "clawmes" / "bridges" / "wc"
        sa = tmp_path / "clawmes" / "bridges" / "sa"
        wc.mkdir(parents=True)
        sa.mkdir(parents=True)
        (wc / ".installed-hash").write_text("abc")
        (sa / ".installed-hash").write_text("def")

        from clawmes.cli import doctor as doc

        monkeypatch.setattr(doc, "get_wallet_state", lambda: WalletState.disconnected())
        doc.run(_ns())
        out = capsys.readouterr().out
        # The bridges line should be green (ok)
        assert "Bridges installed" in out
        assert "wc + sa" in out

    def test_hermes_not_importable(self, capsys, monkeypatch, node_available):
        """Cover the ImportError branch in _gather_checks."""
        import sys

        from clawmes.cli import doctor as doc

        # Force `import hermes_cli` to raise inside _gather_checks
        real_import = (
            __builtins__["__import__"]
            if isinstance(__builtins__, dict)
            else __builtins__.__import__
        )

        def fake_import(name, *a, **kw):
            if name == "hermes_cli":
                raise ImportError("simulated")
            return real_import(name, *a, **kw)

        monkeypatch.setitem(sys.modules, "hermes_cli", None)  # nuke any cached entry

        if isinstance(__builtins__, dict):
            monkeypatch.setitem(__builtins__, "__import__", fake_import)
        else:
            monkeypatch.setattr(__builtins__, "__import__", fake_import)

        monkeypatch.setattr(doc, "get_wallet_state", lambda: WalletState.disconnected())
        rc = doc.run(_ns())
        out = capsys.readouterr().out
        assert "Hermes available" in out
        # Hermes missing → fail → rc != 0
        assert rc == 1
