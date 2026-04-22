"""Tests for clawmes.persona — idempotent SOUL.md install."""

from __future__ import annotations

import pytest

from clawmes import persona


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))


def test_ensure_creates_when_absent(tmp_path):
    target = tmp_path / "SOUL.md"
    assert not target.exists()
    persona.ensure_soul_md()
    assert target.exists()
    content = target.read_text(encoding="utf-8")
    assert "Clawmes" in content


def test_ensure_does_not_overwrite_user_edits(tmp_path):
    target = tmp_path / "SOUL.md"
    target.write_text("user-edited content\n", encoding="utf-8")
    persona.ensure_soul_md()
    # Untouched
    assert target.read_text(encoding="utf-8") == "user-edited content\n"


def test_ensure_writes_install_marker(tmp_path):
    persona.ensure_soul_md()
    marker = tmp_path / ".clawmes-soul-installed"
    assert marker.exists()
    assert marker.read_text(encoding="utf-8").strip()  # contains version


def test_ensure_does_not_write_marker_if_skipped(tmp_path):
    target = tmp_path / "SOUL.md"
    target.write_text("user-edited\n", encoding="utf-8")
    persona.ensure_soul_md()
    # We didn't install → no marker
    marker = tmp_path / ".clawmes-soul-installed"
    assert not marker.exists()


def test_ensure_idempotent(tmp_path):
    persona.ensure_soul_md()
    first = (tmp_path / "SOUL.md").read_text(encoding="utf-8")
    persona.ensure_soul_md()
    second = (tmp_path / "SOUL.md").read_text(encoding="utf-8")
    assert first == second


def test_reinstall_overwrites_only_with_force(tmp_path):
    target = tmp_path / "SOUL.md"
    target.write_text("user content\n", encoding="utf-8")

    # No force → preserved, returns False
    assert persona.reinstall_soul_md() is False
    assert target.read_text(encoding="utf-8") == "user content\n"

    # Force → overwritten, returns True
    assert persona.reinstall_soul_md(force=True) is True
    assert "Clawmes" in target.read_text(encoding="utf-8")


def test_soul_target_uses_hermes_home(tmp_path):
    assert persona.soul_target() == tmp_path / "SOUL.md"


def test_install_marker_path(tmp_path):
    assert persona.install_marker() == tmp_path / ".clawmes-soul-installed"
