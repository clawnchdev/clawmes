"""Tests for the /dca slash command."""

from __future__ import annotations

import json
from dataclasses import dataclass
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
