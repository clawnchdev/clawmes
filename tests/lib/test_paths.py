"""Tests for clawmes.lib.paths."""

from __future__ import annotations

from pathlib import Path

import pytest

from clawmes.lib import paths as paths_mod


@pytest.fixture(autouse=True)
def _reset(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))


class TestHermesHome:
    def test_with_hermes_constants(self, monkeypatch, tmp_path):
        """Cover the get_hermes_home import branch."""
        import sys
        import types

        fake = types.ModuleType("hermes_constants")
        fake.get_hermes_home = lambda: tmp_path / "via-hermes-constants"
        monkeypatch.setitem(sys.modules, "hermes_constants", fake)
        assert paths_mod.hermes_home() == tmp_path / "via-hermes-constants"

    def test_falls_back_to_env_when_hermes_constants_missing(self, monkeypatch, tmp_path):
        # Default fixture path: hermes_constants not importable, env var set
        import sys

        monkeypatch.delitem(sys.modules, "hermes_constants", raising=False)
        # Make the import truly fail
        real_import = (
            __builtins__["__import__"]
            if isinstance(__builtins__, dict)
            else __builtins__.__import__
        )

        def fake_import(name, *a, **kw):
            if name == "hermes_constants":
                raise ImportError("simulated")
            return real_import(name, *a, **kw)

        if isinstance(__builtins__, dict):
            monkeypatch.setitem(__builtins__, "__import__", fake_import)
        else:
            monkeypatch.setattr(__builtins__, "__import__", fake_import)

        assert paths_mod.hermes_home() == Path(str(tmp_path))

    def test_falls_back_to_default_when_env_unset(self, monkeypatch):
        import sys

        monkeypatch.delitem(sys.modules, "hermes_constants", raising=False)
        monkeypatch.delenv("HERMES_HOME", raising=False)

        real_import = (
            __builtins__["__import__"]
            if isinstance(__builtins__, dict)
            else __builtins__.__import__
        )

        def fake_import(name, *a, **kw):
            if name == "hermes_constants":
                raise ImportError("simulated")
            return real_import(name, *a, **kw)

        if isinstance(__builtins__, dict):
            monkeypatch.setitem(__builtins__, "__import__", fake_import)
        else:
            monkeypatch.setattr(__builtins__, "__import__", fake_import)

        result = paths_mod.hermes_home()
        assert result == Path.home() / ".hermes"


class TestSubDirs:
    def test_clawmes_root_creates(self, tmp_path):
        result = paths_mod.clawmes_root()
        assert result == tmp_path / "clawmes"
        assert result.exists()

    def test_state_dir_creates_nested(self, tmp_path):
        result = paths_mod.state_dir("foo", "bar")
        assert result == tmp_path / "clawmes" / "foo" / "bar"
        assert result.exists()

    def test_logs_dir(self, tmp_path):
        assert paths_mod.logs_dir() == tmp_path / "clawmes" / "logs"

    def test_plans_dir(self, tmp_path):
        assert paths_mod.plans_dir() == tmp_path / "clawmes" / "plans"

    def test_ledger_dir(self, tmp_path):
        assert paths_mod.ledger_dir() == tmp_path / "clawmes" / "ledger"

    def test_policy_dir(self, tmp_path):
        assert paths_mod.policy_dir() == tmp_path / "clawmes" / "policy"

    def test_wallet_dir(self, tmp_path):
        assert paths_mod.wallet_dir() == tmp_path / "clawmes" / "wallet"

    def test_bridges_dir(self, tmp_path):
        assert paths_mod.bridges_dir() == tmp_path / "clawmes" / "bridges"


class TestDisplayPath:
    def test_under_hermes_home(self, tmp_path):
        p = tmp_path / "clawmes" / "logs" / "clawmes.log"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()
        assert paths_mod.display_path(p) == "$HERMES_HOME/clawmes/logs/clawmes.log"

    def test_outside_hermes_home(self):
        external = Path("/tmp/some/random/path.log")
        assert paths_mod.display_path(external) == "/tmp/some/random/path.log"
