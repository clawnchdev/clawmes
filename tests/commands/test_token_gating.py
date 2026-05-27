"""Tests that exercise the v0.9.0 token-gating branches in
``/dca``, ``/copy``, and ``/agent``.

The conftest autouse fixture patches the gate helpers to always-pass
for the existing test suite. These tests selectively patch them to
return errors so we exercise the rejection branches in each command's
``_cmd_add`` (or, for ``/agent``, ``_cmd_parse``).
"""

from __future__ import annotations

import pytest

from clawmes.commands import agent_plan, copy, dca


@pytest.fixture(autouse=True)
def _clear_drafts():
    agent_plan._reset_for_tests()
    yield
    agent_plan._reset_for_tests()


@pytest.fixture
def tmp_dca_state(tmp_path, monkeypatch):
    p = tmp_path / "schedules.json"
    monkeypatch.setattr(dca, "_schedules_path", lambda: p)
    return p


@pytest.fixture
def tmp_copy_state(tmp_path, monkeypatch):
    p = tmp_path / "follows.json"
    monkeypatch.setattr(copy, "_follows_path", lambda: p)
    return p


# ── /dca rejection paths ───────────────────────────────────────────


class TestDcaCapRejection:
    def test_cap_blocks_add(self, tmp_dca_state, monkeypatch):
        """When check_cap_or_error returns a string, /dca add bails."""
        import clawmes.services.token_gate as tg

        monkeypatch.setattr(
            tg,
            "check_cap_or_error",
            lambda *a, **k: "Free tier allows 1 active DCA schedule(s).",
        )
        out = dca._cmd_add("u", ["0x" + "a" * 40, "0.01", "1h"])
        assert "Free tier allows" in out


class TestDcaSafeguardGate:
    def test_safeguard_flag_blocks_free_tier(self, tmp_dca_state, monkeypatch):
        """Passing --slippage on free tier triggers the HOLDER gate."""
        import clawmes.services.token_gate as tg

        monkeypatch.setattr(
            tg,
            "check_tier_or_error",
            lambda *a, **k: "feature requires holding at least 10,000,000 $CLAWNCH.",
        )
        out = dca._cmd_add("u", ["0x" + "a" * 40, "0.01", "1h", "--slippage", "50"])
        assert "requires holding at least" in out

    def test_no_safeguard_flags_bypasses_gate(self, tmp_dca_state, monkeypatch):
        """A free-tier add without safeguard flags should NOT call the tier gate."""
        import clawmes.services.token_gate as tg

        called = {"n": 0}

        def _spy(*a, **k):
            called["n"] += 1
            return "should not be hit"

        monkeypatch.setattr(tg, "check_tier_or_error", _spy)
        out = dca._cmd_add("u", ["0x" + "a" * 40, "0.01", "1h"])
        assert "Schedule added" in out
        assert called["n"] == 0


# ── /copy rejection paths ──────────────────────────────────────────


class TestCopyCapRejection:
    def test_cap_blocks_add(self, tmp_copy_state, monkeypatch):
        import clawmes.services.token_gate as tg

        monkeypatch.setattr(
            tg,
            "check_cap_or_error",
            lambda *a, **k: "Free tier allows 1 active copy follow(s).",
        )
        monkeypatch.setattr(copy, "_current_block_height", lambda: 1000)
        out = copy._cmd_add("u", ["0x" + "a" * 40, "0.001"])
        assert "Free tier allows" in out


# ── /agent rejection paths ─────────────────────────────────────────


class TestAgentMultiStepGate:
    def test_multi_step_blocks_free_tier(self, monkeypatch):
        """A 2-step prompt on free tier returns the gate error."""
        import clawmes.services.token_gate as tg

        monkeypatch.setattr(
            tg,
            "check_tier_or_error",
            lambda *a, **k: "/agent multi-step requires holding at least 10,000,000 $CLAWNCH.",
        )
        out = agent_plan._cmd_parse("u", "claim my fees then burn 1000000")
        assert "requires holding" in out
        # Draft NOT stored since the gate rejected.
        assert "u" not in agent_plan._DRAFTS

    def test_single_step_bypasses_gate(self, monkeypatch):
        """Single-step prompts on free tier don't call the gate."""
        import clawmes.services.token_gate as tg

        called = {"n": 0}

        def _spy(*a, **k):
            called["n"] += 1
            return "should not be hit"

        monkeypatch.setattr(tg, "check_tier_or_error", _spy)
        out = agent_plan._cmd_parse("u", "claim my fees")
        assert "Plan parsed" in out
        assert called["n"] == 0
