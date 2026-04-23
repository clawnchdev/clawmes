"""Tests for clawmes.bridges.installer (ensure_node_bridges)."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock

import pytest

from clawmes.bridges import installer as installer_mod
from clawmes.bridges.installer import (
    BridgePaths,
    _hash_files,
    ensure_node_bridges,
)


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))


class TestNoNode:
    def test_returns_none_paths_when_node_missing(self, monkeypatch):
        monkeypatch.setattr(installer_mod.shutil, "which", lambda *a: None)
        result = ensure_node_bridges()
        assert isinstance(result, BridgePaths)
        assert result.wc_entry is None
        assert result.sa_entry is None


class TestSourcesMissing:
    def test_no_source_dirs_returns_none(self, monkeypatch, tmp_path):
        # Node available, but bridges/sources/{wc,sa} doesn't exist in the
        # tree (we don't ship them yet) → installer returns None paths
        monkeypatch.setattr(installer_mod.shutil, "which", lambda *a: "/usr/bin/node")
        # Re-point _SOURCES_ROOT to an empty tmp dir
        monkeypatch.setattr(installer_mod, "_SOURCES_ROOT", tmp_path / "no-such")

        result = ensure_node_bridges()
        assert result.wc_entry is None
        assert result.sa_entry is None


class TestWithSources:
    def test_full_install_flow(self, monkeypatch, tmp_path):
        """Stage a fake sources tree and verify install runs npm ci and writes
        the install hash."""
        # Stage a fake source dir at SOURCES_ROOT/wc with package.json + lock
        sources = tmp_path / "sources"
        wc_src = sources / "wc"
        wc_src.mkdir(parents=True)
        (wc_src / "package.json").write_text('{"name":"wc"}', encoding="utf-8")
        (wc_src / "package-lock.json").write_text("{}", encoding="utf-8")
        (wc_src / "src").mkdir()
        (wc_src / "src" / "index.ts").write_text("// stub", encoding="utf-8")

        monkeypatch.setattr(installer_mod, "_SOURCES_ROOT", sources)
        monkeypatch.setattr(installer_mod.shutil, "which", lambda *a: "/usr/bin/node")

        # Mock subprocess.run so npm ci doesn't actually execute
        run_mock = MagicMock(return_value=subprocess.CompletedProcess(args=[], returncode=0))
        monkeypatch.setattr(installer_mod.subprocess, "run", run_mock)

        result = ensure_node_bridges()
        # wc had sources → entry path returned, sa missing → None
        assert result.wc_entry is not None
        assert result.sa_entry is None

        # npm ci was called once
        run_mock.assert_called_once()
        cmd = run_mock.call_args.args[0]
        assert cmd[0] == "npm"
        assert "ci" in cmd

        # Install marker written
        marker = tmp_path / "clawmes" / "bridges" / "wc" / ".installed-hash"
        assert marker.exists()
        assert marker.read_text(encoding="utf-8").strip()  # non-empty hash

    def test_skip_install_when_hash_matches(self, monkeypatch, tmp_path):
        """Idempotent: same package.json + lock → don't re-run npm ci."""
        sources = tmp_path / "sources" / "wc"
        sources.mkdir(parents=True)
        (sources / "package.json").write_text('{"name":"wc"}', encoding="utf-8")
        (sources / "package-lock.json").write_text("{}", encoding="utf-8")

        monkeypatch.setattr(installer_mod, "_SOURCES_ROOT", tmp_path / "sources")
        monkeypatch.setattr(installer_mod.shutil, "which", lambda *a: "/usr/bin/node")
        run_mock = MagicMock(return_value=subprocess.CompletedProcess(args=[], returncode=0))
        monkeypatch.setattr(installer_mod.subprocess, "run", run_mock)

        ensure_node_bridges()
        first_calls = run_mock.call_count
        ensure_node_bridges()  # should be a no-op since hash hasn't changed
        assert run_mock.call_count == first_calls

    def test_force_reinstall(self, monkeypatch, tmp_path):
        sources = tmp_path / "sources" / "wc"
        sources.mkdir(parents=True)
        (sources / "package.json").write_text('{"name":"wc"}', encoding="utf-8")
        (sources / "package-lock.json").write_text("{}", encoding="utf-8")

        monkeypatch.setattr(installer_mod, "_SOURCES_ROOT", tmp_path / "sources")
        monkeypatch.setattr(installer_mod.shutil, "which", lambda *a: "/usr/bin/node")
        run_mock = MagicMock(return_value=subprocess.CompletedProcess(args=[], returncode=0))
        monkeypatch.setattr(installer_mod.subprocess, "run", run_mock)

        ensure_node_bridges()
        ensure_node_bridges(force=True)
        assert run_mock.call_count >= 2

    def test_npm_ci_failure_returns_none(self, monkeypatch, tmp_path):
        sources = tmp_path / "sources" / "wc"
        sources.mkdir(parents=True)
        (sources / "package.json").write_text('{"name":"wc"}', encoding="utf-8")
        (sources / "package-lock.json").write_text("{}", encoding="utf-8")

        monkeypatch.setattr(installer_mod, "_SOURCES_ROOT", tmp_path / "sources")
        monkeypatch.setattr(installer_mod.shutil, "which", lambda *a: "/usr/bin/node")

        def boom(*a, **kw):
            raise subprocess.CalledProcessError(1, ["npm"], stderr="error")

        monkeypatch.setattr(installer_mod.subprocess, "run", boom)

        result = ensure_node_bridges()
        assert result.wc_entry is None

    def test_missing_package_files_skips(self, monkeypatch, tmp_path):
        # Source dir exists but package.json missing
        sources = tmp_path / "sources" / "wc"
        sources.mkdir(parents=True)
        # No package.json — just the dir

        monkeypatch.setattr(installer_mod, "_SOURCES_ROOT", tmp_path / "sources")
        monkeypatch.setattr(installer_mod.shutil, "which", lambda *a: "/usr/bin/node")
        run_mock = MagicMock()
        monkeypatch.setattr(installer_mod.subprocess, "run", run_mock)

        result = ensure_node_bridges()
        assert result.wc_entry is None
        assert run_mock.call_count == 0

    def test_copy_tree_skips_node_modules(self, monkeypatch, tmp_path):
        # Source includes node_modules — should not be copied
        sources = tmp_path / "sources" / "wc"
        sources.mkdir(parents=True)
        (sources / "package.json").write_text('{"name":"wc"}', encoding="utf-8")
        (sources / "package-lock.json").write_text("{}", encoding="utf-8")
        nm = sources / "node_modules"
        nm.mkdir()
        (nm / "should-be-skipped.txt").write_text("nope", encoding="utf-8")
        (sources / "src").mkdir()
        (sources / "src" / "index.ts").write_text("// stub", encoding="utf-8")

        monkeypatch.setattr(installer_mod, "_SOURCES_ROOT", tmp_path / "sources")
        monkeypatch.setattr(installer_mod.shutil, "which", lambda *a: "/usr/bin/node")
        monkeypatch.setattr(
            installer_mod.subprocess,
            "run",
            MagicMock(return_value=subprocess.CompletedProcess([], 0)),
        )

        ensure_node_bridges()
        target = tmp_path / "clawmes" / "bridges" / "wc"
        assert (target / "src" / "index.ts").exists()
        assert not (target / "node_modules" / "should-be-skipped.txt").exists()

    def test_subsequent_install_refreshes_source_files(self, monkeypatch, tmp_path):
        """When the hash changes, the installer mirrors source files into the existing target."""
        sources = tmp_path / "sources" / "wc"
        sources.mkdir(parents=True)
        (sources / "package.json").write_text('{"name":"wc","version":"1"}', encoding="utf-8")
        (sources / "package-lock.json").write_text("{}", encoding="utf-8")

        monkeypatch.setattr(installer_mod, "_SOURCES_ROOT", tmp_path / "sources")
        monkeypatch.setattr(installer_mod.shutil, "which", lambda *a: "/usr/bin/node")
        monkeypatch.setattr(
            installer_mod.subprocess,
            "run",
            MagicMock(return_value=subprocess.CompletedProcess([], 0)),
        )

        ensure_node_bridges()

        # Simulate a node_modules already populated in the target
        nm = tmp_path / "clawmes" / "bridges" / "wc" / "node_modules"
        nm.mkdir(exist_ok=True)
        (nm / "preserved.txt").write_text("keep", encoding="utf-8")

        # Bump the lock file so the hash changes
        (sources / "package-lock.json").write_text('{"changed":true}', encoding="utf-8")

        ensure_node_bridges()
        # node_modules content was preserved through the source refresh
        assert (nm / "preserved.txt").exists()


class TestHashHelper:
    def test_hash_files(self, tmp_path):
        a = tmp_path / "a.txt"
        b = tmp_path / "b.txt"
        a.write_text("x")
        b.write_text("y")
        h1 = _hash_files(a, b)
        h2 = _hash_files(a, b)
        assert h1 == h2
        # Changing a file changes the hash
        a.write_text("z")
        assert _hash_files(a, b) != h1
