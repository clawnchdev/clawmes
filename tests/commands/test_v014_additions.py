"""Tests for v0.14.0 operator-automation tier:

* ``/report`` — autonomous performance reports
* ``/objective`` — high-level goal tracking
* ``/auto-tune`` — autonomous schedule review + recommendations
* ``/research`` — structured token research
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from clawmes.commands import (
    alerts,
    auto_tune,
    copy,
    dca,
    limit_order,
    objective,
    report,
    research,
    sniper,
)

# ── shared fixtures ────────────────────────────────────────────────


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


@pytest.fixture
def tmp_alerts_state(tmp_path, monkeypatch):
    p = tmp_path / "alerts.json"
    monkeypatch.setattr(alerts, "_alerts_path", lambda: p)
    return p


@pytest.fixture
def tmp_limit_state(tmp_path, monkeypatch):
    p = tmp_path / "orders.json"
    monkeypatch.setattr(limit_order, "_orders_path", lambda: p)
    return p


@pytest.fixture
def tmp_sniper_state(tmp_path, monkeypatch):
    p = tmp_path / "configs.json"
    monkeypatch.setattr(sniper, "_configs_path", lambda: p)
    return p


@pytest.fixture
def tmp_objective_state(tmp_path, monkeypatch):
    p = tmp_path / "objectives.json"
    monkeypatch.setattr(objective, "_objectives_path", lambda: p)
    return p


@pytest.fixture
def tmp_auto_tune_history(tmp_path, monkeypatch):
    p = tmp_path / "history.json"
    monkeypatch.setattr(auto_tune, "_history_path", lambda: p)
    return p


@pytest.fixture
def all_tmp_states(
    tmp_dca_state,
    tmp_copy_state,
    tmp_alerts_state,
    tmp_limit_state,
    tmp_sniper_state,
    tmp_objective_state,
    tmp_auto_tune_history,
):
    """Single fixture pulling in every state dir we need."""
    return None


# ── /report ────────────────────────────────────────────────────────


class TestReportHelpers:
    def test_now_iso_format(self):
        s = report._now_iso()
        assert s.endswith("Z") and "T" in s

    def test_now_epoch(self):
        assert report._now_epoch() > 0

    def test_parse_iso(self):
        ts = report._parse_iso_to_epoch("2026-05-27T01:00:00Z")
        assert ts > 0

    def test_parse_iso_offset(self):
        ts = report._parse_iso_to_epoch("2026-05-27T01:00:00+00:00")
        assert ts > 0

    def test_parse_iso_bad(self):
        assert report._parse_iso_to_epoch("garbage") == 0

    def test_parse_iso_non_str(self):
        assert report._parse_iso_to_epoch(None) == 0  # type: ignore[arg-type]

    def test_within_window_none(self):
        assert report._within_window("garbage", None) is True

    def test_within_window_bad_at(self):
        assert report._within_window("garbage", 86400) is False

    def test_within_window_inside(self):
        assert report._within_window(report._now_iso(), 86400) is True


class TestReportGate:
    async def test_gate_blocks(self, monkeypatch):
        import clawmes.services.token_gate as tg

        monkeypatch.setattr(tg, "check_tier_or_error", lambda *a, **k: "UNLIMITED required")
        out = await report.handle_report("now")
        assert "UNLIMITED required" in out


class TestReportDispatch:
    async def test_empty_routes_to_now(self, all_tmp_states):
        out = await report.handle_report("")
        assert "AUTOMATION COUNTS" in out

    async def test_now(self, all_tmp_states):
        out = await report.handle_report("now")
        assert "Now" in out

    async def test_daily(self, all_tmp_states):
        out = await report.handle_report("daily")
        assert "Last 24 hours" in out

    async def test_weekly(self, all_tmp_states):
        out = await report.handle_report("weekly")
        assert "Last 7 days" in out

    async def test_objectives_empty(self, all_tmp_states):
        out = await report.handle_report("objectives")
        assert "No objectives registered" in out

    async def test_unknown_mode(self, all_tmp_states):
        out = await report.handle_report("garbage")
        assert "Unknown report mode" in out

    async def test_record_swallows(self, monkeypatch, all_tmp_states):
        import clawmes.services.command_history as ch

        def _boom(*a, **k):
            raise RuntimeError("hist broken")

        monkeypatch.setattr(ch, "record_command_call", _boom)
        out = await report.handle_report("now")
        assert "AUTOMATION COUNTS" in out


class TestReportAggregation:
    def test_summarize_empty(self, all_tmp_states):
        stats = report._summarize_dca("u", None)
        assert stats["schedules"] == 0
        assert stats["executions"] == 0

    def test_summarize_dca_with_history(self, all_tmp_states):
        dca._cmd_add("u", ["0x" + "a" * 40, "0.01", "1h"])
        s = dca._load_state()
        s["schedules"][0]["executions"] = [
            {"at": report._now_iso(), "result": {"status": "ok"}},
            {"at": report._now_iso(), "result": {"status": "error"}},
        ]
        dca._save_state(s)
        stats = report._summarize_dca("u", None)
        assert stats["executions"] == 2
        assert stats["successful"] == 1
        assert stats["failed"] == 1

    def test_summarize_copy_with_history(self, all_tmp_states, monkeypatch):
        monkeypatch.setattr(copy, "_current_block_height", lambda: 1000)
        copy._cmd_add("u", ["0x" + "a" * 40, "0.001"])
        s = copy._load_state()
        s["follows"][0]["executions"] = [
            {
                "at": report._now_iso(),
                "eth_amount": 0.001,
                "result": {"status": "ok"},
            },
            {
                "at": report._now_iso(),
                "result": {"status": "blocklisted"},
            },
            {
                "at": report._now_iso(),
                "result": {"status": "error"},
            },
        ]
        copy._save_state(s)
        stats = report._summarize_copy("u", None)
        assert stats["successful"] == 1
        assert stats["blocklisted"] == 1
        assert stats["failed"] == 1

    def test_summarize_alerts(self, all_tmp_states):
        alerts._cmd_add("u", ["price", "CLAWNCH", "above", "0.0001"])
        s = alerts._load_state()
        s["alerts"][0]["fires"] = [{"at": report._now_iso(), "detail": "x"}]
        alerts._save_state(s)
        stats = report._summarize_alerts("u", None)
        assert stats["fires"] == 1

    def test_summarize_limit_orders(self, all_tmp_states):
        limit_order._cmd_add("u", ["buy", "CLAWNCH", "0.01", "below", "0.00001"])
        s = limit_order._load_state()
        s["orders"][0]["attempts"] = [
            {"at": report._now_iso(), "status": "ok"},
            {"at": report._now_iso(), "status": "error"},
        ]
        limit_order._save_state(s)
        stats = report._summarize_limit_orders("u", None)
        assert stats["attempts"] == 2
        assert stats["fills"] == 1

    def test_summarize_sniper(self, all_tmp_states):
        sniper._cmd_add("u", ["0.005"])
        s = sniper._load_state()
        s["configs"][0]["snipes"] = [{"at": report._now_iso(), "result": {"status": "ok"}}]
        s["configs"][0]["auto_sell_watches"] = [
            {"closed_at": report._now_iso(), "status": "filled"}
        ]
        sniper._save_state(s)
        stats = report._summarize_sniper("u", None)
        assert stats["snipes"] == 1
        assert stats["successful"] == 1
        assert stats["auto_sells"] == 1


class TestFormatStatusBreakdown:
    def test_empty(self):
        assert report._format_status_breakdown({}) == "none"

    def test_with_entries(self):
        out = report._format_status_breakdown({"active": 2, "filled": 1})
        assert "active=2" in out
        assert "filled=1" in out


class TestRenderObjectives:
    def test_empty(self, all_tmp_states):
        out = report._render_objectives("u")
        assert "No objectives registered" in out

    def test_with_objective(self, all_tmp_states):
        # Add an objective via the helper, then render.
        objective._cmd_add(
            "u",
            ["q4", "accumulate", "more", "CLAWNCH", "--budget", "1.5"],
        )
        out = report._render_objectives("u")
        assert "Objectives for u" in out
        assert "q4" in out


# ── /objective ─────────────────────────────────────────────────────


class TestObjectiveHelpers:
    def test_now_iso(self):
        assert objective._now_iso().endswith("Z")

    def test_new_id(self):
        assert objective._new_id().startswith("obj_")

    def test_now_epoch(self):
        assert objective._now_epoch() > 0

    def test_parse_horizon(self):
        assert objective._parse_horizon("1h") == 3600
        assert objective._parse_horizon("1d") == 86400
        assert objective._parse_horizon("garbage") is None

    def test_split_flags(self):
        pos, flags = objective._split_flags(["a", "--x", "1"])
        assert pos == ["a"]
        assert flags == {"x": "1"}

    def test_split_flags_bare(self):
        pos, flags = objective._split_flags(["--bare"])
        assert flags == {"bare": ""}

    def test_iso_to_epoch(self):
        assert objective._iso_to_epoch("2026-05-27T01:00:00Z") > 0

    def test_iso_to_epoch_offset(self):
        assert objective._iso_to_epoch("2026-05-27T01:00:00+00:00") > 0

    def test_iso_to_epoch_bad(self):
        assert objective._iso_to_epoch("garbage") == 0

    def test_iso_to_epoch_non_str(self):
        assert objective._iso_to_epoch(None) == 0  # type: ignore[arg-type]


class TestObjectiveStateIO:
    def test_load_missing(self, tmp_objective_state):
        assert objective._load_state() == {"objectives": []}

    def test_load_bad_json(self, tmp_objective_state):
        tmp_objective_state.write_text("not-json")
        assert objective._load_state() == {"objectives": []}

    def test_load_wrong_shape(self, tmp_objective_state):
        tmp_objective_state.write_text(json.dumps({"objectives": "not-list"}))
        assert objective._load_state() == {"objectives": []}

    def test_load_not_dict(self, tmp_objective_state):
        tmp_objective_state.write_text(json.dumps([]))
        assert objective._load_state() == {"objectives": []}

    def test_roundtrip(self, tmp_objective_state):
        s = {"objectives": [{"id": "x"}]}
        objective._save_state(s)
        assert objective._load_state() == s

    def test_default_path(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        assert objective._objectives_path().name == "objectives.json"


class TestObjectiveCmdAdd:
    def test_gate_blocks(self, tmp_objective_state, monkeypatch):
        import clawmes.services.token_gate as tg

        monkeypatch.setattr(tg, "check_tier_or_error", lambda *a, **k: "UNLIMITED required")
        out = objective._cmd_add("u", ["q4", "goal", "--budget", "1.0"])
        assert "UNLIMITED required" in out

    def test_usage(self, tmp_objective_state):
        out = objective._cmd_add("u", [])
        assert "Usage:" in out

    def test_missing_budget(self, tmp_objective_state):
        out = objective._cmd_add("u", ["q4", "accumulate CLAWNCH"])
        assert "Missing --budget" in out

    def test_bad_budget(self, tmp_objective_state):
        out = objective._cmd_add("u", ["q4", "goal", "--budget", "abc"])
        assert "must be a number" in out

    def test_zero_budget(self, tmp_objective_state):
        out = objective._cmd_add("u", ["q4", "goal", "--budget", "0"])
        assert "must be positive" in out

    def test_bad_horizon(self, tmp_objective_state):
        out = objective._cmd_add(
            "u",
            [
                "q4",
                "goal",
                "--budget",
                "1.0",
                "--horizon",
                "garbage",
            ],
        )
        assert "--horizon must be like" in out

    def test_success(self, tmp_objective_state):
        out = objective._cmd_add(
            "u",
            [
                "q4-clawnch",
                "accumulate",
                "CLAWNCH",
                "by",
                "EOY",
                "--budget",
                "1.5",
            ],
        )
        assert "Objective added" in out
        o = objective._load_state()["objectives"][0]
        assert o["name"] == "q4-clawnch"
        assert "accumulate" in o["goal"]
        assert o["budget_eth"] == 1.5

    def test_success_with_horizon(self, tmp_objective_state):
        out = objective._cmd_add(
            "u",
            [
                "q4",
                "goal",
                "--budget",
                "1.0",
                "--horizon",
                "30d",
            ],
        )
        assert "Horizon:" in out


class TestObjectiveCmdList:
    def test_empty(self, tmp_objective_state):
        out = objective._cmd_list("u")
        assert "No objectives" in out

    def test_with_entries(self, tmp_objective_state):
        objective._cmd_add("u", ["q4", "accumulate CLAWNCH", "--budget", "1.5"])
        out = objective._cmd_list("u")
        assert "q4" in out
        assert "active" in out


class TestObjectiveMutate:
    def test_pause_usage(self, tmp_objective_state):
        out = objective._cmd_mutate("u", [], status="paused", verb="paused")
        assert "Usage:" in out

    def test_pause_not_found(self, tmp_objective_state):
        out = objective._cmd_mutate("u", ["obj_xxx"], status="paused", verb="paused")
        assert "No objective found" in out

    def test_pause_resume(self, tmp_objective_state):
        out = objective._cmd_add("u", ["q4", "goal", "--budget", "1.0"])
        oid = next(w for w in out.split() if w.startswith("obj_"))
        objective._cmd_mutate("u", [oid], status="paused", verb="paused")
        assert objective._load_state()["objectives"][0]["status"] == "paused"


class TestObjectiveCancel:
    def test_usage(self, tmp_objective_state):
        assert "Usage:" in objective._cmd_cancel("u", [])

    def test_not_found(self, tmp_objective_state):
        assert "No objective" in objective._cmd_cancel("u", ["obj_xxx"])

    def test_success(self, tmp_objective_state):
        out = objective._cmd_add("u", ["q4", "goal", "--budget", "1.0"])
        oid = next(w for w in out.split() if w.startswith("obj_"))
        objective._cmd_cancel("u", [oid])
        assert objective._load_state()["objectives"] == []


class TestObjectiveProgress:
    def test_usage(self, tmp_objective_state):
        assert "Usage:" in objective._cmd_progress("u", [])

    def test_not_found(self, tmp_objective_state):
        assert "No objective" in objective._cmd_progress("u", ["obj_xxx"])

    def test_success(self, all_tmp_states):
        out = objective._cmd_add("u", ["q4", "goal", "--budget", "1.0"])
        oid = next(w for w in out.split() if w.startswith("obj_"))
        out = objective._cmd_progress("u", [oid])
        assert "Progress on" in out
        assert "BREAKDOWN BY SOURCE" in out

    def test_with_horizon_progress(self, all_tmp_states):
        objective._cmd_add(
            "u",
            [
                "q4",
                "goal",
                "--budget",
                "1.0",
                "--horizon",
                "1d",
            ],
        )
        oid = objective._load_state()["objectives"][0]["id"]
        out = objective._cmd_progress("u", [oid])
        assert "Horizon used" in out


class TestObjectiveComputeProgress:
    def test_counts_only_after_anchor(self, all_tmp_states):
        # Add a DCA execution BEFORE the objective is registered.
        dca._cmd_add("u", ["0x" + "a" * 40, "0.01", "1h"])
        old_iso = "2020-01-01T00:00:00Z"
        s = dca._load_state()
        s["schedules"][0]["executions"] = [{"at": old_iso, "result": {"status": "ok"}}]
        dca._save_state(s)
        # Now register objective — should NOT see the historical exec.
        objective._cmd_add("u", ["q4", "goal", "--budget", "1.0"])
        obj = objective._load_state()["objectives"][0]
        progress = objective._compute_progress(obj, "u")
        assert progress["eth_spent"] == 0.0

    def test_counts_after_anchor(self, all_tmp_states):
        # Register objective first.
        objective._cmd_add("u", ["q4", "goal", "--budget", "1.0"])
        # Then add a successful DCA execution at "now".
        dca._cmd_add("u", ["0x" + "a" * 40, "0.01", "1h"])
        s = dca._load_state()
        s["schedules"][0]["executions"] = [{"at": report._now_iso(), "result": {"status": "ok"}}]
        dca._save_state(s)
        obj = objective._load_state()["objectives"][0]
        progress = objective._compute_progress(obj, "u")
        assert progress["dca_eth"] == 0.01

    def test_counts_copy_too(self, all_tmp_states, monkeypatch):
        objective._cmd_add("u", ["q4", "goal", "--budget", "1.0"])
        monkeypatch.setattr(copy, "_current_block_height", lambda: 1000)
        copy._cmd_add("u", ["0x" + "a" * 40, "0.001"])
        s = copy._load_state()
        s["follows"][0]["executions"] = [
            {
                "at": report._now_iso(),
                "eth_amount": 0.001,
                "result": {"status": "ok"},
            }
        ]
        copy._save_state(s)
        obj = objective._load_state()["objectives"][0]
        progress = objective._compute_progress(obj, "u")
        assert progress["copy_eth"] == 0.001

    def test_skips_other_senders(self, all_tmp_states):
        objective._cmd_add(
            "alice",
            ["q4", "goal", "--budget", "1.0"],
        )
        # Schedule belongs to bob, not alice.
        dca._cmd_add("bob", ["0x" + "a" * 40, "0.01", "1h"])
        s = dca._load_state()
        s["schedules"][0]["executions"] = [{"at": report._now_iso(), "result": {"status": "ok"}}]
        dca._save_state(s)
        obj = objective._load_state()["objectives"][0]
        progress = objective._compute_progress(obj, "alice")
        assert progress["dca_eth"] == 0.0


class TestObjectiveDispatch:
    async def test_empty(self, tmp_objective_state):
        out = await objective.handle_objective("")
        assert "Objectives" in out

    async def test_unknown(self, tmp_objective_state):
        out = await objective.handle_objective("garbage")
        assert "Unknown subcommand" in out

    async def test_add(self, tmp_objective_state):
        out = await objective.handle_objective("add q4 goal --budget 1.0")
        assert "Objective added" in out

    async def test_list(self, tmp_objective_state):
        out = await objective.handle_objective("list")
        assert "No objectives" in out

    async def test_ls_alias(self, tmp_objective_state):
        out = await objective.handle_objective("ls")
        assert "No objectives" in out

    async def test_pause(self, tmp_objective_state):
        out = await objective.handle_objective("pause obj_xxx")
        assert "No objective" in out

    async def test_resume(self, tmp_objective_state):
        out = await objective.handle_objective("resume obj_xxx")
        assert "No objective" in out

    async def test_cancel(self, tmp_objective_state):
        out = await objective.handle_objective("cancel obj_xxx")
        assert "No objective" in out

    async def test_rm_alias(self, tmp_objective_state):
        out = await objective.handle_objective("rm obj_xxx")
        assert "No objective" in out

    async def test_remove_alias(self, tmp_objective_state):
        out = await objective.handle_objective("remove obj_xxx")
        assert "No objective" in out

    async def test_progress(self, tmp_objective_state):
        out = await objective.handle_objective("progress obj_xxx")
        assert "No objective" in out

    async def test_record_swallows(self, monkeypatch, tmp_objective_state):
        import clawmes.services.command_history as ch

        def _boom(*a, **k):
            raise RuntimeError("hist broken")

        monkeypatch.setattr(ch, "record_command_call", _boom)
        out = await objective.handle_objective("")
        assert "Objectives" in out


class TestObjectiveRegister:
    def test_register(self):
        registered: list[dict] = []

        class Ctx:
            def register_command(self, **kwargs):
                registered.append(kwargs)

        objective.register(Ctx())
        assert len(registered) == 1
        assert registered[0]["name"] == "objective"


# ── /auto-tune ─────────────────────────────────────────────────────


class TestAutoTuneHelpers:
    def test_now_iso(self):
        assert auto_tune._now_iso().endswith("Z")

    def test_new_id(self):
        assert auto_tune._new_id().startswith("rec_")

    def test_now_epoch(self):
        assert auto_tune._now_epoch() > 0

    def test_iso_to_epoch(self):
        assert auto_tune._iso_to_epoch("2026-05-27T01:00:00Z") > 0

    def test_iso_to_epoch_offset(self):
        assert auto_tune._iso_to_epoch("2026-05-27T01:00:00+00:00") > 0

    def test_iso_to_epoch_bad(self):
        assert auto_tune._iso_to_epoch("garbage") == 0

    def test_iso_to_epoch_non_str(self):
        assert auto_tune._iso_to_epoch(None) == 0  # type: ignore[arg-type]


class TestAutoTuneStateIO:
    def test_load_missing(self, tmp_auto_tune_history):
        assert auto_tune._load_history() == {"runs": []}

    def test_load_bad_json(self, tmp_auto_tune_history):
        tmp_auto_tune_history.write_text("not-json")
        assert auto_tune._load_history() == {"runs": []}

    def test_load_wrong_shape(self, tmp_auto_tune_history):
        tmp_auto_tune_history.write_text(json.dumps({"runs": "not-list"}))
        assert auto_tune._load_history() == {"runs": []}

    def test_load_not_dict(self, tmp_auto_tune_history):
        tmp_auto_tune_history.write_text(json.dumps([]))
        assert auto_tune._load_history() == {"runs": []}

    def test_roundtrip(self, tmp_auto_tune_history):
        s = {"runs": [{"id": "x"}]}
        auto_tune._save_history(s)
        assert auto_tune._load_history() == s

    def test_default_path(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        assert auto_tune._history_path().name == "history.json"


class TestAutoTuneGate:
    async def test_gate_blocks(self, monkeypatch):
        import clawmes.services.token_gate as tg

        monkeypatch.setattr(tg, "check_tier_or_error", lambda *a, **k: "UNLIMITED required")
        out = await auto_tune.handle_auto_tune("review")
        assert "UNLIMITED required" in out


class TestAutoTuneRecommendDca:
    def test_no_schedules(self, all_tmp_states):
        assert auto_tune._recommend_dca("u") == []

    def test_low_success_rate(self, all_tmp_states):
        dca._cmd_add("u", ["0x" + "a" * 40, "0.01", "1h"])
        s = dca._load_state()
        # 5 executions, 1 success, 4 failures → 20% success rate.
        s["schedules"][0]["executions"] = [
            {"at": auto_tune._now_iso(), "result": {"status": "ok"}},
            {"at": auto_tune._now_iso(), "result": {"status": "error"}},
            {"at": auto_tune._now_iso(), "result": {"status": "error"}},
            {"at": auto_tune._now_iso(), "result": {"status": "error"}},
            {"at": auto_tune._now_iso(), "result": {"status": "error"}},
        ]
        dca._save_state(s)
        recs = auto_tune._recommend_dca("u")
        assert len(recs) == 1
        assert recs[0]["action"] == "pause"

    def test_paused_schedule_skipped(self, all_tmp_states):
        dca._cmd_add("u", ["0x" + "a" * 40, "0.01", "1h"])
        s = dca._load_state()
        s["schedules"][0]["status"] = "paused"
        s["schedules"][0]["executions"] = [
            {"at": auto_tune._now_iso(), "result": {"status": "error"}} for _ in range(5)
        ]
        dca._save_state(s)
        assert auto_tune._recommend_dca("u") == []

    def test_too_few_executions(self, all_tmp_states):
        dca._cmd_add("u", ["0x" + "a" * 40, "0.01", "1h"])
        s = dca._load_state()
        s["schedules"][0]["executions"] = [
            {"at": auto_tune._now_iso(), "result": {"status": "error"}}
        ]
        dca._save_state(s)
        assert auto_tune._recommend_dca("u") == []

    def test_skips_other_senders(self, all_tmp_states):
        dca._cmd_add("alice", ["0x" + "a" * 40, "0.01", "1h"])
        s = dca._load_state()
        s["schedules"][0]["executions"] = [
            {"at": auto_tune._now_iso(), "result": {"status": "error"}} for _ in range(5)
        ]
        dca._save_state(s)
        assert auto_tune._recommend_dca("bob") == []


class TestAutoTuneRecommendCopy:
    def test_no_follows(self, all_tmp_states):
        assert auto_tune._recommend_copy("u") == []

    def test_zero_successful_with_activity(self, all_tmp_states, monkeypatch):
        monkeypatch.setattr(copy, "_current_block_height", lambda: 1000)
        copy._cmd_add("u", ["0x" + "a" * 40, "0.001"])
        s = copy._load_state()
        s["follows"][0]["executions"] = [
            {"at": auto_tune._now_iso(), "result": {"status": "error"}} for _ in range(3)
        ]
        copy._save_state(s)
        recs = auto_tune._recommend_copy("u")
        assert len(recs) == 1
        assert recs[0]["action"] == "pause"

    def test_paused_skipped(self, all_tmp_states, monkeypatch):
        monkeypatch.setattr(copy, "_current_block_height", lambda: 1000)
        copy._cmd_add("u", ["0x" + "a" * 40, "0.001"])
        s = copy._load_state()
        s["follows"][0]["status"] = "paused"
        s["follows"][0]["executions"] = [
            {"at": auto_tune._now_iso(), "result": {"status": "error"}} for _ in range(3)
        ]
        copy._save_state(s)
        assert auto_tune._recommend_copy("u") == []

    def test_no_recent_executions(self, all_tmp_states, monkeypatch):
        monkeypatch.setattr(copy, "_current_block_height", lambda: 1000)
        copy._cmd_add("u", ["0x" + "a" * 40, "0.001"])
        # No executions at all → no rec.
        assert auto_tune._recommend_copy("u") == []

    def test_old_executions_ignored(self, all_tmp_states, monkeypatch):
        monkeypatch.setattr(copy, "_current_block_height", lambda: 1000)
        copy._cmd_add("u", ["0x" + "a" * 40, "0.001"])
        s = copy._load_state()
        s["follows"][0]["executions"] = [
            {"at": "2020-01-01T00:00:00Z", "result": {"status": "error"}} for _ in range(3)
        ]
        copy._save_state(s)
        assert auto_tune._recommend_copy("u") == []

    def test_success_above_threshold(self, all_tmp_states, monkeypatch):
        monkeypatch.setattr(copy, "_current_block_height", lambda: 1000)
        copy._cmd_add("u", ["0x" + "a" * 40, "0.001"])
        s = copy._load_state()
        s["follows"][0]["executions"] = [
            {"at": auto_tune._now_iso(), "result": {"status": "ok"}} for _ in range(3)
        ]
        copy._save_state(s)
        assert auto_tune._recommend_copy("u") == []


class TestAutoTuneRecommendSniper:
    def test_no_configs(self, all_tmp_states):
        assert auto_tune._recommend_sniper("u") == []

    def test_idle_with_budget(self, all_tmp_states):
        sniper._cmd_add("u", ["0.005"])
        # No snipes yet, budget remaining → rec.
        recs = auto_tune._recommend_sniper("u")
        assert len(recs) == 1
        assert recs[0]["action"] == "review"

    def test_paused_skipped(self, all_tmp_states):
        sniper._cmd_add("u", ["0.005"])
        s = sniper._load_state()
        s["configs"][0]["status"] = "paused"
        sniper._save_state(s)
        assert auto_tune._recommend_sniper("u") == []

    def test_recent_snipe_no_rec(self, all_tmp_states):
        sniper._cmd_add("u", ["0.005"])
        s = sniper._load_state()
        s["configs"][0]["snipes"] = [{"at": auto_tune._now_iso(), "result": {"status": "ok"}}]
        sniper._save_state(s)
        assert auto_tune._recommend_sniper("u") == []

    def test_budget_exhausted_no_rec(self, all_tmp_states):
        sniper._cmd_add("u", ["0.005", "--max-buys", "1"])
        s = sniper._load_state()
        s["configs"][0]["buys_made"] = 1
        sniper._save_state(s)
        assert auto_tune._recommend_sniper("u") == []


class TestAutoTuneRecommendLimitOrder:
    def test_no_orders(self, all_tmp_states):
        assert auto_tune._recommend_limit_order("u") == []

    def test_stale_active(self, all_tmp_states):
        limit_order._cmd_add("u", ["buy", "CLAWNCH", "0.01", "below", "0.00001"])
        s = limit_order._load_state()
        # Force created_at into the past.
        s["orders"][0]["created_at"] = "2020-01-01T00:00:00Z"
        limit_order._save_state(s)
        recs = auto_tune._recommend_limit_order("u")
        assert len(recs) == 1

    def test_recent_order_no_rec(self, all_tmp_states):
        limit_order._cmd_add("u", ["buy", "CLAWNCH", "0.01", "below", "0.00001"])
        # Default created_at is "now" → no rec.
        assert auto_tune._recommend_limit_order("u") == []

    def test_paused_skipped(self, all_tmp_states):
        limit_order._cmd_add("u", ["buy", "CLAWNCH", "0.01", "below", "0.00001"])
        s = limit_order._load_state()
        s["orders"][0]["created_at"] = "2020-01-01T00:00:00Z"
        s["orders"][0]["status"] = "paused"
        limit_order._save_state(s)
        assert auto_tune._recommend_limit_order("u") == []

    def test_missing_created_at_skipped(self, all_tmp_states):
        limit_order._cmd_add("u", ["buy", "CLAWNCH", "0.01", "below", "0.00001"])
        s = limit_order._load_state()
        s["orders"][0]["created_at"] = ""
        limit_order._save_state(s)
        assert auto_tune._recommend_limit_order("u") == []


class TestAutoTuneRecommendAlerts:
    def test_no_alerts(self, all_tmp_states):
        assert auto_tune._recommend_alerts("u") == []

    def test_stale_never_fired_wallet(self, all_tmp_states):
        # Stub block height for the wallet alert.
        import clawmes.commands.alerts as alerts_mod

        alerts_mod._current_block_height = lambda: 1000  # type: ignore[assignment]
        alerts._cmd_add("u", ["wallet", "0x" + "a" * 40])
        s = alerts._load_state()
        s["alerts"][0]["created_at"] = "2020-01-01T00:00:00Z"
        alerts._save_state(s)
        recs = auto_tune._recommend_alerts("u")
        assert len(recs) == 1

    def test_skip_price_type(self, all_tmp_states):
        alerts._cmd_add("u", ["price", "CLAWNCH", "above", "0.0001"])
        s = alerts._load_state()
        s["alerts"][0]["created_at"] = "2020-01-01T00:00:00Z"
        alerts._save_state(s)
        # Price alerts aren't subject to this rec.
        assert auto_tune._recommend_alerts("u") == []

    def test_recent_no_rec(self, all_tmp_states, monkeypatch):
        import clawmes.commands.alerts as alerts_mod

        monkeypatch.setattr(alerts_mod, "_current_block_height", lambda: 1000)
        alerts._cmd_add("u", ["wallet", "0x" + "a" * 40])
        assert auto_tune._recommend_alerts("u") == []

    def test_skipped_when_fired(self, all_tmp_states, monkeypatch):
        import clawmes.commands.alerts as alerts_mod

        monkeypatch.setattr(alerts_mod, "_current_block_height", lambda: 1000)
        alerts._cmd_add("u", ["wallet", "0x" + "a" * 40])
        s = alerts._load_state()
        s["alerts"][0]["created_at"] = "2020-01-01T00:00:00Z"
        s["alerts"][0]["fires"] = [{"at": "x", "detail": "y"}]
        alerts._save_state(s)
        assert auto_tune._recommend_alerts("u") == []

    def test_paused_skipped(self, all_tmp_states, monkeypatch):
        import clawmes.commands.alerts as alerts_mod

        monkeypatch.setattr(alerts_mod, "_current_block_height", lambda: 1000)
        alerts._cmd_add("u", ["wallet", "0x" + "a" * 40])
        s = alerts._load_state()
        s["alerts"][0]["created_at"] = "2020-01-01T00:00:00Z"
        s["alerts"][0]["status"] = "paused"
        alerts._save_state(s)
        assert auto_tune._recommend_alerts("u") == []

    def test_missing_created_at_skipped(self, all_tmp_states, monkeypatch):
        import clawmes.commands.alerts as alerts_mod

        monkeypatch.setattr(alerts_mod, "_current_block_height", lambda: 1000)
        alerts._cmd_add("u", ["wallet", "0x" + "a" * 40])
        s = alerts._load_state()
        s["alerts"][0]["created_at"] = ""
        alerts._save_state(s)
        assert auto_tune._recommend_alerts("u") == []


class TestAutoTuneCmdReview:
    def test_no_recommendations(self, all_tmp_states):
        out = auto_tune._cmd_review("u")
        assert "No recommendations" in out

    def test_with_recommendations_persists(self, all_tmp_states):
        # Create a stale limit order to generate a rec.
        limit_order._cmd_add("u", ["buy", "CLAWNCH", "0.01", "below", "0.00001"])
        s = limit_order._load_state()
        s["orders"][0]["created_at"] = "2020-01-01T00:00:00Z"
        limit_order._save_state(s)
        out = auto_tune._cmd_review("u")
        assert "Recommendations" in out
        # History saved.
        h = auto_tune._load_history()
        assert len(h["runs"]) == 1


class TestAutoTuneCmdApply:
    def test_no_pending(self, all_tmp_states):
        out = auto_tune._cmd_apply("u", [])
        assert "No pending recommendations" in out

    def test_unknown_rec_id(self, all_tmp_states):
        # Generate a review first.
        limit_order._cmd_add("u", ["buy", "CLAWNCH", "0.01", "below", "0.00001"])
        s = limit_order._load_state()
        s["orders"][0]["created_at"] = "2020-01-01T00:00:00Z"
        limit_order._save_state(s)
        auto_tune._cmd_review("u")
        out = auto_tune._cmd_apply("u", ["rec_xxx"])
        assert "No recommendation found" in out

    def test_apply_all_review_no_op(self, all_tmp_states):
        # Stale limit order → "review" action (informational). Apply
        # all should mark them noop but succeed.
        limit_order._cmd_add("u", ["buy", "CLAWNCH", "0.01", "below", "0.00001"])
        s = limit_order._load_state()
        s["orders"][0]["created_at"] = "2020-01-01T00:00:00Z"
        limit_order._save_state(s)
        auto_tune._cmd_review("u")
        out = auto_tune._cmd_apply("u", [])
        assert "Applied" in out

    def test_apply_dca_pause(self, all_tmp_states):
        # Create a DCA schedule eligible for pause.
        dca._cmd_add("u", ["0x" + "a" * 40, "0.01", "1h"])
        s = dca._load_state()
        s["schedules"][0]["executions"] = [
            {"at": auto_tune._now_iso(), "result": {"status": "error"}} for _ in range(5)
        ]
        dca._save_state(s)
        auto_tune._cmd_review("u")
        out = auto_tune._cmd_apply("u", [])
        assert "paused dca schedule" in out
        assert dca._load_state()["schedules"][0]["status"] == "paused"


class TestCommitRecommendation:
    def test_unknown_action(self, all_tmp_states):
        ok, msg = auto_tune._commit_recommendation(
            {"surface": "dca", "action": "weird", "target_id": "x"}, "u"
        )
        assert not ok
        assert "unknown action" in msg

    def test_dca_not_found(self, all_tmp_states):
        ok, msg = auto_tune._commit_recommendation(
            {"surface": "dca", "action": "pause", "target_id": "nope"}, "u"
        )
        assert not ok

    def test_copy_pause(self, all_tmp_states, monkeypatch):
        monkeypatch.setattr(copy, "_current_block_height", lambda: 1000)
        copy._cmd_add("u", ["0x" + "a" * 40, "0.001"])
        fid = copy._load_state()["follows"][0]["id"]
        ok, msg = auto_tune._commit_recommendation(
            {"surface": "copy", "action": "pause", "target_id": fid}, "u"
        )
        assert ok
        assert copy._load_state()["follows"][0]["status"] == "paused"

    def test_copy_not_found(self, all_tmp_states):
        ok, _ = auto_tune._commit_recommendation(
            {"surface": "copy", "action": "pause", "target_id": "nope"}, "u"
        )
        assert not ok

    def test_limit_order_pause(self, all_tmp_states):
        limit_order._cmd_add("u", ["buy", "CLAWNCH", "0.01", "below", "0.00001"])
        oid = limit_order._load_state()["orders"][0]["id"]
        ok, _ = auto_tune._commit_recommendation(
            {"surface": "limit_order", "action": "pause", "target_id": oid}, "u"
        )
        assert ok

    def test_limit_order_not_found(self, all_tmp_states):
        ok, _ = auto_tune._commit_recommendation(
            {"surface": "limit_order", "action": "pause", "target_id": "nope"}, "u"
        )
        assert not ok

    def test_sniper_pause(self, all_tmp_states):
        sniper._cmd_add("u", ["0.005"])
        cid = sniper._load_state()["configs"][0]["id"]
        ok, _ = auto_tune._commit_recommendation(
            {"surface": "sniper", "action": "pause", "target_id": cid}, "u"
        )
        assert ok

    def test_sniper_not_found(self, all_tmp_states):
        ok, _ = auto_tune._commit_recommendation(
            {"surface": "sniper", "action": "pause", "target_id": "nope"}, "u"
        )
        assert not ok

    def test_alerts_pause(self, all_tmp_states, monkeypatch):
        import clawmes.commands.alerts as alerts_mod

        monkeypatch.setattr(alerts_mod, "_current_block_height", lambda: 1000)
        alerts._cmd_add("u", ["wallet", "0x" + "a" * 40])
        aid = alerts._load_state()["alerts"][0]["id"]
        ok, _ = auto_tune._commit_recommendation(
            {"surface": "alerts", "action": "pause", "target_id": aid}, "u"
        )
        assert ok

    def test_alerts_not_found(self, all_tmp_states):
        ok, _ = auto_tune._commit_recommendation(
            {"surface": "alerts", "action": "pause", "target_id": "nope"}, "u"
        )
        assert not ok


class TestAutoTuneCmdHistory:
    def test_empty(self, all_tmp_states):
        out = auto_tune._cmd_history("u")
        assert "No auto-tune history" in out

    def test_with_runs(self, all_tmp_states):
        # Force a review to record history.
        limit_order._cmd_add("u", ["buy", "CLAWNCH", "0.01", "below", "0.00001"])
        s = limit_order._load_state()
        s["orders"][0]["created_at"] = "2020-01-01T00:00:00Z"
        limit_order._save_state(s)
        auto_tune._cmd_review("u")
        out = auto_tune._cmd_history("u")
        assert "Auto-tune history" in out


class TestAutoTuneDispatch:
    async def test_empty_routes_to_review(self, all_tmp_states):
        out = await auto_tune.handle_auto_tune("")
        assert "No recommendations" in out

    async def test_review(self, all_tmp_states):
        out = await auto_tune.handle_auto_tune("review")
        assert "No recommendations" in out

    async def test_apply_empty(self, all_tmp_states):
        out = await auto_tune.handle_auto_tune("apply")
        assert "No pending" in out

    async def test_apply_with_id(self, all_tmp_states):
        out = await auto_tune.handle_auto_tune("apply rec_xxx")
        assert "No pending" in out

    async def test_history(self, all_tmp_states):
        out = await auto_tune.handle_auto_tune("history")
        assert "No auto-tune history" in out

    async def test_unknown(self, all_tmp_states):
        out = await auto_tune.handle_auto_tune("garbage")
        assert "Unknown subcommand" in out

    async def test_record_swallows(self, monkeypatch, all_tmp_states):
        import clawmes.services.command_history as ch

        def _boom(*a, **k):
            raise RuntimeError("hist broken")

        monkeypatch.setattr(ch, "record_command_call", _boom)
        out = await auto_tune.handle_auto_tune("")
        assert "No recommendations" in out


class TestAutoTuneRegister:
    def test_register(self):
        registered: list[dict] = []

        class Ctx:
            def register_command(self, **kwargs):
                registered.append(kwargs)

        auto_tune.register(Ctx())
        assert len(registered) == 1
        assert registered[0]["name"] == "auto-tune"


class TestReportRegister:
    def test_register(self):
        registered: list[dict] = []

        class Ctx:
            def register_command(self, **kwargs):
                registered.append(kwargs)

        report.register(Ctx())
        assert len(registered) == 1
        assert registered[0]["name"] == "report"


# ── /research ──────────────────────────────────────────────────────


@pytest.fixture
def fake_http(monkeypatch):
    """Stub http_get for both DexScreener and Clawnch endpoints."""
    state: dict[str, Any] = {"dex": None, "clawnch": None, "raises": None}

    def _fake(url, *, params=None, timeout=None):  # noqa: ARG001
        if state["raises"] is not None:
            raise state["raises"]
        if "dexscreener" in url:
            return state["dex"]
        if "clawn.ch" in url:
            return state["clawnch"]
        return None

    monkeypatch.setattr(research, "http_get", _fake)
    return state


@pytest.fixture
def fake_defi_price(monkeypatch):
    state: dict[str, Any] = {"payload": None, "raises": None}

    def _fake(args):  # noqa: ARG001
        if state["raises"] is not None:
            raise state["raises"]
        return json.dumps(state["payload"])

    import clawmes.tools.defi_price as mod

    monkeypatch.setattr(mod, "defi_price", _fake)
    return state


class TestResearchHelpers:
    def test_safe_float_none(self):
        assert research._safe_float(None) is None

    def test_safe_float_number(self):
        assert research._safe_float(1.5) == 1.5

    def test_safe_float_string(self):
        assert research._safe_float("1.5") == 1.5

    def test_safe_float_bad(self):
        assert research._safe_float("garbage") is None

    def test_fmt_none(self):
        assert research._fmt(None) == "?"

    def test_fmt_int(self):
        out = research._fmt(2.5)
        assert "$" in out

    def test_fmt_small(self):
        out = research._fmt(0.000001)
        assert "$" in out

    def test_fmt_string(self):
        assert research._fmt("text") == "text"

    def test_fmt_pct_none(self):
        assert research._fmt_pct(None) == "?"

    def test_fmt_pct_positive(self):
        out = research._fmt_pct(5.0)
        assert "+5.0%" == out

    def test_fmt_pct_negative(self):
        out = research._fmt_pct(-3.5)
        assert "-3.5%" == out

    def test_fmt_pct_string(self):
        assert research._fmt_pct("text") == "text"


class TestResearchCard:
    """Desktop UI: /research writes a research card surfaced as an artifact."""

    def test_card_suffix_rich(self):
        # Partial dex (price + liquidity present, others absent) exercises both
        # the value-present and value-absent branches of the row loop.
        report = {
            "dex": {"symbol": "MNEME", "price_usd": 0.0001, "liquidity_usd": 5000.0},
            "flags": ["low_liquidity"],
            "resolved_address": "0x" + "a" * 40,
        }
        out = research._card_suffix(report, "MNEME")
        assert "Research card:" in out
        assert ".html" in out

    def test_card_suffix_empty_report(self):
        # No dex data, no flags → nothing to render → empty suffix.
        assert research._card_suffix({}, "FOO") == ""

    def test_card_suffix_swallows_errors(self, monkeypatch):
        import clawmes.lib.ui_cards as ui_cards

        def _boom(*_a, **_k):
            raise RuntimeError("render failed")

        monkeypatch.setattr(ui_cards, "write_card", _boom)
        report = {"dex": {"symbol": "X", "price_usd": 1.0}, "flags": []}
        assert research._card_suffix(report, "X") == ""

    def test_fmt_usd_none(self):
        assert research._fmt_usd(None) == "?"

    def test_fmt_usd_billions(self):
        assert research._fmt_usd(2_500_000_000) == "$2.50B"

    def test_fmt_usd_millions(self):
        assert research._fmt_usd(2_500_000) == "$2.50M"

    def test_fmt_usd_thousands(self):
        assert research._fmt_usd(2500) == "$2.5k"

    def test_fmt_usd_small(self):
        assert research._fmt_usd(50) == "$50.00"

    def test_fmt_usd_string(self):
        assert research._fmt_usd("text") == "text"


class TestResearchExtractors:
    def test_extract_dex_pairs_list(self):
        out = research._extract_dex_pairs([{"a": 1}, "junk"])
        assert len(out) == 1

    def test_extract_dex_pairs_dict(self):
        out = research._extract_dex_pairs({"pairs": [{"a": 1}]})
        assert len(out) == 1

    def test_extract_dex_pairs_empty(self):
        assert research._extract_dex_pairs(None) == []

    def test_extract_dex_pairs_no_pairs_key(self):
        assert research._extract_dex_pairs({"other": "thing"}) == []

    def test_extract_launches_list(self):
        out = research._extract_launches([{"a": 1}])
        assert len(out) == 1

    def test_extract_launches_dict(self):
        out = research._extract_launches({"launches": [{"a": 1}]})
        assert len(out) == 1

    def test_extract_launches_fallback(self):
        out = research._extract_launches({"results": [{"a": 1}]})
        assert len(out) == 1

    def test_extract_launches_empty(self):
        assert research._extract_launches(None) == []


class TestFetchDexscreener:
    def test_address_endpoint(self, fake_http):
        addr = "0x" + "a" * 40
        fake_http["dex"] = [
            {
                "chainId": "base",
                "baseToken": {"address": addr, "symbol": "X", "name": "X token"},
                "priceUsd": "0.001",
                "volume": {"h24": "1000"},
                "liquidity": {"usd": "5000"},
                "marketCap": "100000",
                "priceChange": {"h24": "5.0"},
                "dexId": "uniswap",
                "pairAddress": "0xpair",
            }
        ]
        out = research._fetch_dexscreener(addr)
        assert out["symbol"] == "X"
        assert out["price_usd"] == 0.001

    def test_symbol_endpoint(self, fake_http):
        fake_http["dex"] = {
            "pairs": [
                {
                    "chainId": "base",
                    "baseToken": {"symbol": "Y"},
                    "volume": {"h24": "1000"},
                }
            ]
        }
        out = research._fetch_dexscreener("Y")
        assert out["symbol"] == "Y"

    def test_no_base_pairs(self, fake_http):
        fake_http["dex"] = [{"chainId": "ethereum", "baseToken": {"symbol": "Z"}}]
        assert research._fetch_dexscreener("Z") is None

    def test_empty(self, fake_http):
        fake_http["dex"] = []
        assert research._fetch_dexscreener("X") is None

    def test_http_error(self, fake_http):
        fake_http["raises"] = RuntimeError("down")
        assert research._fetch_dexscreener("X") is None

    def test_chooses_highest_volume(self, fake_http):
        fake_http["dex"] = [
            {
                "chainId": "base",
                "baseToken": {"symbol": "A"},
                "volume": {"h24": "100"},
            },
            {
                "chainId": "base",
                "baseToken": {"symbol": "B"},
                "volume": {"h24": "1000"},
            },
        ]
        out = research._fetch_dexscreener("X")
        assert out["symbol"] == "B"


class TestFetchDefiPrice:
    def test_success(self, fake_defi_price):
        fake_defi_price["payload"] = {
            "isError": False,
            "details": {"price_usd": 0.5},
        }
        assert research._fetch_defi_price("X") == 0.5

    def test_isError(self, fake_defi_price):
        fake_defi_price["payload"] = {"isError": True}
        assert research._fetch_defi_price("X") is None

    def test_raises(self, fake_defi_price):
        fake_defi_price["raises"] = RuntimeError("rate limit")
        assert research._fetch_defi_price("X") is None

    def test_bad_json(self, monkeypatch):
        import clawmes.tools.defi_price as mod

        monkeypatch.setattr(mod, "defi_price", lambda *a, **k: "not-json")
        assert research._fetch_defi_price("X") is None


class TestFetchClawnchLaunch:
    def test_success(self, fake_http):
        fake_http["clawnch"] = {
            "launches": [
                {
                    "agentName": "ClawBot",
                    "source": "clawmes",
                    "symbol": "X",
                    "name": "X token",
                    "createdAt": "2026-05-27T00:00:00Z",
                }
            ]
        }
        out = research._fetch_clawnch_launch("0x" + "a" * 40)
        assert out["agent"] == "ClawBot"

    def test_no_launches(self, fake_http):
        fake_http["clawnch"] = {"launches": []}
        assert research._fetch_clawnch_launch("0x" + "a" * 40) is None

    def test_http_error(self, fake_http):
        fake_http["raises"] = RuntimeError("down")
        assert research._fetch_clawnch_launch("0x" + "a" * 40) is None


class TestComputeFlags:
    def test_no_dex(self):
        assert research._compute_flags({}) == []

    def test_low_liquidity(self):
        report = {"dex": {"liquidity_usd": 100}}
        assert "low_liquidity" in research._compute_flags(report)

    def test_thin_volume(self):
        report = {"dex": {"volume_24h": 500}}
        assert "thin_volume_24h" in research._compute_flags(report)

    def test_drawdown(self):
        report = {"dex": {"price_change_24h": -60.0}}
        assert "major_drawdown_24h" in research._compute_flags(report)

    def test_blow_off_top(self):
        report = {"dex": {"price_change_24h": 600.0}}
        assert "blow_off_top_candidate" in research._compute_flags(report)


class TestBuildReport:
    def test_with_dex_data(self, fake_http, fake_defi_price):
        fake_http["dex"] = [
            {
                "chainId": "base",
                "baseToken": {
                    "address": "0x" + "a" * 40,
                    "symbol": "X",
                    "name": "X",
                },
                "priceUsd": "0.5",
                "volume": {"h24": "10000"},
                "liquidity": {"usd": "50000"},
                "marketCap": "1000000",
            }
        ]
        report = research._build_report("X")
        assert report["dex"]["symbol"] == "X"
        assert "flags" in report

    def test_with_defi_price_fallback(self, fake_http, fake_defi_price):
        # DexScreener returns nothing → defi_price fallback.
        fake_http["dex"] = []
        fake_defi_price["payload"] = {
            "isError": False,
            "details": {"price_usd": 0.5},
        }
        report = research._build_report("X")
        assert report["price_usd"] == 0.5

    def test_with_clawnch_launch(self, fake_http, fake_defi_price):
        addr = "0x" + "a" * 40
        # DexScreener returns a Base pair with a priceUsd → no defi_price fallback.
        fake_http["dex"] = [
            {
                "chainId": "base",
                "baseToken": {"address": addr, "symbol": "X"},
                "priceUsd": "0.5",
                "volume": {"h24": "1000"},
            }
        ]
        fake_http["clawnch"] = {"launches": [{"agentName": "Bot"}]}
        report = research._build_report(addr)
        assert "clawnch_launch" in report


class TestLlmSynthesize:
    def test_no_opengateway_returns_empty(self, monkeypatch):
        # Force ImportError on opengateway.
        import builtins

        original_import = builtins.__import__

        def _block(name, *args, **kw):
            if name == "clawmes.services.opengateway":
                raise ImportError("no opengateway")
            return original_import(name, *args, **kw)

        monkeypatch.setattr(builtins, "__import__", _block)
        assert research._llm_synthesize({"any": "thing"}) == ""

    def test_opengateway_raises(self, monkeypatch):
        import clawmes.services.opengateway as og

        class _Boom:
            def chat_completion(self, messages, **kw):
                raise RuntimeError("upstream")

        monkeypatch.setattr(og, "get_opengateway_service", lambda: _Boom())
        assert research._llm_synthesize({"x": 1}) == ""

    def test_empty_choices(self, monkeypatch):
        import clawmes.services.opengateway as og

        class _Empty:
            def chat_completion(self, messages, **kw):
                return {"choices": []}

        monkeypatch.setattr(og, "get_opengateway_service", lambda: _Empty())
        assert research._llm_synthesize({}) == ""

    def test_no_content(self, monkeypatch):
        import clawmes.services.opengateway as og

        class _NoContent:
            def chat_completion(self, messages, **kw):
                return {"choices": [{"message": {}}]}

        monkeypatch.setattr(og, "get_opengateway_service", lambda: _NoContent())
        assert research._llm_synthesize({}) == ""

    def test_content_not_string(self, monkeypatch):
        import clawmes.services.opengateway as og

        class _BadType:
            def chat_completion(self, messages, **kw):
                return {"choices": [{"message": {"content": 123}}]}

        monkeypatch.setattr(og, "get_opengateway_service", lambda: _BadType())
        assert research._llm_synthesize({}) == ""

    def test_empty_string(self, monkeypatch):
        import clawmes.services.opengateway as og

        class _Empty:
            def chat_completion(self, messages, **kw):
                return {"choices": [{"message": {"content": "   "}}]}

        monkeypatch.setattr(og, "get_opengateway_service", lambda: _Empty())
        assert research._llm_synthesize({}) == ""

    def test_success(self, monkeypatch):
        import clawmes.services.opengateway as og

        class _Good:
            def chat_completion(self, messages, **kw):
                return {"choices": [{"message": {"content": "Solid token."}}]}

        monkeypatch.setattr(og, "get_opengateway_service", lambda: _Good())
        assert research._llm_synthesize({"x": 1}) == "SUMMARY\nSolid token."


class TestRenderReport:
    def test_with_full_dex(self):
        report = {
            "token_input": "X",
            "dex": {
                "symbol": "X",
                "name": "X token",
                "address": "0xabc",
                "price_usd": 0.5,
                "price_change_24h": 5.0,
                "volume_24h": 10000,
                "liquidity_usd": 50000,
                "market_cap": 1000000,
                "dex_id": "uniswap",
                "pair_address": "0xpair",
            },
            "flags": ["low_liquidity"],
        }
        out = research._render_report(report)
        assert "DEX" in out
        assert "FLAGS" in out

    def test_with_price_fallback(self):
        report = {"token_input": "X", "price_usd": 0.5}
        out = research._render_report(report)
        assert "PRICE" in out
        assert "defi_price" in out

    def test_no_data(self):
        report = {"token_input": "X"}
        out = research._render_report(report)
        assert "No price or pair data" in out

    def test_with_clawnch_launch(self):
        report = {
            "token_input": "X",
            "clawnch_launch": {
                "agent": "Bot",
                "source": "clawmes",
                "symbol": "X",
                "name": "X",
                "deployed_at": "2026-05-27",
            },
        }
        out = research._render_report(report)
        assert "CLAWNCH LAUNCH METADATA" in out


class TestResearchGate:
    async def test_gate_blocks(self, monkeypatch):
        import clawmes.services.token_gate as tg

        monkeypatch.setattr(tg, "check_tier_or_error", lambda *a, **k: "UNLIMITED required")
        out = await research.handle_research("CLAWNCH")
        assert "UNLIMITED required" in out


class TestResearchDispatch:
    async def test_empty_usage(self):
        out = await research.handle_research("")
        assert "Usage:" in out

    async def test_basic(self, fake_http, fake_defi_price):
        fake_http["dex"] = []
        fake_defi_price["payload"] = {"isError": True}
        out = await research.handle_research("UNKNOWN --no-narrative")
        assert "Research: UNKNOWN" in out

    async def test_json_output(self, fake_http, fake_defi_price):
        fake_http["dex"] = []
        fake_defi_price["payload"] = {"isError": True}
        out = await research.handle_research("X --json")
        # JSON should parse.
        data = json.loads(out)
        assert data["token_input"] == "X"

    async def test_with_narrative(self, fake_http, fake_defi_price, monkeypatch):
        fake_http["dex"] = []
        fake_defi_price["payload"] = {"isError": True}
        import clawmes.services.opengateway as og

        class _Good:
            def chat_completion(self, messages, **kw):
                return {"choices": [{"message": {"content": "Looks risky."}}]}

        monkeypatch.setattr(og, "get_opengateway_service", lambda: _Good())
        out = await research.handle_research("X")
        assert "SUMMARY" in out

    async def test_record_swallows(self, monkeypatch, fake_http, fake_defi_price):
        import clawmes.services.command_history as ch

        def _boom(*a, **k):
            raise RuntimeError("hist broken")

        monkeypatch.setattr(ch, "record_command_call", _boom)
        # Stub defi_price with a valid (empty) response so the fallback path
        # doesn't crash on json.dumps(None).
        fake_defi_price["payload"] = {"isError": True}
        out = await research.handle_research("X --no-narrative")
        assert "Research:" in out

    async def test_empty_record_swallows(self, monkeypatch):
        """Empty args path also passes through _record() swallowing."""
        import clawmes.services.command_history as ch

        def _boom(*a, **k):
            raise RuntimeError("hist broken")

        monkeypatch.setattr(ch, "record_command_call", _boom)
        out = await research.handle_research("")
        assert "Usage:" in out


class TestResearchRegister:
    def test_register(self):
        registered: list[dict] = []

        class Ctx:
            def register_command(self, **kwargs):
                registered.append(kwargs)

        research.register(Ctx())
        assert len(registered) == 1
        assert registered[0]["name"] == "research"


# ── branch-coverage stragglers ─────────────────────────────────────


class TestReportWindowSkips:
    """Each summarizer skips executions outside the window. Hit those branches."""

    def test_dca_skips_outside_window(self, all_tmp_states):
        dca._cmd_add("u", ["0x" + "a" * 40, "0.01", "1h"])
        s = dca._load_state()
        # One in-window execution, one out-of-window.
        s["schedules"][0]["executions"] = [
            {"at": report._now_iso(), "result": {"status": "ok"}},
            {"at": "2020-01-01T00:00:00Z", "result": {"status": "ok"}},
        ]
        dca._save_state(s)
        stats = report._summarize_dca("u", 86400)
        # Only the recent one counts.
        assert stats["executions"] == 1

    def test_copy_skips_outside_window(self, all_tmp_states, monkeypatch):
        monkeypatch.setattr(copy, "_current_block_height", lambda: 1000)
        copy._cmd_add("u", ["0x" + "a" * 40, "0.001"])
        s = copy._load_state()
        s["follows"][0]["executions"] = [
            {
                "at": report._now_iso(),
                "eth_amount": 0.001,
                "result": {"status": "ok"},
            },
            {
                "at": "2020-01-01T00:00:00Z",
                "result": {"status": "ok"},
            },
        ]
        copy._save_state(s)
        stats = report._summarize_copy("u", 86400)
        assert stats["executions"] == 1

    def test_alerts_skips_outside_window(self, all_tmp_states):
        alerts._cmd_add("u", ["price", "CLAWNCH", "above", "0.0001"])
        s = alerts._load_state()
        s["alerts"][0]["fires"] = [
            {"at": report._now_iso(), "detail": "x"},
            {"at": "2020-01-01T00:00:00Z", "detail": "y"},
        ]
        alerts._save_state(s)
        stats = report._summarize_alerts("u", 86400)
        assert stats["fires"] == 1

    def test_limit_order_skips_outside_window(self, all_tmp_states):
        limit_order._cmd_add("u", ["buy", "CLAWNCH", "0.01", "below", "0.00001"])
        s = limit_order._load_state()
        s["orders"][0]["attempts"] = [
            {"at": report._now_iso(), "status": "ok"},
            {"at": "2020-01-01T00:00:00Z", "status": "ok"},
        ]
        limit_order._save_state(s)
        stats = report._summarize_limit_orders("u", 86400)
        assert stats["attempts"] == 1

    def test_sniper_skips_outside_window(self, all_tmp_states):
        sniper._cmd_add("u", ["0.005"])
        s = sniper._load_state()
        s["configs"][0]["snipes"] = [
            {"at": report._now_iso(), "result": {"status": "ok"}},
            {"at": "2020-01-01T00:00:00Z", "result": {"status": "ok"}},
        ]
        sniper._save_state(s)
        stats = report._summarize_sniper("u", 86400)
        assert stats["snipes"] == 1


class TestObjectiveProgressSkips:
    def test_dca_skips_non_ok(self, all_tmp_states):
        """Errored DCA executions don't count toward an objective's spend."""
        objective._cmd_add("u", ["q4", "goal", "--budget", "1.0"])
        dca._cmd_add("u", ["0x" + "a" * 40, "0.01", "1h"])
        s = dca._load_state()
        s["schedules"][0]["executions"] = [{"at": report._now_iso(), "result": {"status": "error"}}]
        dca._save_state(s)
        obj = objective._load_state()["objectives"][0]
        progress = objective._compute_progress(obj, "u")
        assert progress["dca_eth"] == 0.0

    def test_copy_skips_non_ok(self, all_tmp_states, monkeypatch):
        """Errored copy executions don't count."""
        objective._cmd_add("u", ["q4", "goal", "--budget", "1.0"])
        monkeypatch.setattr(copy, "_current_block_height", lambda: 1000)
        copy._cmd_add("u", ["0x" + "a" * 40, "0.001"])
        s = copy._load_state()
        s["follows"][0]["executions"] = [
            {
                "at": report._now_iso(),
                "eth_amount": 0.001,
                "result": {"status": "error"},
            }
        ]
        copy._save_state(s)
        obj = objective._load_state()["objectives"][0]
        progress = objective._compute_progress(obj, "u")
        assert progress["copy_eth"] == 0.0

    def test_copy_skips_pre_anchor(self, all_tmp_states, monkeypatch):
        """Copy executions before the objective was registered don't count."""
        monkeypatch.setattr(copy, "_current_block_height", lambda: 1000)
        copy._cmd_add("u", ["0x" + "a" * 40, "0.001"])
        s = copy._load_state()
        s["follows"][0]["executions"] = [
            {
                "at": "2020-01-01T00:00:00Z",
                "eth_amount": 0.001,
                "result": {"status": "ok"},
            }
        ]
        copy._save_state(s)
        # Register objective AFTER the (old) execution.
        objective._cmd_add("u", ["q4", "goal", "--budget", "1.0"])
        obj = objective._load_state()["objectives"][0]
        progress = objective._compute_progress(obj, "u")
        assert progress["copy_eth"] == 0.0

    def test_copy_skips_other_senders(self, all_tmp_states, monkeypatch):
        """A copy follow owned by another sender shouldn't contribute."""
        objective._cmd_add("alice", ["q4", "goal", "--budget", "1.0"])
        monkeypatch.setattr(copy, "_current_block_height", lambda: 1000)
        # Follow belongs to bob.
        copy._cmd_add("bob", ["0x" + "a" * 40, "0.001"])
        s = copy._load_state()
        s["follows"][0]["executions"] = [
            {
                "at": report._now_iso(),
                "eth_amount": 0.001,
                "result": {"status": "ok"},
            }
        ]
        copy._save_state(s)
        obj = objective._load_state()["objectives"][0]
        progress = objective._compute_progress(obj, "alice")
        assert progress["copy_eth"] == 0.0


class TestAutoTuneSenderIsolation:
    """Recommenders must skip records owned by other senders."""

    def test_copy_other_sender_skipped(self, all_tmp_states, monkeypatch):
        monkeypatch.setattr(copy, "_current_block_height", lambda: 1000)
        copy._cmd_add("alice", ["0x" + "a" * 40, "0.001"])
        s = copy._load_state()
        s["follows"][0]["executions"] = [
            {"at": auto_tune._now_iso(), "result": {"status": "error"}} for _ in range(3)
        ]
        copy._save_state(s)
        assert auto_tune._recommend_copy("bob") == []

    def test_sniper_other_sender_skipped(self, all_tmp_states):
        sniper._cmd_add("alice", ["0.005"])
        assert auto_tune._recommend_sniper("bob") == []

    def test_limit_order_other_sender_skipped(self, all_tmp_states):
        limit_order._cmd_add("alice", ["buy", "CLAWNCH", "0.01", "below", "0.00001"])
        s = limit_order._load_state()
        s["orders"][0]["created_at"] = "2020-01-01T00:00:00Z"
        limit_order._save_state(s)
        assert auto_tune._recommend_limit_order("bob") == []

    def test_alerts_other_sender_skipped(self, all_tmp_states, monkeypatch):
        import clawmes.commands.alerts as alerts_mod

        monkeypatch.setattr(alerts_mod, "_current_block_height", lambda: 1000)
        alerts._cmd_add("alice", ["wallet", "0x" + "a" * 40])
        s = alerts._load_state()
        s["alerts"][0]["created_at"] = "2020-01-01T00:00:00Z"
        alerts._save_state(s)
        assert auto_tune._recommend_alerts("bob") == []


class TestAutoTuneApplyFailures:
    """Hit the failure path in _cmd_apply by deleting the target before apply."""

    def test_apply_records_failures(self, all_tmp_states):
        # Stale alert → "review" rec, which is no-op.
        # Use a DCA schedule with low success to get a "pause" rec, then
        # delete the schedule between review and apply.
        dca._cmd_add("u", ["0x" + "a" * 40, "0.01", "1h"])
        s = dca._load_state()
        s["schedules"][0]["executions"] = [
            {"at": auto_tune._now_iso(), "result": {"status": "error"}} for _ in range(5)
        ]
        dca._save_state(s)
        # Review captures the rec.
        auto_tune._cmd_review("u")
        # Now cancel the schedule before apply runs.
        s = dca._load_state()
        s["schedules"] = []
        dca._save_state(s)
        # Apply should record the failure.
        out = auto_tune._cmd_apply("u", [])
        assert "Failed" in out
