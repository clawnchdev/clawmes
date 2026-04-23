"""Final coverage gap-fills for assorted modules.

This file collects single-line + small-edge-case tests for modules where
the bulk of behavior is already covered elsewhere but a few stray
branches or methods are uncovered. Grouped here to avoid creating tiny
one-off test files.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ── lib/addr ──────────────────────────────────────────────────────────────


class TestAddrZeroAddress:
    def test_is_zero_address_returns_true(self):
        from clawmes.lib.addr import ZERO_ADDRESS, is_zero_address

        # Cover line 39 — is_hex_address True branch + zero match
        assert is_zero_address(ZERO_ADDRESS)

    def test_to_checksum_via_eth_utils(self):
        # Cover line 53 — eth_utils import success path with a valid address
        from clawmes.lib.addr import to_checksum

        addr = "0x4200000000000000000000000000000000000006"  # WETH on Base
        result = to_checksum(addr)
        # eth_utils returns the EIP-55 checksummed form
        assert result.lower() == addr.lower()


# ── lib/params ────────────────────────────────────────────────────────────


class TestParamsFloatEdges:
    def test_read_float_empty_optional_returns_none(self):
        # Cover lines 56-58 — empty string with required=False
        from clawmes.lib.params import read_float

        assert read_float({"a": ""}, "a") is None

    def test_read_float_empty_required_raises(self):
        from clawmes.lib.params import ParamError, read_float

        with pytest.raises(ParamError):
            read_float({"a": ""}, "a", required=True)

    def test_read_float_missing_required_raises(self):
        from clawmes.lib.params import ParamError, read_float

        with pytest.raises(ParamError):
            read_float({}, "a", required=True)


# ── plans/validator ───────────────────────────────────────────────────────


class TestValidatorUnknownStep:
    def test_unknown_step_kind_raises(self):
        # Cover line 94 — _check_steps catches non-IR step types
        from clawmes.plans.ir import Plan
        from clawmes.plans.validator import validate_plan

        # Slip in a non-IR object as a "step"
        bad_plan = Plan(plan_id="p", description="d", steps=[object()])  # type: ignore[list-item]
        report = validate_plan(bad_plan)
        assert not report.ok
        assert any("unknown step kind" in e.lower() for e in report.errors)


# ── services/_base ────────────────────────────────────────────────────────


class TestServiceBaseHealth:
    def test_default_health(self):
        # Cover line 44 — Service.health() default impl
        from clawmes.services._base import Service

        class Concrete(Service):
            id = "test.concrete"

            def start(self):
                pass

            def stop(self):
                pass

        h = Concrete().health()
        assert h["id"] == "test.concrete"
        assert h["status"] == "unknown"


# ── services/coingecko singleton ──────────────────────────────────────────


class TestCoinGeckoSingletonInit:
    def test_singleton_initialized_when_missing(self, monkeypatch):
        # Cover line 133 — the `if _instance is None` branch
        from clawmes.services import coingecko as cg_mod

        monkeypatch.setattr(cg_mod, "_instance", None)
        a = cg_mod.get_coingecko_service()
        assert isinstance(a, cg_mod.CoinGeckoService)


# ── services/price empty input ────────────────────────────────────────────


class TestPriceServiceEmptyInput:
    def test_get_prices_empty_returns_empty(self, monkeypatch):
        # Cover line 71 — empty symbols_or_ids early return
        from clawmes.services import coingecko as cg_mod
        from clawmes.services import price as price_mod

        monkeypatch.setattr(cg_mod, "_instance", None)
        monkeypatch.setattr(price_mod, "_instance", None)
        svc = price_mod.get_price_service()
        assert svc.get_prices([], "usd") == {}


# ── skills/__init__ ───────────────────────────────────────────────────────


class TestSkillsRegisterAll:
    def test_skills_dir_missing_returns_silently(self, monkeypatch, tmp_path):
        # Cover line 30 — _SKILLS_DIR doesn't exist
        from clawmes import skills as skills_mod

        monkeypatch.setattr(skills_mod, "_SKILLS_DIR", tmp_path / "no-such-dir")
        recorded = []

        class FakeCtx:
            def register_skill(self, **kw):
                recorded.append(kw)

        skills_mod.register_all(FakeCtx())
        assert recorded == []

    def test_skills_dir_with_files_only(self, monkeypatch, tmp_path):
        """Cover the `if not child.is_dir(): continue` branch."""
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "loose-file.txt").write_text("not a skill", encoding="utf-8")

        from clawmes import skills as skills_mod

        monkeypatch.setattr(skills_mod, "_SKILLS_DIR", skills_dir)
        recorded = []

        class FakeCtx:
            def register_skill(self, **kw):
                recorded.append(kw)

        skills_mod.register_all(FakeCtx())
        assert recorded == []

    def test_skills_dir_with_dir_no_skill_md(self, monkeypatch, tmp_path):
        """Cover the `if not skill_md.exists(): continue` branch."""
        skills_dir = tmp_path / "skills"
        (skills_dir / "incomplete-skill").mkdir(parents=True)

        from clawmes import skills as skills_mod

        monkeypatch.setattr(skills_mod, "_SKILLS_DIR", skills_dir)
        recorded = []

        class FakeCtx:
            def register_skill(self, **kw):
                recorded.append(kw)

        skills_mod.register_all(FakeCtx())
        assert recorded == []

    def test_skill_with_register_failure_continues(self, monkeypatch, tmp_path):
        """Cover lines 41-42 — one skill registration raises, others skipped."""
        skills_dir = tmp_path / "skills"
        skill_a = skills_dir / "alpha"
        skill_a.mkdir(parents=True)
        (skill_a / "SKILL.md").write_text("---\nname: alpha\n---\nbody", encoding="utf-8")

        from clawmes import skills as skills_mod

        monkeypatch.setattr(skills_mod, "_SKILLS_DIR", skills_dir)

        class BoomCtx:
            def register_skill(self, **kw):
                raise RuntimeError("simulated")

        # Must not raise
        skills_mod.register_all(BoomCtx())

    def test_extract_description_handles_oserror(self, monkeypatch):
        """Cover lines 53-54 — read_text raises OSError."""
        from clawmes.skills import _extract_description

        path = MagicMock(spec=Path)
        path.read_text.side_effect = OSError("permission denied")
        assert _extract_description(path) == ""

    def test_extract_description_no_frontmatter(self, tmp_path):
        """Cover line 56 — file doesn't start with '---'."""
        from clawmes.skills import _extract_description

        path = tmp_path / "SKILL.md"
        path.write_text("# Just a heading, no frontmatter\n", encoding="utf-8")
        assert _extract_description(path) == ""

    def test_extract_description_terminator(self, tmp_path):
        """Cover line 60 — frontmatter terminator hit before description."""
        from clawmes.skills import _extract_description

        path = tmp_path / "SKILL.md"
        # No description, just name + closing ---
        path.write_text("---\nname: x\n---\nbody\n", encoding="utf-8")
        assert _extract_description(path) == ""

    def test_extract_description_returns_quoted_value(self, tmp_path):
        """Cover line 63 — description with quotes is stripped."""
        from clawmes.skills import _extract_description

        path = tmp_path / "SKILL.md"
        path.write_text('---\nname: x\ndescription: "a quoted string"\n---\n', encoding="utf-8")
        assert _extract_description(path) == "a quoted string"

    def test_extract_description_no_match_in_frontmatter(self, tmp_path):
        """Cover the fall-through return after exhausting all lines."""
        from clawmes.skills import _extract_description

        path = tmp_path / "SKILL.md"
        # Frontmatter has no description, no closing terminator either
        path.write_text("---\nname: x\nversion: 1.0\nauthor: me\n", encoding="utf-8")
        # Falls through the end of splitlines() loop → returns ""
        assert _extract_description(path) == ""


# ── bridges/installer _copy_tree direct invocation ────────────────────────


class TestInstallerCopyTreeBranches:
    def test_copy_tree_replaces_existing_subdir(self, tmp_path):
        """Cover line 121 — shutil.rmtree(target) when subdir already exists."""
        from clawmes.bridges.installer import _copy_tree

        src = tmp_path / "src"
        dst = tmp_path / "dst"

        # First install
        src.mkdir()
        (src / "package.json").write_text("v1", encoding="utf-8")
        (src / "subdir").mkdir()
        (src / "subdir" / "a.ts").write_text("first", encoding="utf-8")
        _copy_tree(src, dst)
        assert (dst / "subdir" / "a.ts").read_text(encoding="utf-8") == "first"

        # Re-copy — now dst already exists with subdir, hits the rmtree branch
        (src / "subdir" / "a.ts").write_text("second", encoding="utf-8")
        _copy_tree(src, dst)
        assert (dst / "subdir" / "a.ts").read_text(encoding="utf-8") == "second"

    def test_copy_tree_replaces_top_level_file(self, tmp_path):
        """Cover line 126 — shutil.copy2 of a top-level file when dst exists."""
        from clawmes.bridges.installer import _copy_tree

        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        (src / "package.json").write_text("first", encoding="utf-8")
        _copy_tree(src, dst)
        assert (dst / "package.json").read_text(encoding="utf-8") == "first"

        # Re-copy with new content
        (src / "package.json").write_text("second", encoding="utf-8")
        _copy_tree(src, dst)
        assert (dst / "package.json").read_text(encoding="utf-8") == "second"

    def test_copy_tree_skips_node_modules_in_existing_dst(self, tmp_path):
        """node_modules is preserved when target already exists."""
        from clawmes.bridges.installer import _copy_tree

        src = tmp_path / "src"
        dst = tmp_path / "dst"
        src.mkdir()
        (src / "package.json").write_text("{}", encoding="utf-8")
        # First copy, then add node_modules to dst
        _copy_tree(src, dst)
        nm = dst / "node_modules"
        nm.mkdir()
        (nm / "preserved.txt").write_text("keep", encoding="utf-8")

        # Add a node_modules dir to src too — should still be skipped on copy
        (src / "node_modules").mkdir()
        (src / "node_modules" / "should-not-copy.txt").write_text("nope", encoding="utf-8")

        _copy_tree(src, dst)
        assert (nm / "preserved.txt").exists()
        assert not (nm / "should-not-copy.txt").exists()


# ── bridges/process line 72 (start when proc already alive) ────────────────


class TestProcessStartShortCircuit:
    def test_start_idempotent_when_alive(self):
        """Cover line 72 — second start() returns early when self._proc.poll() is None."""
        from clawmes.bridges.process import BridgeProcess

        class FakeProc:
            def poll(self):
                return None  # process is "still running"

        proc = BridgeProcess(name="test", entry=Path("/fake.mjs"))
        proc._proc = FakeProc()
        # Should short-circuit; no error
        proc.start()
        assert proc._proc is not None
