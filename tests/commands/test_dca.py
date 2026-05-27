"""Tests for the /dca slash command."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC
from typing import Any

import pytest

from clawmes.commands import dca

# ── fakes ───────────────────────────────────────────────────────────


@dataclass
class _FakeWalletState:
    connected: bool = True
    address: str = "0x" + "1" * 40


@pytest.fixture
def tmp_state(tmp_path, monkeypatch):
    """Redirect ``_schedules_path`` to a tmp file so tests don't write
    to the real ``~/.hermes/clawmes/dca/schedules.json``."""
    p = tmp_path / "schedules.json"

    def _path():
        return p

    monkeypatch.setattr(dca, "_schedules_path", _path)
    return p


@pytest.fixture
def fake_wallet(monkeypatch):
    state = _FakeWalletState()

    def _state():
        return state

    import clawmes.services.wallet as wallet_mod

    monkeypatch.setattr(wallet_mod, "get_wallet_state", _state)
    return state


@pytest.fixture
def fake_defi_swap(monkeypatch):
    """Swap-result fixture: configure response by setting ``state['payload']``."""
    state: dict[str, Any] = {"payload": None, "raises": None}

    def _fake(args):  # noqa: ARG001
        if state["raises"] is not None:
            raise state["raises"]
        return json.dumps(state["payload"])

    import clawmes.tools.defi_swap as swap_mod

    monkeypatch.setattr(swap_mod, "defi_swap", _fake)
    return state


# ── _parse_interval / _format_interval ─────────────────────────────


class TestParseInterval:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("1m", 60),
            ("30m", 1800),
            ("1h", 3600),
            ("4h", 14400),
            ("1d", 86400),
            ("1w", 604800),
            ("  2h  ", 7200),
            ("3H", 10800),  # case-insensitive
        ],
    )
    def test_valid(self, raw, expected):
        assert dca._parse_interval(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        [
            "30s",  # below floor (60s)
            "",
            "garbage",
            "1y",  # no year unit
            "10w",  # 10w = 70d, below ceiling but >0s, allowed
        ],
    )
    def test_invalid_below_floor_or_bad(self, raw):
        if raw == "10w":
            # 10w is actually valid (within ceiling of 365d).
            assert dca._parse_interval(raw) == 10 * 604800
        else:
            assert dca._parse_interval(raw) is None

    def test_above_ceiling(self):
        # 53w > 365d → rejected.
        assert dca._parse_interval("53w") is None


class TestFormatInterval:
    @pytest.mark.parametrize(
        "secs,expected",
        [
            (60, "1m"),
            (3600, "1h"),
            (86400, "1d"),
            (604800, "1w"),
            (1209600, "2w"),
            (1800, "30m"),
        ],
    )
    def test_format(self, secs, expected):
        assert dca._format_interval(secs) == expected


# ── _format_relative ────────────────────────────────────────────────


class TestFormatRelative:
    @pytest.mark.parametrize(
        "secs,expected",
        [
            (45, "45s"),
            (90, "1m"),
            (3600, "1h"),
            (86400, "1d"),
            (-10, "0s"),  # negatives clamp to 0
        ],
    )
    def test_format(self, secs, expected):
        assert dca._format_relative(secs) == expected


# ── _short ──────────────────────────────────────────────────────────


class TestShort:
    def test_short_unchanged(self):
        assert dca._short("0x123") == "0x123"

    def test_long_truncated(self):
        out = dca._short("0x" + "a" * 40)
        assert "…" in out

    def test_non_str(self):
        assert dca._short(None) == "None"  # type: ignore[arg-type]


# ── state I/O ───────────────────────────────────────────────────────


class TestState:
    def test_load_missing_file(self, tmp_state):
        assert dca._load_state() == {"schedules": []}

    def test_load_bad_json(self, tmp_state):
        tmp_state.write_text("not-json")
        assert dca._load_state() == {"schedules": []}

    def test_load_wrong_shape(self, tmp_state):
        tmp_state.write_text(json.dumps({"schedules": "not-a-list"}))
        assert dca._load_state() == {"schedules": []}

    def test_load_not_a_dict(self, tmp_state):
        tmp_state.write_text(json.dumps(["foo"]))
        assert dca._load_state() == {"schedules": []}

    def test_save_roundtrip(self, tmp_state):
        state = {"schedules": [{"id": "x"}]}
        dca._save_state(state)
        assert dca._load_state() == state

    def test_now_iso_format(self):
        s = dca._now_iso()
        assert s.endswith("Z")
        assert "T" in s

    def test_new_id_prefix(self):
        sid = dca._new_id()
        assert sid.startswith("dca_")

    def test_default_schedules_path(self, monkeypatch, tmp_path):
        # Cover the lazy-import branch in _schedules_path() by hitting
        # the real implementation. We point HERMES_HOME at tmp_path so
        # the path computation works without depending on user state.
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        # The fixture above replaces _schedules_path; bypass it for this test
        # by calling state_dir directly via the real module.
        from clawmes.commands.dca import _schedules_path as _ignored  # noqa: F401
        from clawmes.lib.paths import state_dir

        p = state_dir("dca") / "schedules.json"
        assert "dca" in str(p)


class TestRealSchedulesPath:
    def test_default_path_under_hermes_home(self, monkeypatch, tmp_path):
        """Force the real (unpatched) ``_schedules_path`` to resolve."""
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        # Use the real function — no monkeypatch.
        p = dca._schedules_path()
        assert p.name == "schedules.json"
        assert "dca" in str(p)


# ── _cmd_add ────────────────────────────────────────────────────────


class TestCmdAdd:
    def test_too_few_args(self, tmp_state):
        out = dca._cmd_add("u", [])
        assert "Usage:" in out

    def test_bad_token(self, tmp_state):
        out = dca._cmd_add("u", ["not-address", "0.01", "1h"])
        assert "must be a 0x… address" in out

    def test_non_numeric_eth(self, tmp_state):
        out = dca._cmd_add("u", ["0x" + "1" * 40, "abc", "1h"])
        assert "eth_amount must be a number" in out

    def test_zero_eth(self, tmp_state):
        out = dca._cmd_add("u", ["0x" + "1" * 40, "0", "1h"])
        assert "must be positive" in out

    def test_negative_eth(self, tmp_state):
        out = dca._cmd_add("u", ["0x" + "1" * 40, "-1", "1h"])
        assert "must be positive" in out

    def test_bad_interval(self, tmp_state):
        out = dca._cmd_add("u", ["0x" + "1" * 40, "0.01", "junk"])
        assert "Could not parse interval" in out

    def test_success(self, tmp_state):
        out = dca._cmd_add("u", ["0x" + "1" * 40, "0.001", "1h"])
        assert "Schedule added" in out
        assert "dca_" in out

        # Persisted.
        loaded = dca._load_state()
        assert len(loaded["schedules"]) == 1
        sched = loaded["schedules"][0]
        assert sched["token"] == "0x" + "1" * 40
        assert sched["eth_amount"] == 0.001
        assert sched["interval_seconds"] == 3600
        assert sched["status"] == "active"


# ── _cmd_list ───────────────────────────────────────────────────────


class TestCmdList:
    def test_empty(self, tmp_state):
        out = dca._cmd_list("u")
        assert "No DCA schedules" in out

    def test_isolates_by_sender(self, tmp_state):
        dca._cmd_add("alice", ["0x" + "1" * 40, "0.01", "1h"])
        dca._cmd_add("bob", ["0x" + "2" * 40, "0.02", "1d"])
        out = dca._cmd_list("alice")
        assert "alice" in out
        assert "0.01" in out
        assert "0.02" not in out

    def test_overdue_marker(self, tmp_state, monkeypatch):
        # Add a schedule, then poke its next_run_epoch into the past.
        dca._cmd_add("u", ["0x" + "1" * 40, "0.01", "1h"])
        state = dca._load_state()
        state["schedules"][0]["next_run_epoch"] = 0
        dca._save_state(state)
        out = dca._cmd_list("u")
        assert "overdue" in out

    def test_shows_run_count(self, tmp_state):
        dca._cmd_add("u", ["0x" + "1" * 40, "0.01", "1h"])
        state = dca._load_state()
        state["schedules"][0]["executions"] = [{"at": "x"}, {"at": "y"}]
        dca._save_state(state)
        out = dca._cmd_list("u")
        assert "(2 runs" in out


# ── _cmd_pause / _cmd_resume / _cmd_cancel / _mutate ───────────────


class TestPauseResumeCancel:
    def test_pause_missing_id(self, tmp_state):
        out = dca._cmd_pause("u", [])
        assert "Usage:" in out

    def test_pause_not_found(self, tmp_state):
        out = dca._cmd_pause("u", ["dca_zzz"])
        assert "No schedule found" in out

    def test_pause_resume_cycle(self, tmp_state):
        add_out = dca._cmd_add("u", ["0x" + "1" * 40, "0.01", "1h"])
        sched_id = [w for w in add_out.split() if w.startswith("dca_")][0]
        assert "paused" in dca._cmd_pause("u", [sched_id])
        assert dca._load_state()["schedules"][0]["status"] == "paused"
        assert "resumed" in dca._cmd_resume("u", [sched_id])
        assert dca._load_state()["schedules"][0]["status"] == "active"

    def test_cancel_missing_id(self, tmp_state):
        out = dca._cmd_cancel("u", [])
        assert "Usage:" in out

    def test_cancel_not_found(self, tmp_state):
        out = dca._cmd_cancel("u", ["dca_zzz"])
        assert "No schedule found" in out

    def test_cancel_success(self, tmp_state):
        add_out = dca._cmd_add("u", ["0x" + "1" * 40, "0.01", "1h"])
        sched_id = [w for w in add_out.split() if w.startswith("dca_")][0]
        out = dca._cmd_cancel("u", [sched_id])
        assert "Cancelled" in out
        assert dca._load_state()["schedules"] == []

    def test_cancel_isolated_by_sender(self, tmp_state):
        add_out = dca._cmd_add("alice", ["0x" + "1" * 40, "0.01", "1h"])
        sched_id = [w for w in add_out.split() if w.startswith("dca_")][0]
        # Bob tries to cancel Alice's schedule — should fail.
        out = dca._cmd_cancel("bob", [sched_id])
        assert "No schedule found" in out
        assert len(dca._load_state()["schedules"]) == 1


# ── _cmd_tick / _execute ───────────────────────────────────────────


class TestTick:
    async def test_no_due(self, tmp_state, fake_wallet):
        out = await dca._cmd_tick()
        assert "No DCA schedules due" in out

    async def test_one_due_no_wallet(self, tmp_state, fake_wallet, fake_defi_swap):
        fake_wallet.connected = False
        # Add a schedule and force it to be overdue.
        dca._cmd_add("u", ["0x" + "1" * 40, "0.01", "1h"])
        state = dca._load_state()
        state["schedules"][0]["next_run_epoch"] = 0
        dca._save_state(state)

        out = await dca._cmd_tick()
        assert "Executing 1" in out
        assert "no_wallet" in out

        # next_run_epoch advanced.
        s2 = dca._load_state()
        assert s2["schedules"][0]["next_run_epoch"] > 0

    async def test_one_due_swap_success(self, tmp_state, fake_wallet, fake_defi_swap):
        fake_defi_swap["payload"] = {
            "isError": False,
            "details": {"tx_hash": "0xfeed1234567890abc"},
        }
        dca._cmd_add("u", ["0x" + "1" * 40, "0.01", "1h"])
        state = dca._load_state()
        state["schedules"][0]["next_run_epoch"] = 0
        dca._save_state(state)

        out = await dca._cmd_tick()
        assert "ok" in out
        s2 = dca._load_state()
        executions = s2["schedules"][0]["executions"]
        assert len(executions) == 1
        assert executions[0]["tx_hash"] == "0xfeed1234567890abc"

    async def test_swap_error_isError_true(self, tmp_state, fake_wallet, fake_defi_swap):
        fake_defi_swap["payload"] = {
            "isError": True,
            "content": [{"text": "slippage too high"}],
        }
        dca._cmd_add("u", ["0x" + "1" * 40, "0.01", "1h"])
        state = dca._load_state()
        state["schedules"][0]["next_run_epoch"] = 0
        dca._save_state(state)

        out = await dca._cmd_tick()
        assert "error" in out

    async def test_swap_isError_with_missing_content(self, tmp_state, fake_wallet, fake_defi_swap):
        fake_defi_swap["payload"] = {"isError": True}
        dca._cmd_add("u", ["0x" + "1" * 40, "0.01", "1h"])
        state = dca._load_state()
        state["schedules"][0]["next_run_epoch"] = 0
        dca._save_state(state)

        out = await dca._cmd_tick()
        assert "error" in out

    async def test_swap_raises(self, tmp_state, fake_wallet, fake_defi_swap):
        fake_defi_swap["raises"] = RuntimeError("rpc down")
        dca._cmd_add("u", ["0x" + "1" * 40, "0.01", "1h"])
        state = dca._load_state()
        state["schedules"][0]["next_run_epoch"] = 0
        dca._save_state(state)

        out = await dca._cmd_tick()
        assert "error" in out
        assert "rpc down" in out

    async def test_swap_bad_json(self, tmp_state, fake_wallet, monkeypatch):
        import clawmes.tools.defi_swap as swap_mod

        monkeypatch.setattr(swap_mod, "defi_swap", lambda *a, **k: "not-json")
        dca._cmd_add("u", ["0x" + "1" * 40, "0.01", "1h"])
        state = dca._load_state()
        state["schedules"][0]["next_run_epoch"] = 0
        dca._save_state(state)

        out = await dca._cmd_tick()
        assert "error" in out

    async def test_paused_schedule_skipped(self, tmp_state, fake_wallet, fake_defi_swap):
        dca._cmd_add("u", ["0x" + "1" * 40, "0.01", "1h"])
        state = dca._load_state()
        state["schedules"][0]["next_run_epoch"] = 0
        state["schedules"][0]["status"] = "paused"
        dca._save_state(state)

        out = await dca._cmd_tick()
        assert "No DCA schedules due" in out


# ── _cmd_history ────────────────────────────────────────────────────


class TestHistory:
    def test_missing_id(self, tmp_state):
        out = dca._cmd_history("u", [])
        assert "Usage:" in out

    def test_not_found(self, tmp_state):
        out = dca._cmd_history("u", ["dca_zzz"])
        assert "No schedule found" in out

    def test_no_executions(self, tmp_state):
        add_out = dca._cmd_add("u", ["0x" + "1" * 40, "0.01", "1h"])
        sched_id = [w for w in add_out.split() if w.startswith("dca_")][0]
        out = dca._cmd_history("u", [sched_id])
        assert "no executions yet" in out

    def test_with_executions(self, tmp_state):
        add_out = dca._cmd_add("u", ["0x" + "1" * 40, "0.01", "1h"])
        sched_id = [w for w in add_out.split() if w.startswith("dca_")][0]
        state = dca._load_state()
        state["schedules"][0]["executions"] = [
            {
                "at": "2026-05-27T01:00:00Z",
                "tx_hash": "0xabcd1234567890efgh",
                "result": {"status": "ok"},
            },
            {
                "at": "2026-05-27T02:00:00Z",
                "tx_hash": "",
                "result": {"status": "no_wallet"},
            },
        ]
        dca._save_state(state)
        out = dca._cmd_history("u", [sched_id])
        assert "Executions for" in out
        assert "ok" in out
        assert "no_wallet" in out
        # First run shows the truncated tx hash.
        assert "tx 0xabcd" in out


# ── handle_dca (dispatch) ──────────────────────────────────────────


class TestHandleDca:
    async def test_empty_args(self, tmp_state):
        out = await dca.handle_dca("")
        assert "recurring ETH-funded buys" in out

    async def test_unknown_subcommand(self, tmp_state):
        out = await dca.handle_dca("garbage")
        assert "Unknown subcommand" in out

    async def test_add_dispatch(self, tmp_state):
        out = await dca.handle_dca("add 0x" + "1" * 40 + " 0.01 1h")
        assert "Schedule added" in out

    async def test_list_dispatch(self, tmp_state):
        out = await dca.handle_dca("list")
        assert "No DCA schedules" in out

    async def test_ls_alias(self, tmp_state):
        out = await dca.handle_dca("ls")
        assert "No DCA schedules" in out

    async def test_pause_dispatch(self, tmp_state):
        out = await dca.handle_dca("pause dca_zzz")
        assert "No schedule found" in out

    async def test_resume_dispatch(self, tmp_state):
        out = await dca.handle_dca("resume dca_zzz")
        assert "No schedule found" in out

    async def test_cancel_dispatch(self, tmp_state):
        out = await dca.handle_dca("cancel dca_zzz")
        assert "No schedule found" in out

    async def test_rm_alias(self, tmp_state):
        out = await dca.handle_dca("rm dca_zzz")
        assert "No schedule found" in out

    async def test_remove_alias(self, tmp_state):
        out = await dca.handle_dca("remove dca_zzz")
        assert "No schedule found" in out

    async def test_tick_dispatch(self, tmp_state, fake_wallet):
        out = await dca.handle_dca("tick")
        assert "No DCA schedules due" in out

    async def test_history_dispatch(self, tmp_state):
        out = await dca.handle_dca("history dca_zzz")
        assert "No schedule found" in out

    async def test_record_swallows(self, monkeypatch, tmp_state):
        import clawmes.services.command_history as ch

        def _boom(*a, **k):
            raise RuntimeError("hist broken")

        monkeypatch.setattr(ch, "record_command_call", _boom)
        # Should not raise.
        out = await dca.handle_dca("")
        assert "recurring" in out


# ── register ───────────────────────────────────────────────────────


class TestRegister:
    def test_register_wires_command(self):
        registered: list[dict] = []

        class Ctx:
            def register_command(self, **kwargs):
                registered.append(kwargs)

        dca.register(Ctx())
        assert len(registered) == 1
        assert registered[0]["name"] == "dca"


# ── v0.6.1 additions ────────────────────────────────────────────────


def _add_basic(sender_id: str = "u") -> str:
    """Helper: add a schedule and return its id."""
    out = dca._cmd_add(sender_id, ["0x" + "1" * 40, "0.01", "1h"])
    return next(w for w in out.split() if w.startswith("dca_"))


# ── _split_flags ────────────────────────────────────────────────────


class TestSplitFlags:
    def test_all_positional(self):
        pos, flags = dca._split_flags(["a", "b", "c"])
        assert pos == ["a", "b", "c"]
        assert flags == {}

    def test_flag_with_value(self):
        pos, flags = dca._split_flags(["a", "--slippage", "50"])
        assert pos == ["a"]
        assert flags == {"slippage": "50"}

    def test_bare_trailing_flag(self):
        pos, flags = dca._split_flags(["a", "--bare"])
        assert pos == ["a"]
        # Bare flag at end attaches empty string per design.
        assert flags == {"bare": ""}

    def test_multiple_mixed(self):
        pos, flags = dca._split_flags(["a", "b", "--x", "1", "c", "--y", "2", "--z"])
        assert pos == ["a", "b", "c"]
        assert flags == {"x": "1", "y": "2", "z": ""}


# ── _cmd_add with flags ─────────────────────────────────────────────


class TestCmdAddFlags:
    def test_slippage(self, tmp_state):
        out = dca._cmd_add("u", ["0x" + "1" * 40, "0.01", "1h", "--slippage", "50"])
        assert "Schedule added" in out
        sched = dca._load_state()["schedules"][0]
        assert sched["slippage_bps"] == 50

    def test_bad_slippage_non_int(self, tmp_state):
        out = dca._cmd_add("u", ["0x" + "1" * 40, "0.01", "1h", "--slippage", "abc"])
        assert "--slippage must be an integer" in out

    def test_bad_slippage_range(self, tmp_state):
        out = dca._cmd_add("u", ["0x" + "1" * 40, "0.01", "1h", "--slippage", "20000"])
        assert "0–10000" in out

    def test_daily_cap(self, tmp_state):
        out = dca._cmd_add("u", ["0x" + "1" * 40, "0.01", "1h", "--daily-cap", "0.05"])
        assert "Daily cap:   0.05" in out
        sched = dca._load_state()["schedules"][0]
        assert sched["daily_cap_eth"] == 0.05

    def test_bad_daily_cap(self, tmp_state):
        out = dca._cmd_add("u", ["0x" + "1" * 40, "0.01", "1h", "--daily-cap", "abc"])
        assert "--daily-cap must be a number" in out

    def test_daily_cap_zero_or_negative(self, tmp_state):
        out = dca._cmd_add("u", ["0x" + "1" * 40, "0.01", "1h", "--daily-cap", "0"])
        assert "--daily-cap must be positive" in out

    def test_max_total(self, tmp_state):
        out = dca._cmd_add("u", ["0x" + "1" * 40, "0.01", "1h", "--max-total", "1.5"])
        sched = dca._load_state()["schedules"][0]
        assert sched["max_eth_total"] == 1.5
        assert "Total cap:   1.5" in out

    def test_bad_max_total(self, tmp_state):
        out = dca._cmd_add("u", ["0x" + "1" * 40, "0.01", "1h", "--max-total", "x"])
        assert "--max-total must be a number" in out

    def test_max_total_non_positive(self, tmp_state):
        out = dca._cmd_add("u", ["0x" + "1" * 40, "0.01", "1h", "--max-total", "-1"])
        assert "--max-total must be positive" in out

    def test_max_failures(self, tmp_state):
        dca._cmd_add("u", ["0x" + "1" * 40, "0.01", "1h", "--max-failures", "5"])
        sched = dca._load_state()["schedules"][0]
        assert sched["max_consecutive_failures"] == 5

    def test_bad_max_failures(self, tmp_state):
        out = dca._cmd_add("u", ["0x" + "1" * 40, "0.01", "1h", "--max-failures", "x"])
        assert "--max-failures must be an integer" in out

    def test_max_failures_zero(self, tmp_state):
        out = dca._cmd_add("u", ["0x" + "1" * 40, "0.01", "1h", "--max-failures", "0"])
        assert "must be >= 1" in out

    def test_defaults_set(self, tmp_state):
        out = dca._cmd_add("u", ["0x" + "1" * 40, "0.01", "1h"])
        assert "Schedule added" in out
        sched = dca._load_state()["schedules"][0]
        assert sched["slippage_bps"] == dca._DEFAULT_SLIPPAGE_BPS
        assert sched["daily_cap_eth"] is None
        assert sched["max_eth_total"] is None
        assert sched["max_consecutive_failures"] == dca._DEFAULT_MAX_FAILURES
        assert sched["total_eth_spent"] == 0.0


# ── safeguards in _execute_sync ─────────────────────────────────────


class TestSafeguards:
    def test_total_cap_blocks(self, tmp_state, fake_wallet, fake_defi_swap):
        dca._cmd_add("u", ["0x" + "1" * 40, "0.01", "1h", "--max-total", "0.005"])
        # Force a tick.
        st = dca._load_state()
        st["schedules"][0]["next_run_epoch"] = 0
        dca._save_state(st)
        out = dca._run_due_sync()
        assert out == 1
        # Verify the result was the safeguard, not a swap attempt.
        st2 = dca._load_state()
        result = st2["schedules"][0]["executions"][0]["result"]
        assert result["status"] == "total_capped"
        # defi_swap was never called.
        assert fake_defi_swap["payload"] is None

    def test_daily_cap_blocks(self, tmp_state, fake_wallet, fake_defi_swap):
        # Seed one successful prior run in the past 24h, then cap is 0.015.
        # New run would push total to 0.02 → blocked.
        dca._cmd_add("u", ["0x" + "1" * 40, "0.01", "1h", "--daily-cap", "0.015"])
        st = dca._load_state()
        st["schedules"][0]["executions"].append(
            {
                "at": dca._now_iso(),
                "tx_hash": "0xabc",
                "result": {"status": "ok"},
            }
        )
        st["schedules"][0]["next_run_epoch"] = 0
        dca._save_state(st)
        dca._run_due_sync()
        st2 = dca._load_state()
        last = st2["schedules"][0]["executions"][-1]
        assert last["result"]["status"] == "daily_capped"

    def test_daily_cap_window_old_runs_ignored(self, tmp_state, fake_wallet, fake_defi_swap):
        """Runs older than 24h must not count toward the daily cap."""
        fake_defi_swap["payload"] = {
            "isError": False,
            "details": {"tx_hash": "0xfeedabcdefg"},
        }
        dca._cmd_add("u", ["0x" + "1" * 40, "0.01", "1h", "--daily-cap", "0.015"])
        st = dca._load_state()
        # A two-day-old successful run — must not block today's run.
        from datetime import datetime, timedelta

        old = (datetime.now(UTC) - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        st["schedules"][0]["executions"].append(
            {"at": old, "tx_hash": "0xold", "result": {"status": "ok"}}
        )
        st["schedules"][0]["next_run_epoch"] = 0
        dca._save_state(st)
        dca._run_due_sync()
        st2 = dca._load_state()
        last = st2["schedules"][0]["executions"][-1]
        assert last["result"]["status"] == "ok"

    def test_daily_cap_skips_non_ok_runs(self, tmp_state, fake_wallet, fake_defi_swap):
        """Errored prior runs should not count toward the daily cap window."""
        fake_defi_swap["payload"] = {
            "isError": False,
            "details": {"tx_hash": "0xfeed"},
        }
        dca._cmd_add("u", ["0x" + "1" * 40, "0.01", "1h", "--daily-cap", "0.015"])
        st = dca._load_state()
        # A recent failed run — should be skipped in spend-window math.
        st["schedules"][0]["executions"].append(
            {
                "at": dca._now_iso(),
                "tx_hash": "",
                "result": {"status": "error"},
            }
        )
        st["schedules"][0]["next_run_epoch"] = 0
        dca._save_state(st)
        dca._run_due_sync()
        st2 = dca._load_state()
        last = st2["schedules"][0]["executions"][-1]
        # Cap = 0.015 ETH, prior error run doesn't count, new run = 0.01,
        # so total within cap → swap proceeds.
        assert last["result"]["status"] == "ok"

    def test_daily_cap_malformed_at_ignored(self, tmp_state, fake_wallet, fake_defi_swap):
        """A malformed ``at`` field should be silently skipped (defensive)."""
        fake_defi_swap["payload"] = {
            "isError": False,
            "details": {"tx_hash": "0xfeed"},
        }
        dca._cmd_add("u", ["0x" + "1" * 40, "0.01", "1h", "--daily-cap", "0.015"])
        st = dca._load_state()
        st["schedules"][0]["executions"].append(
            {"at": "garbage", "tx_hash": "0x", "result": {"status": "ok"}}
        )
        st["schedules"][0]["next_run_epoch"] = 0
        dca._save_state(st)
        dca._run_due_sync()
        st2 = dca._load_state()
        last = st2["schedules"][0]["executions"][-1]
        # Bad timestamp ignored — swap proceeds.
        assert last["result"]["status"] == "ok"

    def test_total_eth_spent_accumulates(self, tmp_state, fake_wallet, fake_defi_swap):
        fake_defi_swap["payload"] = {
            "isError": False,
            "details": {"tx_hash": "0xfeed"},
        }
        dca._cmd_add("u", ["0x" + "1" * 40, "0.01", "1h"])
        # Tick once.
        st = dca._load_state()
        st["schedules"][0]["next_run_epoch"] = 0
        dca._save_state(st)
        dca._run_due_sync()
        st2 = dca._load_state()
        assert st2["schedules"][0]["total_eth_spent"] == 0.01

    def test_auto_pause_after_failures(self, tmp_state, fake_wallet, fake_defi_swap):
        """Three consecutive failures auto-pause the schedule."""
        fake_wallet.connected = False  # forces no_wallet failures
        dca._cmd_add("u", ["0x" + "1" * 40, "0.01", "1h", "--max-failures", "2"])
        # Tick three times, each fails with no_wallet.
        for _ in range(3):
            st = dca._load_state()
            st["schedules"][0]["next_run_epoch"] = 0
            dca._save_state(st)
            dca._run_due_sync()

        st = dca._load_state()
        assert st["schedules"][0]["status"] == "paused"

    def test_no_auto_pause_below_threshold(self, tmp_state, fake_wallet, fake_defi_swap):
        fake_wallet.connected = False
        dca._cmd_add("u", ["0x" + "1" * 40, "0.01", "1h", "--max-failures", "5"])
        for _ in range(2):
            st = dca._load_state()
            st["schedules"][0]["next_run_epoch"] = 0
            dca._save_state(st)
            dca._run_due_sync()
        st = dca._load_state()
        assert st["schedules"][0]["status"] == "active"


# ── _maybe_auto_pause direct ───────────────────────────────────────


class TestMaybeAutoPause:
    def test_too_few_runs(self):
        sched = {"max_consecutive_failures": 3, "executions": [{"result": {"status": "error"}}]}
        dca._maybe_auto_pause(sched)
        assert "status" not in sched

    def test_mixed_tail_no_pause(self):
        sched = {
            "max_consecutive_failures": 3,
            "executions": [
                {"result": {"status": "error"}},
                {"result": {"status": "ok"}},
                {"result": {"status": "error"}},
            ],
        }
        dca._maybe_auto_pause(sched)
        assert sched.get("status") != "paused"

    def test_all_fail_pauses(self):
        sched = {
            "max_consecutive_failures": 3,
            "executions": [
                {"result": {"status": "error"}},
                {"result": {"status": "no_wallet"}},
                {"result": {"status": "daily_capped"}},
            ],
        }
        dca._maybe_auto_pause(sched)
        assert sched["status"] == "paused"

    def test_uses_default_when_unset(self):
        # Missing max field → default 3 applies.
        sched = {
            "executions": [
                {"result": {"status": "error"}},
                {"result": {"status": "error"}},
                {"result": {"status": "error"}},
            ],
        }
        dca._maybe_auto_pause(sched)
        assert sched["status"] == "paused"


# ── _cmd_edit ───────────────────────────────────────────────────────


class TestCmdEdit:
    def test_usage(self, tmp_state):
        out = dca._cmd_edit("u", [])
        assert "Usage:" in out
        assert "Fields:" in out

    def test_unknown_field(self, tmp_state):
        sid = _add_basic()
        out = dca._cmd_edit("u", [sid, "garbage", "1"])
        assert "Unknown field" in out

    def test_not_found(self, tmp_state):
        out = dca._cmd_edit("u", ["dca_xxx", "eth_amount", "0.02"])
        assert "No schedule found" in out

    def test_token(self, tmp_state):
        sid = _add_basic()
        new_addr = "0x" + "9" * 40
        out = dca._cmd_edit("u", [sid, "token", new_addr])
        assert "token = " in out
        assert dca._load_state()["schedules"][0]["token"] == new_addr

    def test_token_bad(self, tmp_state):
        sid = _add_basic()
        out = dca._cmd_edit("u", [sid, "token", "notaddr"])
        assert "must be a 0x… address" in out

    def test_eth_amount(self, tmp_state):
        sid = _add_basic()
        out = dca._cmd_edit("u", [sid, "eth_amount", "0.05"])
        assert "eth_amount = " in out
        assert dca._load_state()["schedules"][0]["eth_amount"] == 0.05

    def test_eth_amount_bad(self, tmp_state):
        sid = _add_basic()
        out = dca._cmd_edit("u", [sid, "eth_amount", "abc"])
        assert "must be a number" in out

    def test_eth_amount_non_positive(self, tmp_state):
        sid = _add_basic()
        out = dca._cmd_edit("u", [sid, "eth_amount", "0"])
        assert "must be positive" in out

    def test_interval(self, tmp_state):
        sid = _add_basic()
        out = dca._cmd_edit("u", [sid, "interval", "30m"])
        assert "interval = " in out
        assert dca._load_state()["schedules"][0]["interval_seconds"] == 1800

    def test_interval_bad(self, tmp_state):
        sid = _add_basic()
        out = dca._cmd_edit("u", [sid, "interval", "garbage"])
        assert "Could not parse interval" in out

    def test_slippage_bps(self, tmp_state):
        sid = _add_basic()
        out = dca._cmd_edit("u", [sid, "slippage_bps", "75"])
        assert "slippage_bps = " in out
        assert dca._load_state()["schedules"][0]["slippage_bps"] == 75

    def test_slippage_bps_bad_int(self, tmp_state):
        sid = _add_basic()
        out = dca._cmd_edit("u", [sid, "slippage_bps", "x"])
        assert "must be an integer" in out

    def test_slippage_bps_range(self, tmp_state):
        sid = _add_basic()
        out = dca._cmd_edit("u", [sid, "slippage_bps", "99999"])
        assert "0–10000" in out

    def test_daily_cap_eth_value(self, tmp_state):
        sid = _add_basic()
        out = dca._cmd_edit("u", [sid, "daily_cap_eth", "0.1"])
        assert "daily_cap_eth" in out
        assert dca._load_state()["schedules"][0]["daily_cap_eth"] == 0.1

    def test_daily_cap_eth_none(self, tmp_state):
        sid = _add_basic()
        # Set first
        dca._cmd_edit("u", [sid, "daily_cap_eth", "0.1"])
        out = dca._cmd_edit("u", [sid, "daily_cap_eth", "none"])
        assert "daily_cap_eth" in out
        assert dca._load_state()["schedules"][0]["daily_cap_eth"] is None

    def test_daily_cap_eth_bad(self, tmp_state):
        sid = _add_basic()
        out = dca._cmd_edit("u", [sid, "daily_cap_eth", "abc"])
        assert "must be a number" in out

    def test_daily_cap_eth_non_positive(self, tmp_state):
        sid = _add_basic()
        out = dca._cmd_edit("u", [sid, "daily_cap_eth", "0"])
        assert "must be positive" in out

    def test_max_eth_total_value(self, tmp_state):
        sid = _add_basic()
        dca._cmd_edit("u", [sid, "max_eth_total", "1.0"])
        assert dca._load_state()["schedules"][0]["max_eth_total"] == 1.0

    def test_max_eth_total_none(self, tmp_state):
        sid = _add_basic()
        dca._cmd_edit("u", [sid, "max_eth_total", "NONE"])
        assert dca._load_state()["schedules"][0]["max_eth_total"] is None

    def test_max_eth_total_bad(self, tmp_state):
        sid = _add_basic()
        out = dca._cmd_edit("u", [sid, "max_eth_total", "abc"])
        assert "must be a number" in out

    def test_max_eth_total_non_positive(self, tmp_state):
        sid = _add_basic()
        out = dca._cmd_edit("u", [sid, "max_eth_total", "-1"])
        assert "must be positive" in out

    def test_max_consecutive_failures(self, tmp_state):
        sid = _add_basic()
        dca._cmd_edit("u", [sid, "max_consecutive_failures", "7"])
        assert dca._load_state()["schedules"][0]["max_consecutive_failures"] == 7

    def test_max_consecutive_failures_bad(self, tmp_state):
        sid = _add_basic()
        out = dca._cmd_edit("u", [sid, "max_consecutive_failures", "x"])
        assert "must be an integer" in out

    def test_max_consecutive_failures_zero(self, tmp_state):
        sid = _add_basic()
        out = dca._cmd_edit("u", [sid, "max_consecutive_failures", "0"])
        assert "must be >= 1" in out


# ── _cmd_skip ───────────────────────────────────────────────────────


class TestCmdSkip:
    def test_usage(self, tmp_state):
        out = dca._cmd_skip("u", [])
        assert "Usage:" in out

    def test_not_found(self, tmp_state):
        out = dca._cmd_skip("u", ["dca_xxx"])
        assert "No schedule found" in out

    def test_advances_next_run(self, tmp_state):
        sid = _add_basic()
        # Force next_run to be in the past.
        st = dca._load_state()
        st["schedules"][0]["next_run_epoch"] = 0
        dca._save_state(st)
        out = dca._cmd_skip("u", [sid])
        assert "Skipped next run" in out
        st2 = dca._load_state()
        assert st2["schedules"][0]["next_run_epoch"] > 0


# ── _cmd_dry_run ────────────────────────────────────────────────────


class TestCmdDryRun:
    def test_usage(self, tmp_state):
        out = dca._cmd_dry_run("u", [])
        assert "Usage:" in out

    def test_not_found(self, tmp_state):
        out = dca._cmd_dry_run("u", ["dca_xxx"])
        assert "No schedule found" in out

    def test_swap_raises(self, tmp_state, fake_defi_swap):
        fake_defi_swap["raises"] = RuntimeError("rpc down")
        sid = _add_basic()
        out = dca._cmd_dry_run("u", [sid])
        assert "Dry-run failed" in out
        assert "rpc down" in out

    def test_swap_bad_json(self, tmp_state, monkeypatch):
        import clawmes.tools.defi_swap as swap_mod

        monkeypatch.setattr(swap_mod, "defi_swap", lambda *a, **k: "not-json")
        sid = _add_basic()
        out = dca._cmd_dry_run("u", [sid])
        assert "bad swap response" in out

    def test_swap_isError(self, tmp_state, fake_defi_swap):
        fake_defi_swap["payload"] = {
            "isError": True,
            "content": [{"text": "no route"}],
        }
        sid = _add_basic()
        out = dca._cmd_dry_run("u", [sid])
        assert "Dry-run quote failed" in out
        assert "no route" in out

    def test_swap_isError_no_content(self, tmp_state, fake_defi_swap):
        fake_defi_swap["payload"] = {"isError": True}
        sid = _add_basic()
        out = dca._cmd_dry_run("u", [sid])
        assert "Dry-run quote failed" in out

    def test_swap_success(self, tmp_state, fake_defi_swap):
        fake_defi_swap["payload"] = {
            "isError": False,
            "details": {"buy_amount": "1234.5"},
        }
        sid = _add_basic()
        out = dca._cmd_dry_run("u", [sid])
        assert "Dry-run for" in out
        assert "1234.5" in out
        assert "No transaction submitted" in out


# ── _cmd_status ─────────────────────────────────────────────────────


class TestCmdStatus:
    def test_empty(self, tmp_state):
        out = dca._cmd_status("u")
        assert "scheduler service is idle" in out

    def test_summarizes(self, tmp_state):
        dca._cmd_add("alice", ["0x" + "1" * 40, "0.01", "1h"])
        dca._cmd_add("bob", ["0x" + "2" * 40, "0.02", "1d"])
        # Mark bob's schedule paused + add some history.
        st = dca._load_state()
        st["schedules"][1]["status"] = "paused"
        st["schedules"][0]["executions"] = [
            {"at": "x", "tx_hash": "0xa", "result": {"status": "ok"}},
            {"at": "y", "tx_hash": "0xb", "result": {"status": "error"}},
        ]
        st["schedules"][0]["total_eth_spent"] = 0.01
        dca._save_state(st)

        out = dca._cmd_status("u")
        assert "(2 schedule(s))" in out
        assert "active=1" in out
        assert "paused=1" in out
        assert "Total runs:   2" in out
        assert "Failures:     1" in out
        assert "0.010000" in out  # ETH spent

    def test_service_health_swallow(self, monkeypatch, tmp_state):
        # Force the service import to blow up — _cmd_status should
        # still return cleanly without the service line.
        import clawmes.services.dca_scheduler as svc_mod

        def _boom():
            raise RuntimeError("svc not initialized")

        monkeypatch.setattr(svc_mod, "get_dca_scheduler_service", _boom)
        dca._cmd_add("u", ["0x" + "1" * 40, "0.01", "1h"])
        out = dca._cmd_status("u")
        assert "Service:" not in out

    def test_service_health_present(self, tmp_state):
        # Reset + start service so health() returns a real snapshot.
        from clawmes.services import dca_scheduler as svc_mod

        svc_mod._reset_for_tests()
        svc = svc_mod.get_dca_scheduler_service()
        svc.start()
        try:
            dca._cmd_add("u", ["0x" + "1" * 40, "0.01", "1h"])
            out = dca._cmd_status("u")
            assert "Service:" in out
            assert "running" in out
        finally:
            svc.stop()
            svc_mod._reset_for_tests()


# ── _find_sched ─────────────────────────────────────────────────────


class TestFindSched:
    def test_match(self, tmp_state):
        sid = _add_basic()
        state = dca._load_state()
        assert dca._find_sched(state, sid, "u") is not None

    def test_wrong_sender(self, tmp_state):
        sid = _add_basic("alice")
        state = dca._load_state()
        assert dca._find_sched(state, sid, "bob") is None

    def test_missing_id(self, tmp_state):
        state = dca._load_state()
        assert dca._find_sched(state, "dca_xxx", "u") is None


# ── dispatch for new subcommands ───────────────────────────────────


class TestNewDispatch:
    async def test_edit_dispatch(self, tmp_state):
        out = await dca.handle_dca("edit dca_xxx eth_amount 0.02")
        assert "No schedule found" in out

    async def test_skip_dispatch(self, tmp_state):
        out = await dca.handle_dca("skip dca_xxx")
        assert "No schedule found" in out

    async def test_dry_run_dispatch(self, tmp_state):
        out = await dca.handle_dca("dry-run dca_xxx")
        assert "No schedule found" in out

    async def test_dryrun_alias(self, tmp_state):
        out = await dca.handle_dca("dryrun dca_xxx")
        assert "No schedule found" in out

    async def test_status_dispatch(self, tmp_state):
        out = await dca.handle_dca("status")
        assert "scheduler" in out.lower()


# ── _run_due_sync / _run_due_with_lines ────────────────────────────


class TestRunDueSync:
    def test_no_due(self, tmp_state, fake_wallet, fake_defi_swap):
        # Add a schedule with future next_run.
        _add_basic()
        assert dca._run_due_sync() == 0

    def test_due_runs_count(self, tmp_state, fake_wallet, fake_defi_swap):
        fake_defi_swap["payload"] = {
            "isError": False,
            "details": {"tx_hash": "0xfeed"},
        }
        _add_basic()
        st = dca._load_state()
        st["schedules"][0]["next_run_epoch"] = 0
        dca._save_state(st)
        assert dca._run_due_sync() == 1
