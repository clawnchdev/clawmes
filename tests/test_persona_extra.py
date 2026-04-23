"""Extra coverage for clawmes.persona — bundled SOUL.md missing branch."""

from __future__ import annotations

import pytest

from clawmes import persona


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))


def test_ensure_soul_md_with_missing_bundled(monkeypatch, tmp_path):
    """Cover lines 50-51: bundled SOUL.md doesn't exist on disk."""
    monkeypatch.setattr(persona, "_BUNDLED_SOUL", tmp_path / "no-such-bundle.md")
    persona.ensure_soul_md()
    # Target was NOT created, no exception raised
    assert not (tmp_path / "SOUL.md").exists()


def test_reinstall_with_missing_bundled(monkeypatch, tmp_path):
    """force=True still tries to copy; missing bundled file raises FileNotFoundError."""
    monkeypatch.setattr(persona, "_BUNDLED_SOUL", tmp_path / "ghost.md")
    # Pre-create so we hit the `force=True` path
    (tmp_path / "SOUL.md").write_text("user", encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        persona.reinstall_soul_md(force=True)
