"""Tests for the /copy slash command."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from clawmes.commands import copy

# ── fakes ───────────────────────────────────────────────────────────


@dataclass
class _FakeWalletState:
    connected: bool = True
    address: str = "0x" + "1" * 40


@pytest.fixture
def tmp_state(tmp_path, monkeypatch):
    p = tmp_path / "follows.json"

    def _path():
        return p

    monkeypatch.setattr(copy, "_follows_path", _path)
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
    state: dict[str, Any] = {"payload": None, "raises": None}

    def _fake(args):  # noqa: ARG001
        if state["raises"] is not None:
            raise state["raises"]
        return json.dumps(state["payload"])

    import clawmes.tools.defi_swap as swap_mod

    monkeypatch.setattr(swap_mod, "defi_swap", _fake)
    return state


@pytest.fixture
def fake_basescan(monkeypatch):
    """Patch ``http_get`` so Basescan endpoints return canned bodies."""
    state: dict[str, Any] = {
        "tokentx": None,  # body for account.tokentx
        "blocknum": None,  # body for proxy.eth_blockNumber
        "raises": None,
    }

    def _fake(url, *, params=None, timeout=None):  # noqa: ARG001
        if state["raises"] is not None:
            raise state["raises"]
        action = (params or {}).get("action")
        if action == "tokentx":
            return state["tokentx"]
        if action == "eth_blockNumber":
            return state["blocknum"]
        return None

    monkeypatch.setattr(copy, "http_get", _fake)
    return state


def _add_basic_follow(monkeypatch, sender="u", wallet=None, eth="0.001") -> str:
    """Add a follow and return its id. Stubs block height to 1000."""
    addr = wallet or "0x" + "a" * 40
    monkeypatch.setattr(copy, "_current_block_height", lambda: 1000)
    out = copy._cmd_add(sender, [addr, eth])
    return next(w for w in out.split() if w.startswith("copy_"))


# ── helpers ─────────────────────────────────────────────────────────


class TestHelpers:
    def test_short_unchanged(self):
        assert copy._short("0x123") == "0x123"

    def test_short_truncated(self):
        out = copy._short("0x" + "a" * 40)
        assert "…" in out

    def test_short_non_str(self):
        assert copy._short(None) == "None"  # type: ignore[arg-type]

    def test_now_iso_format(self):
        s = copy._now_iso()
        assert s.endswith("Z") and "T" in s

    def test_new_id(self):
        assert copy._new_id().startswith("copy_")

    def test_now_epoch(self):
        assert copy._now_epoch() > 0


class TestSplitFlags:
    def test_positional_only(self):
        pos, flags = copy._split_flags(["a", "b"])
        assert pos == ["a", "b"]
        assert flags == {}

    def test_flag_with_value(self):
        pos, flags = copy._split_flags(["a", "--x", "1"])
        assert flags == {"x": "1"}

    def test_bare_trailing(self):
        pos, flags = copy._split_flags(["--bare"])
        assert flags == {"bare": ""}


class TestParseBlocklist:
    def test_empty(self):
        assert copy._parse_blocklist("") == []

    def test_single(self):
        assert copy._parse_blocklist("0x" + "1" * 40) == ["0x" + "1" * 40]

    def test_multi(self):
        out = copy._parse_blocklist("0x" + "1" * 40 + ", 0x" + "2" * 40)
        assert len(out) == 2

    def test_filters_bad_entries(self):
        out = copy._parse_blocklist("0x" + "1" * 40 + ",not-an-addr, 0xabc")
        assert out == ["0x" + "1" * 40]


# ── state I/O ───────────────────────────────────────────────────────


class TestStateIO:
    def test_load_missing(self, tmp_state):
        assert copy._load_state() == {"follows": []}

    def test_load_bad_json(self, tmp_state):
        tmp_state.write_text("not-json")
        assert copy._load_state() == {"follows": []}

    def test_load_wrong_shape(self, tmp_state):
        tmp_state.write_text(json.dumps({"follows": "not-a-list"}))
        assert copy._load_state() == {"follows": []}

    def test_load_not_a_dict(self, tmp_state):
        tmp_state.write_text(json.dumps(["foo"]))
        assert copy._load_state() == {"follows": []}

    def test_roundtrip(self, tmp_state):
        state = {"follows": [{"id": "x"}]}
        copy._save_state(state)
        assert copy._load_state() == state

    def test_default_path_under_hermes_home(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        p = copy._follows_path()
        assert p.name == "follows.json"
        assert "copy" in str(p)


# ── _cmd_add ────────────────────────────────────────────────────────


class TestCmdAdd:
    def test_usage(self, tmp_state, monkeypatch):
        monkeypatch.setattr(copy, "_current_block_height", lambda: 1000)
        out = copy._cmd_add("u", [])
        assert "Usage:" in out

    def test_bad_wallet(self, tmp_state, monkeypatch):
        monkeypatch.setattr(copy, "_current_block_height", lambda: 1000)
        out = copy._cmd_add("u", ["not-addr", "0.001"])
        assert "must be a 0x… address" in out

    def test_bad_eth_non_numeric(self, tmp_state, monkeypatch):
        monkeypatch.setattr(copy, "_current_block_height", lambda: 1000)
        out = copy._cmd_add("u", ["0x" + "a" * 40, "abc"])
        assert "must be a number" in out

    def test_bad_eth_zero(self, tmp_state, monkeypatch):
        monkeypatch.setattr(copy, "_current_block_height", lambda: 1000)
        out = copy._cmd_add("u", ["0x" + "a" * 40, "0"])
        assert "must be positive" in out

    def test_bad_slippage_non_int(self, tmp_state, monkeypatch):
        monkeypatch.setattr(copy, "_current_block_height", lambda: 1000)
        out = copy._cmd_add("u", ["0x" + "a" * 40, "0.001", "--slippage", "x"])
        assert "--slippage must be an integer" in out

    def test_bad_slippage_range(self, tmp_state, monkeypatch):
        monkeypatch.setattr(copy, "_current_block_height", lambda: 1000)
        out = copy._cmd_add("u", ["0x" + "a" * 40, "0.001", "--slippage", "99999"])
        assert "0–10000" in out

    def test_bad_daily_cap(self, tmp_state, monkeypatch):
        monkeypatch.setattr(copy, "_current_block_height", lambda: 1000)
        out = copy._cmd_add("u", ["0x" + "a" * 40, "0.001", "--daily-cap", "x"])
        assert "--daily-cap must be a number" in out

    def test_daily_cap_non_positive(self, tmp_state, monkeypatch):
        monkeypatch.setattr(copy, "_current_block_height", lambda: 1000)
        out = copy._cmd_add("u", ["0x" + "a" * 40, "0.001", "--daily-cap", "0"])
        assert "--daily-cap must be positive" in out

    def test_bad_max_total(self, tmp_state, monkeypatch):
        monkeypatch.setattr(copy, "_current_block_height", lambda: 1000)
        out = copy._cmd_add("u", ["0x" + "a" * 40, "0.001", "--max-total", "x"])
        assert "--max-total must be a number" in out

    def test_max_total_non_positive(self, tmp_state, monkeypatch):
        monkeypatch.setattr(copy, "_current_block_height", lambda: 1000)
        out = copy._cmd_add("u", ["0x" + "a" * 40, "0.001", "--max-total", "-1"])
        assert "--max-total must be positive" in out

    def test_bad_max_failures(self, tmp_state, monkeypatch):
        monkeypatch.setattr(copy, "_current_block_height", lambda: 1000)
        out = copy._cmd_add("u", ["0x" + "a" * 40, "0.001", "--max-failures", "x"])
        assert "--max-failures must be an integer" in out

    def test_max_failures_zero(self, tmp_state, monkeypatch):
        monkeypatch.setattr(copy, "_current_block_height", lambda: 1000)
        out = copy._cmd_add("u", ["0x" + "a" * 40, "0.001", "--max-failures", "0"])
        assert "must be >= 1" in out

    def test_success_defaults(self, tmp_state, monkeypatch):
        monkeypatch.setattr(copy, "_current_block_height", lambda: 1000)
        out = copy._cmd_add("u", ["0x" + "a" * 40, "0.001"])
        assert "Follow added" in out
        f = copy._load_state()["follows"][0]
        assert f["wallet"] == "0x" + "a" * 40
        assert f["eth_per_copy"] == 0.001
        assert f["slippage_bps"] == copy._DEFAULT_SLIPPAGE_BPS
        assert f["max_consecutive_failures"] == copy._DEFAULT_MAX_FAILURES
        assert f["last_seen_block"] == 1000 - copy._DEFAULT_LOOKBACK_BLOCKS

    def test_lookback_clamps_at_zero(self, tmp_state, monkeypatch):
        # If chain height is very low (testnet, fresh chain), don't go negative.
        monkeypatch.setattr(copy, "_current_block_height", lambda: 3)
        copy._cmd_add("u", ["0x" + "a" * 40, "0.001"])
        f = copy._load_state()["follows"][0]
        assert f["last_seen_block"] == 0

    def test_success_all_flags(self, tmp_state, monkeypatch):
        monkeypatch.setattr(copy, "_current_block_height", lambda: 1000)
        bl = "0x" + "1" * 40 + ",0x" + "2" * 40
        out = copy._cmd_add(
            "u",
            [
                "0x" + "a" * 40,
                "0.001",
                "--slippage",
                "200",
                "--daily-cap",
                "0.05",
                "--max-total",
                "1.0",
                "--max-failures",
                "5",
                "--blocklist",
                bl,
            ],
        )
        assert "Follow added" in out
        f = copy._load_state()["follows"][0]
        assert f["slippage_bps"] == 200
        assert f["daily_cap_eth"] == 0.05
        assert f["max_eth_total"] == 1.0
        assert f["max_consecutive_failures"] == 5
        assert len(f["blocklist"]) == 2


# ── _cmd_list / mutate / cancel ────────────────────────────────────


class TestCmdList:
    def test_empty(self, tmp_state):
        assert "No /copy follows" in copy._cmd_list("u")

    def test_lists_mine(self, tmp_state, monkeypatch):
        _add_basic_follow(monkeypatch, "alice")
        _add_basic_follow(monkeypatch, "bob", wallet="0x" + "b" * 40)
        out = copy._cmd_list("alice")
        assert "alice" in out
        # Only Alice's follow should appear (1 follow listed).
        assert out.count("ETH/copy") == 1


class TestMutate:
    def test_pause_usage(self, tmp_state):
        out = copy._cmd_mutate("u", [], status="paused", verb="paused")
        assert "Usage:" in out

    def test_pause_not_found(self, tmp_state):
        out = copy._cmd_mutate("u", ["copy_xxx"], status="paused", verb="paused")
        assert "No follow found" in out

    def test_pause_success(self, tmp_state, monkeypatch):
        fid = _add_basic_follow(monkeypatch)
        out = copy._cmd_mutate("u", [fid], status="paused", verb="paused")
        assert "paused" in out
        assert copy._load_state()["follows"][0]["status"] == "paused"

    def test_resume_success(self, tmp_state, monkeypatch):
        fid = _add_basic_follow(monkeypatch)
        copy._cmd_mutate("u", [fid], status="paused", verb="paused")
        out = copy._cmd_mutate("u", [fid], status="active", verb="resumed")
        assert "resumed" in out


class TestCancel:
    def test_usage(self, tmp_state):
        assert "Usage:" in copy._cmd_cancel("u", [])

    def test_not_found(self, tmp_state):
        assert "No follow" in copy._cmd_cancel("u", ["copy_xxx"])

    def test_success(self, tmp_state, monkeypatch):
        fid = _add_basic_follow(monkeypatch)
        out = copy._cmd_cancel("u", [fid])
        assert "Cancelled" in out
        assert copy._load_state()["follows"] == []

    def test_isolated_by_sender(self, tmp_state, monkeypatch):
        fid = _add_basic_follow(monkeypatch, "alice")
        out = copy._cmd_cancel("bob", [fid])
        assert "No follow" in out
        assert len(copy._load_state()["follows"]) == 1


# ── _cmd_edit ───────────────────────────────────────────────────────


class TestCmdEdit:
    def test_usage(self, tmp_state):
        out = copy._cmd_edit("u", [])
        assert "Usage:" in out

    def test_unknown_field(self, tmp_state, monkeypatch):
        fid = _add_basic_follow(monkeypatch)
        out = copy._cmd_edit("u", [fid, "garbage", "1"])
        assert "Unknown field" in out

    def test_not_found(self, tmp_state):
        out = copy._cmd_edit("u", ["copy_xxx", "eth_per_copy", "0.01"])
        assert "No follow found" in out

    def test_eth_per_copy(self, tmp_state, monkeypatch):
        fid = _add_basic_follow(monkeypatch)
        copy._cmd_edit("u", [fid, "eth_per_copy", "0.05"])
        assert copy._load_state()["follows"][0]["eth_per_copy"] == 0.05

    def test_eth_per_copy_bad(self, tmp_state, monkeypatch):
        fid = _add_basic_follow(monkeypatch)
        assert "must be a number" in copy._cmd_edit("u", [fid, "eth_per_copy", "x"])

    def test_eth_per_copy_non_positive(self, tmp_state, monkeypatch):
        fid = _add_basic_follow(monkeypatch)
        assert "must be positive" in copy._cmd_edit("u", [fid, "eth_per_copy", "0"])

    def test_slippage_bps(self, tmp_state, monkeypatch):
        fid = _add_basic_follow(monkeypatch)
        copy._cmd_edit("u", [fid, "slippage_bps", "75"])
        assert copy._load_state()["follows"][0]["slippage_bps"] == 75

    def test_slippage_bps_bad(self, tmp_state, monkeypatch):
        fid = _add_basic_follow(monkeypatch)
        assert "integer" in copy._cmd_edit("u", [fid, "slippage_bps", "x"])

    def test_slippage_bps_range(self, tmp_state, monkeypatch):
        fid = _add_basic_follow(monkeypatch)
        assert "0–10000" in copy._cmd_edit("u", [fid, "slippage_bps", "99999"])

    def test_daily_cap_value(self, tmp_state, monkeypatch):
        fid = _add_basic_follow(monkeypatch)
        copy._cmd_edit("u", [fid, "daily_cap_eth", "0.1"])
        assert copy._load_state()["follows"][0]["daily_cap_eth"] == 0.1

    def test_daily_cap_none(self, tmp_state, monkeypatch):
        fid = _add_basic_follow(monkeypatch)
        copy._cmd_edit("u", [fid, "daily_cap_eth", "0.1"])
        copy._cmd_edit("u", [fid, "daily_cap_eth", "none"])
        assert copy._load_state()["follows"][0]["daily_cap_eth"] is None

    def test_daily_cap_bad(self, tmp_state, monkeypatch):
        fid = _add_basic_follow(monkeypatch)
        assert "must be a number" in copy._cmd_edit("u", [fid, "daily_cap_eth", "x"])

    def test_daily_cap_non_positive(self, tmp_state, monkeypatch):
        fid = _add_basic_follow(monkeypatch)
        assert "must be positive" in copy._cmd_edit("u", [fid, "daily_cap_eth", "0"])

    def test_max_total_value(self, tmp_state, monkeypatch):
        fid = _add_basic_follow(monkeypatch)
        copy._cmd_edit("u", [fid, "max_eth_total", "0.5"])
        assert copy._load_state()["follows"][0]["max_eth_total"] == 0.5

    def test_max_total_none(self, tmp_state, monkeypatch):
        fid = _add_basic_follow(monkeypatch)
        copy._cmd_edit("u", [fid, "max_eth_total", "NONE"])
        assert copy._load_state()["follows"][0]["max_eth_total"] is None

    def test_max_total_bad(self, tmp_state, monkeypatch):
        fid = _add_basic_follow(monkeypatch)
        assert "must be a number" in copy._cmd_edit("u", [fid, "max_eth_total", "x"])

    def test_max_total_non_positive(self, tmp_state, monkeypatch):
        fid = _add_basic_follow(monkeypatch)
        assert "must be positive" in copy._cmd_edit("u", [fid, "max_eth_total", "-1"])

    def test_max_failures(self, tmp_state, monkeypatch):
        fid = _add_basic_follow(monkeypatch)
        copy._cmd_edit("u", [fid, "max_consecutive_failures", "7"])
        assert copy._load_state()["follows"][0]["max_consecutive_failures"] == 7

    def test_max_failures_bad(self, tmp_state, monkeypatch):
        fid = _add_basic_follow(monkeypatch)
        assert "integer" in copy._cmd_edit("u", [fid, "max_consecutive_failures", "x"])

    def test_max_failures_zero(self, tmp_state, monkeypatch):
        fid = _add_basic_follow(monkeypatch)
        assert "must be >= 1" in copy._cmd_edit("u", [fid, "max_consecutive_failures", "0"])

    def test_blocklist(self, tmp_state, monkeypatch):
        fid = _add_basic_follow(monkeypatch)
        copy._cmd_edit("u", [fid, "blocklist", "0x" + "1" * 40 + ",0x" + "2" * 40])
        assert len(copy._load_state()["follows"][0]["blocklist"]) == 2


# ── _find ───────────────────────────────────────────────────────────


class TestFind:
    def test_match(self, tmp_state, monkeypatch):
        fid = _add_basic_follow(monkeypatch)
        state = copy._load_state()
        assert copy._find(state, fid, "u") is not None

    def test_wrong_sender(self, tmp_state, monkeypatch):
        fid = _add_basic_follow(monkeypatch, "alice")
        state = copy._load_state()
        assert copy._find(state, fid, "bob") is None

    def test_missing(self, tmp_state):
        assert copy._find(copy._load_state(), "copy_xxx", "u") is None


# ── _basescan_token_receipts ────────────────────────────────────────


class TestBasescanTokenReceipts:
    def test_non_dict_body(self, fake_basescan):
        fake_basescan["tokentx"] = "not-a-dict"
        assert copy._basescan_token_receipts("0xabc", start_block=0) == []

    def test_status_zero(self, fake_basescan):
        fake_basescan["tokentx"] = {"status": "0", "message": "No transactions found"}
        assert copy._basescan_token_receipts("0xabc", start_block=0) == []

    def test_result_not_list(self, fake_basescan):
        fake_basescan["tokentx"] = {"status": "1", "result": "string"}
        assert copy._basescan_token_receipts("0xabc", start_block=0) == []

    def test_filters_incoming(self, fake_basescan):
        wallet = "0x" + "a" * 40
        fake_basescan["tokentx"] = {
            "status": "1",
            "result": [
                {"to": wallet, "contractAddress": "0xT1", "blockNumber": "100"},
                {"to": "0x" + "f" * 40, "contractAddress": "0xT2", "blockNumber": "101"},
                {"contractAddress": "0xT3"},  # no `to`
                "not-a-dict",  # noise
            ],
        }
        out = copy._basescan_token_receipts(wallet, start_block=50)
        assert len(out) == 1
        assert out[0]["contractAddress"] == "0xT1"

    def test_api_key_passed_through(self, monkeypatch, fake_basescan):
        captured = {}

        def _spy(url, *, params=None, timeout=None):  # noqa: ARG001
            captured.update(params)
            return {"status": "0"}

        monkeypatch.setattr(copy, "http_get", _spy)
        monkeypatch.setenv("BASESCAN_API_KEY", "MYKEY")
        copy._basescan_token_receipts("0xabc", start_block=0)
        assert captured.get("apikey") == "MYKEY"


# ── _current_block_height ───────────────────────────────────────────


class TestCurrentBlockHeight:
    def test_http_error(self, fake_basescan):
        fake_basescan["raises"] = RuntimeError("down")
        assert copy._current_block_height() == 0

    def test_non_dict(self, fake_basescan):
        fake_basescan["blocknum"] = "junk"
        assert copy._current_block_height() == 0

    def test_no_result_string(self, fake_basescan):
        fake_basescan["blocknum"] = {"result": 123}
        assert copy._current_block_height() == 0

    def test_bad_hex(self, fake_basescan):
        fake_basescan["blocknum"] = {"result": "0xZZZ"}
        assert copy._current_block_height() == 0

    def test_success(self, fake_basescan):
        fake_basescan["blocknum"] = {"result": "0x3e8"}
        assert copy._current_block_height() == 1000

    def test_api_key_passed_through(self, monkeypatch):
        captured = {}

        def _spy(url, *, params=None, timeout=None):  # noqa: ARG001
            captured.update(params)
            return {"result": "0x0"}

        monkeypatch.setattr(copy, "http_get", _spy)
        monkeypatch.setenv("BASESCAN_API_KEY", "MYKEY")
        copy._current_block_height()
        assert captured.get("apikey") == "MYKEY"


# ── _maybe_auto_pause / _spend_in_last_24h ─────────────────────────


class TestMaybeAutoPause:
    def test_too_few(self):
        follow = {"max_consecutive_failures": 3, "executions": [{"result": {"status": "error"}}]}
        copy._maybe_auto_pause(follow)
        assert "status" not in follow

    def test_mixed_no_pause(self):
        follow = {
            "max_consecutive_failures": 3,
            "executions": [
                {"result": {"status": "error"}},
                {"result": {"status": "ok"}},
                {"result": {"status": "error"}},
            ],
        }
        copy._maybe_auto_pause(follow)
        assert follow.get("status") != "paused"

    def test_all_fail_pauses(self):
        follow = {
            "max_consecutive_failures": 3,
            "executions": [
                {"result": {"status": "error"}},
                {"result": {"status": "no_wallet"}},
                {"result": {"status": "daily_capped"}},
            ],
        }
        copy._maybe_auto_pause(follow)
        assert follow["status"] == "paused"

    def test_default_threshold(self):
        follow = {
            "executions": [
                {"result": {"status": "error"}},
                {"result": {"status": "error"}},
                {"result": {"status": "error"}},
            ]
        }
        copy._maybe_auto_pause(follow)
        assert follow["status"] == "paused"


class TestSpendInLast24h:
    def test_no_runs(self):
        assert copy._spend_in_last_24h({"eth_per_copy": 0.01, "executions": []}) == 0

    def test_skips_non_ok(self):
        follow = {
            "eth_per_copy": 0.01,
            "executions": [
                {"at": copy._now_iso(), "result": {"status": "error"}},
                {"at": copy._now_iso(), "result": {"status": "ok"}},
            ],
        }
        assert copy._spend_in_last_24h(follow) == 0.01

    def test_skips_old(self):
        old = (datetime.now(UTC) - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        follow = {
            "eth_per_copy": 0.01,
            "executions": [{"at": old, "result": {"status": "ok"}}],
        }
        assert copy._spend_in_last_24h(follow) == 0

    def test_skips_bad_timestamp(self):
        follow = {
            "eth_per_copy": 0.01,
            "executions": [{"at": "garbage", "result": {"status": "ok"}}],
        }
        assert copy._spend_in_last_24h(follow) == 0


# ── _execute_copy ───────────────────────────────────────────────────


class TestExecuteCopy:
    def test_total_capped(self, fake_wallet, fake_defi_swap):
        follow = {
            "eth_per_copy": 0.01,
            "max_eth_total": 0.005,
            "total_eth_spent": 0.0,
            "executions": [],
        }
        result = copy._execute_copy(follow, "0x" + "1" * 40)
        assert result["status"] == "total_capped"

    def test_daily_capped(self, fake_wallet, fake_defi_swap):
        follow = {
            "eth_per_copy": 0.01,
            "daily_cap_eth": 0.005,
            "executions": [{"at": copy._now_iso(), "result": {"status": "ok"}}],
        }
        result = copy._execute_copy(follow, "0x" + "1" * 40)
        assert result["status"] == "daily_capped"

    def test_no_wallet(self, fake_wallet, fake_defi_swap):
        fake_wallet.connected = False
        follow = {"eth_per_copy": 0.01, "executions": []}
        assert copy._execute_copy(follow, "0x" + "1" * 40)["status"] == "no_wallet"

    def test_defi_swap_raises(self, fake_wallet, fake_defi_swap):
        fake_defi_swap["raises"] = RuntimeError("rpc broke")
        follow = {"eth_per_copy": 0.01, "executions": []}
        result = copy._execute_copy(follow, "0x" + "1" * 40)
        assert result["status"] == "error"
        assert "rpc broke" in result["detail"]

    def test_defi_swap_bad_json(self, fake_wallet, monkeypatch):
        import clawmes.tools.defi_swap as swap_mod

        monkeypatch.setattr(swap_mod, "defi_swap", lambda *a, **k: "not-json")
        follow = {"eth_per_copy": 0.01, "executions": []}
        result = copy._execute_copy(follow, "0x" + "1" * 40)
        assert result["status"] == "error"

    def test_defi_swap_isError(self, fake_wallet, fake_defi_swap):
        fake_defi_swap["payload"] = {
            "isError": True,
            "content": [{"text": "no route"}],
        }
        follow = {"eth_per_copy": 0.01, "executions": []}
        result = copy._execute_copy(follow, "0x" + "1" * 40)
        assert result["status"] == "error"
        assert "no route" in result["detail"]

    def test_defi_swap_isError_missing_content(self, fake_wallet, fake_defi_swap):
        fake_defi_swap["payload"] = {"isError": True}
        follow = {"eth_per_copy": 0.01, "executions": []}
        result = copy._execute_copy(follow, "0x" + "1" * 40)
        assert result["status"] == "error"

    def test_success(self, fake_wallet, fake_defi_swap):
        fake_defi_swap["payload"] = {
            "isError": False,
            "details": {"tx_hash": "0xfeed1234567890abc"},
        }
        follow = {"eth_per_copy": 0.01, "executions": []}
        result = copy._execute_copy(follow, "0x" + "1" * 40)
        assert result["status"] == "ok"
        assert "0xfeed" in result["tx_hash"]


# ── _process_follow + runner ───────────────────────────────────────


class TestProcessFollow:
    def test_no_new_tx(self, tmp_state, fake_basescan, monkeypatch):
        fake_basescan["tokentx"] = {"status": "0", "message": "No transactions found"}
        _add_basic_follow(monkeypatch)
        n = copy._run_due_sync()
        assert n == 0

    def test_blocklisted_skipped(
        self, tmp_state, fake_basescan, monkeypatch, fake_wallet, fake_defi_swap
    ):
        fid = _add_basic_follow(monkeypatch)
        # Add a blocklist entry.
        copy._cmd_edit("u", [fid, "blocklist", "0x" + "T" * 40])

        wallet = copy._load_state()["follows"][0]["wallet"]
        fake_basescan["tokentx"] = {
            "status": "1",
            "result": [
                {
                    "to": wallet,
                    "contractAddress": "0x" + "T" * 40,
                    "blockNumber": "2000",
                    "hash": "0xseen",
                }
            ],
        }
        n = copy._run_due_sync()
        # Blocklisted ⇒ no actual buy submitted; counter is 0.
        assert n == 0
        state = copy._load_state()
        assert state["follows"][0]["executions"][0]["result"]["status"] == "blocklisted"

    def test_successful_copy(
        self, tmp_state, fake_basescan, monkeypatch, fake_wallet, fake_defi_swap
    ):
        fake_defi_swap["payload"] = {
            "isError": False,
            "details": {"tx_hash": "0xfeed"},
        }
        _add_basic_follow(monkeypatch)
        wallet = copy._load_state()["follows"][0]["wallet"]
        token_addr = "0x" + "B" * 40
        fake_basescan["tokentx"] = {
            "status": "1",
            "result": [
                {
                    "to": wallet,
                    "contractAddress": token_addr,
                    "blockNumber": "2000",
                    "hash": "0xseen",
                }
            ],
        }
        n = copy._run_due_sync()
        assert n == 1
        state = copy._load_state()
        follow = state["follows"][0]
        assert follow["last_seen_block"] == 2000
        assert follow["executions"][0]["result"]["status"] == "ok"
        assert follow["total_eth_spent"] == 0.001  # eth_per_copy

    def test_skips_invalid_contract(self, tmp_state, fake_basescan, monkeypatch, fake_wallet):
        _add_basic_follow(monkeypatch)
        wallet = copy._load_state()["follows"][0]["wallet"]
        # Malformed contractAddress should be skipped silently.
        fake_basescan["tokentx"] = {
            "status": "1",
            "result": [
                {"to": wallet, "contractAddress": "0xbad", "blockNumber": "2000"},
                {"to": wallet, "blockNumber": "2001"},
            ],
        }
        n = copy._run_due_sync()
        assert n == 0
        state = copy._load_state()
        # No executions recorded — malformed contractAddress is skipped.
        assert state["follows"][0]["executions"] == []

    def test_paused_follow_skipped(self, tmp_state, monkeypatch, fake_basescan):
        fid = _add_basic_follow(monkeypatch)
        copy._cmd_mutate("u", [fid], status="paused", verb="paused")
        # Even though we have a Basescan response queued, the paused
        # follow shouldn't even hit the polling path.
        fake_basescan["tokentx"] = {
            "status": "1",
            "result": [{"to": "x", "contractAddress": "0x" + "C" * 40, "blockNumber": "2000"}],
        }
        n = copy._run_due_sync()
        assert n == 0

    def test_max_per_tick_cap(
        self, tmp_state, fake_basescan, monkeypatch, fake_wallet, fake_defi_swap
    ):
        fake_defi_swap["payload"] = {
            "isError": False,
            "details": {"tx_hash": "0xfeed"},
        }
        _add_basic_follow(monkeypatch)
        wallet = copy._load_state()["follows"][0]["wallet"]

        # 30 incoming transfers — should be capped at _MAX_TX_PER_TICK (20).
        result = [
            {
                "to": wallet,
                "contractAddress": "0x" + f"{i:040x}",
                "blockNumber": str(2000 + i),
                "hash": f"0xseen{i}",
            }
            for i in range(30)
        ]
        fake_basescan["tokentx"] = {"status": "1", "result": result}
        n = copy._run_due_sync()
        assert n == copy._MAX_TX_PER_TICK

    def test_runner_swallows_per_follow_errors(self, tmp_state, monkeypatch):
        _add_basic_follow(monkeypatch)

        def _boom(*a, **k):
            raise RuntimeError("basescan down")

        monkeypatch.setattr(copy, "_basescan_token_receipts", _boom)
        n, lines = copy._run_due_with_lines()
        assert n == 0
        assert any("error fetching" in line for line in lines)


# ── _cmd_status ─────────────────────────────────────────────────────


class TestCmdStatus:
    def test_empty(self, tmp_state):
        out = copy._cmd_status("u")
        assert "watcher service is idle" in out

    def test_summarizes(self, tmp_state, monkeypatch):
        _add_basic_follow(monkeypatch, "alice")
        _add_basic_follow(monkeypatch, "bob", wallet="0x" + "b" * 40)
        state = copy._load_state()
        state["follows"][0]["executions"] = [
            {"at": "x", "result": {"status": "ok"}},
            {"at": "y", "result": {"status": "error"}},
        ]
        state["follows"][0]["total_eth_spent"] = 0.005
        copy._save_state(state)
        out = copy._cmd_status("alice")
        assert "(2 follow(s))" in out
        assert "active=2" in out
        assert "Failures:     1" in out

    def test_service_health_swallow(self, monkeypatch, tmp_state):
        import clawmes.services.copy_trader as svc_mod

        def _boom():
            raise RuntimeError("svc not initialized")

        monkeypatch.setattr(svc_mod, "get_copy_trader_service", _boom)
        _add_basic_follow(monkeypatch)
        out = copy._cmd_status("u")
        assert "Service:" not in out

    def test_service_health_present(self, tmp_state, monkeypatch):
        from clawmes.services import copy_trader as svc_mod

        svc_mod._reset_for_tests()
        svc = svc_mod.get_copy_trader_service()
        svc.start()
        try:
            _add_basic_follow(monkeypatch)
            out = copy._cmd_status("u")
            assert "Service:" in out
            assert "running" in out
        finally:
            svc.stop()
            svc_mod._reset_for_tests()


# ── _cmd_history ───────────────────────────────────────────────────


class TestCmdHistory:
    def test_usage(self, tmp_state):
        assert "Usage:" in copy._cmd_history("u", [])

    def test_not_found(self, tmp_state):
        assert "No follow" in copy._cmd_history("u", ["copy_xxx"])

    def test_empty(self, tmp_state, monkeypatch):
        fid = _add_basic_follow(monkeypatch)
        assert "no copies yet" in copy._cmd_history("u", [fid])

    def test_with_runs(self, tmp_state, monkeypatch):
        fid = _add_basic_follow(monkeypatch)
        state = copy._load_state()
        state["follows"][0]["executions"] = [
            {
                "at": "2026-05-27T01:00:00Z",
                "tx_seen": "0xabcd1234567890",
                "token": "0x" + "T" * 40,
                "result": {"status": "ok"},
            },
            {
                "at": "2026-05-27T02:00:00Z",
                "tx_seen": "",
                "token": "0x" + "U" * 40,
                "result": {"status": "blocklisted"},
            },
        ]
        copy._save_state(state)
        out = copy._cmd_history("u", [fid])
        assert "Copies for" in out
        assert "ok" in out
        assert "blocklisted" in out
        assert "seen 0xabcd" in out


# ── handle_copy (dispatch) ─────────────────────────────────────────


class TestHandleCopy:
    async def test_empty(self, tmp_state):
        out = await copy.handle_copy("")
        assert "Copy-trade" in out

    async def test_unknown(self, tmp_state):
        out = await copy.handle_copy("garbage")
        assert "Unknown subcommand" in out

    async def test_add(self, tmp_state, monkeypatch):
        monkeypatch.setattr(copy, "_current_block_height", lambda: 1000)
        out = await copy.handle_copy("add 0x" + "a" * 40 + " 0.001")
        assert "Follow added" in out

    async def test_list(self, tmp_state):
        out = await copy.handle_copy("list")
        assert "No /copy follows" in out

    async def test_ls_alias(self, tmp_state):
        out = await copy.handle_copy("ls")
        assert "No /copy follows" in out

    async def test_pause(self, tmp_state):
        out = await copy.handle_copy("pause copy_xxx")
        assert "No follow found" in out

    async def test_resume(self, tmp_state):
        out = await copy.handle_copy("resume copy_xxx")
        assert "No follow found" in out

    async def test_cancel(self, tmp_state):
        out = await copy.handle_copy("cancel copy_xxx")
        assert "No follow found" in out

    async def test_rm_alias(self, tmp_state):
        out = await copy.handle_copy("rm copy_xxx")
        assert "No follow found" in out

    async def test_remove_alias(self, tmp_state):
        out = await copy.handle_copy("remove copy_xxx")
        assert "No follow found" in out

    async def test_edit(self, tmp_state):
        out = await copy.handle_copy("edit copy_xxx eth_per_copy 0.01")
        assert "No follow found" in out

    async def test_tick(self, tmp_state):
        out = await copy.handle_copy("tick")
        assert "No copy follows" in out

    async def test_status(self, tmp_state):
        out = await copy.handle_copy("status")
        assert "idle" in out

    async def test_history(self, tmp_state):
        out = await copy.handle_copy("history copy_xxx")
        assert "No follow" in out

    async def test_record_swallows(self, monkeypatch, tmp_state):
        import clawmes.services.command_history as ch

        def _boom(*a, **k):
            raise RuntimeError("hist broken")

        monkeypatch.setattr(ch, "record_command_call", _boom)
        out = await copy.handle_copy("")
        assert "Copy-trade" in out


# ── register ───────────────────────────────────────────────────────


class TestRegister:
    def test_register_wires_command(self):
        registered: list[dict] = []

        class Ctx:
            def register_command(self, **kwargs):
                registered.append(kwargs)

        copy.register(Ctx())
        assert len(registered) == 1
        assert registered[0]["name"] == "copy"


# ── tick wrapper renders Processed line ────────────────────────────


class TestManualTick:
    async def test_with_activity(
        self, tmp_state, fake_basescan, monkeypatch, fake_wallet, fake_defi_swap
    ):
        fake_defi_swap["payload"] = {
            "isError": False,
            "details": {"tx_hash": "0xfeed"},
        }
        _add_basic_follow(monkeypatch)
        wallet = copy._load_state()["follows"][0]["wallet"]
        fake_basescan["tokentx"] = {
            "status": "1",
            "result": [
                {
                    "to": wallet,
                    "contractAddress": "0x" + "B" * 40,
                    "blockNumber": "2000",
                    "hash": "0xseen",
                }
            ],
        }
        out = await copy._cmd_tick()
        assert "Processed 1" in out
