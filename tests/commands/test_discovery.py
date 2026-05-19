"""Tests for /skills, /persona, /chains, /tools_list, /safety_status."""

from __future__ import annotations

import pytest

from clawmes.commands import discovery as disc_cmd
from clawmes.services import mode_service as mode_mod
from clawmes.services import persona_service as persona_mod


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(persona_mod, "_instance", None)
    monkeypatch.setattr(mode_mod, "_instance", None)


# --- /skills ------------------------------------------------------------


class TestHandleSkills:
    async def test_lists_bundled_skills(self):
        # The clawmes/skills/ directory ships with the 0.1.0 set.
        out = await disc_cmd.handle_skills("")
        assert "clawmes skill(s) bundled" in out
        # At least one well-known skill from CHANGELOG line 105.
        assert "transfer" in out or "defi-trading" in out

    async def test_empty_skills_dir(self, monkeypatch, tmp_path):
        # When the skills directory exists but contains no SKILL.md files,
        # the command should report "No clawmes skills installed."
        empty_skills = tmp_path / "skills"
        empty_skills.mkdir()
        # Add a non-directory entry to prove iteration handles it.
        (empty_skills / "stray.txt").write_text("ignored", encoding="utf-8")
        # And a directory without SKILL.md.
        (empty_skills / "no_md_here").mkdir()

        from clawmes.commands import discovery

        original_path_cls = discovery.Path

        class StubPath:
            def __init__(self, *args, **kwargs):
                self._real = original_path_cls(*args, **kwargs)

            @property
            def parent(self):
                wrapped = type(self).__new__(type(self))
                wrapped._real = self._real.parent
                return wrapped

            def __truediv__(self, other):
                if other == "skills":
                    return empty_skills
                return self._real / other

            def exists(self):
                return self._real.exists()

        monkeypatch.setattr(discovery, "Path", StubPath)
        out = await disc_cmd.handle_skills("")
        assert "No clawmes skills installed" in out

    async def test_frontmatter_end_marker(self, tmp_path):
        # Cover the "second ---" branch in _read_skill_description.
        skill = tmp_path / "SKILL.md"
        skill.write_text(
            "---\nname: test\n---\n\ndescription: This is in the body, NOT the frontmatter.\n",
            encoding="utf-8",
        )
        # No description in frontmatter → returns "".
        assert disc_cmd._read_skill_description(skill) == ""

    async def test_missing_dir(self, monkeypatch, tmp_path):
        # If the skills_dir doesn't exist, return a clear error.
        from clawmes.commands import discovery

        # Patch Path so .parent.parent / "skills" lands on a nonexistent dir.
        class FakePath:
            def __init__(self, *a, **kw):
                pass

            @property
            def parent(self):
                return self

            def __truediv__(self, other):
                return tmp_path / "definitely-not-here" / other

        monkeypatch.setattr(discovery, "Path", FakePath)
        out = await disc_cmd.handle_skills("")
        assert "No clawmes skills directory found" in out

    async def test_handles_unreadable_file(self, monkeypatch, tmp_path):
        # The _read_skill_description helper must tolerate OSError.
        path = tmp_path / "broken.md"
        # File doesn't exist → read_text raises OSError → returns "".
        assert disc_cmd._read_skill_description(path) == ""

    async def test_reads_frontmatter_description(self, tmp_path):
        skill = tmp_path / "SKILL.md"
        skill.write_text(
            "---\n"
            "name: test-skill\n"
            "description: A test skill for unit testing.\n"
            "tags: [test]\n"
            "---\n\n"
            "# body content\n",
            encoding="utf-8",
        )
        assert disc_cmd._read_skill_description(skill) == "A test skill for unit testing."

    async def test_no_frontmatter_returns_empty(self, tmp_path):
        skill = tmp_path / "SKILL.md"
        skill.write_text("# body only\n", encoding="utf-8")
        assert disc_cmd._read_skill_description(skill) == ""

    async def test_truncates_long_description(self, tmp_path, monkeypatch):
        # Create a fake skill dir + SKILL.md with a >100 char description.
        skills_dir = tmp_path / "skills"
        sub = skills_dir / "test"
        sub.mkdir(parents=True)
        long_desc = "x" * 200
        (sub / "SKILL.md").write_text(f"---\ndescription: {long_desc}\n---\n", encoding="utf-8")
        # Patch the dir resolution.
        import clawmes.commands.discovery as discovery

        original_path = discovery.Path

        class StubPath:
            def __init__(self, *a, **kw):
                self._underlying = original_path(*a, **kw)

            @property
            def parent(self):
                p = type(self).__new__(type(self))
                p._underlying = self._underlying.parent
                return p

            def __truediv__(self, other):
                # Redirect to the tmp skills dir when asking for "skills"
                if other == "skills":
                    return skills_dir
                return self._underlying / other

            def exists(self):
                return self._underlying.exists()

        monkeypatch.setattr(discovery, "Path", StubPath)
        out = await disc_cmd.handle_skills("")
        # Long description should be truncated; the full 200-char string
        # should NOT appear verbatim.
        assert long_desc not in out
        assert "..." in out


# --- /persona -----------------------------------------------------------


class TestHandlePersona:
    async def test_no_active(self):
        out = await disc_cmd.handle_persona("")
        assert "No active persona" in out
        # Should list the choices.
        assert "/degen" in out
        assert "/professional" in out

    async def test_with_active(self):
        persona_mod.get_persona_service().set_persona("degen")
        out = await disc_cmd.handle_persona("")
        assert "Active persona: degen" in out
        assert "Tagline:" in out


# --- /chains ------------------------------------------------------------


class TestHandleChains:
    async def test_lists_chains(self):
        out = await disc_cmd.handle_chains("")
        assert "supported chain(s)" in out
        assert "ethereum" in out
        assert "base" in out
        assert "arbitrum" in out

    async def test_marks_default(self):
        out = await disc_cmd.handle_chains("")
        # The default-chain marker '*' precedes the entry for base (8453).
        # Check that one line contains both the marker and 'base'.
        marker_lines = [ln for ln in out.splitlines() if ln.startswith(" *")]
        assert marker_lines, "Expected at least one line marked with *"
        assert any("base" in ln for ln in marker_lines)


# --- /tools_list --------------------------------------------------------


class TestHandleToolsList:
    async def test_lists_tools_from_manifest(self):
        out = await disc_cmd.handle_tools_list("")
        # The CHANGELOG promises 45 tools at 0.1.0; just verify a few
        # well-known names appear.
        assert "tool(s) declared in plugin.yaml" in out
        assert "transfer" in out
        assert "defi_swap" in out

    async def test_handles_missing_manifest(self, monkeypatch):
        monkeypatch.setattr(disc_cmd, "_read_manifest_tools", lambda: None)
        out = await disc_cmd.handle_tools_list("")
        assert "Could not locate plugin.yaml" in out

    async def test_handles_empty_manifest(self, monkeypatch):
        monkeypatch.setattr(disc_cmd, "_read_manifest_tools", lambda: [])
        out = await disc_cmd.handle_tools_list("")
        assert "No tools declared" in out


class TestReadManifestTools:
    def test_finds_real_manifest(self):
        # The bundled clawmes/plugin.yaml must have provides_tools.
        tools = disc_cmd._read_manifest_tools()
        assert tools is not None
        assert "transfer" in tools

    def test_handles_resource_failure(self, monkeypatch):
        import importlib.resources

        def boom(*args, **kwargs):
            raise OSError("simulated resource failure")

        # Patch the resource accessor to raise.
        class BrokenResource:
            def joinpath(self, *a, **kw):
                raise OSError("nope")

        monkeypatch.setattr(importlib.resources, "files", lambda *a, **kw: BrokenResource())
        assert disc_cmd._read_manifest_tools() is None

    def test_stops_at_next_top_level_key(self, monkeypatch):
        # Simulate a manifest where provides_tools is followed by another
        # top-level key — the parser should NOT include lines past it.
        import importlib.resources

        fake_manifest = "provides_tools:\n  - foo\n  - bar\nprovides_hooks:\n  - pre_tool_call\n"

        class FakeRes:
            def joinpath(self, *a, **kw):
                return self

            def read_text(self, *a, **kw):
                return fake_manifest

        monkeypatch.setattr(importlib.resources, "files", lambda *a, **kw: FakeRes())
        tools = disc_cmd._read_manifest_tools()
        assert tools == ["foo", "bar"]


# --- /safety_status -----------------------------------------------------


class TestHandleSafetyStatus:
    async def test_normal(self):
        # Default mode is "normal".
        out = await disc_cmd.handle_safety_status("")
        assert "NORMAL" in out
        assert "off" in out  # readonly off

    async def test_readonly(self):
        mode_mod.get_mode_service().set_mode("readonly")
        out = await disc_cmd.handle_safety_status("")
        assert "READONLY" in out
        assert "blocked at stage 1" in out

    async def test_danger(self):
        mode_mod.get_mode_service().set_mode("danger")
        out = await disc_cmd.handle_safety_status("")
        assert "DANGER" in out
        assert "BYPASSED" in out


# --- registration -------------------------------------------------------


class TestRegister:
    def test_registers_five_commands(self):
        captured = []

        class FakeCtx:
            def register_command(self, **kw):
                captured.append(kw["name"])

        disc_cmd.register(FakeCtx())
        assert set(captured) == {
            "skills",
            "persona",
            "chains",
            "tools_list",
            "safety_status",
        }
