"""Tests for the /sniper slash command."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest

from clawmes.commands import sniper


@dataclass
class _FakeWalletState:
    connected: bool = True
    address: str = "0x" + "1" * 40


@pytest.fixture
def tmp_state(tmp_path, monkeypatch):
    p = tmp_path / "configs.json"
    monkeypatch.setattr(sniper, "_configs_path", lambda: p)
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
def fake_http(monkeypatch):
    """Patch ``http_get`` to return canned /api/launches bodies."""
    state: dict[str, Any] = {"body": None, "raises": None}

    def _fake(url, *, params=None, timeout=None):  # noqa: ARG001
        if state["raises"] is not None:
            raise state["raises"]
        return state["body"]

    monkeypatch.setattr(sniper, "http_get", _fake)
    return state


@pytest.fixture
def fake_defi_swap(monkeypatch):
    state: dict[str, Any] = {"payload": None, "raises": None}

    def _fake(args):  # noqa: ARG001
        if state["raises"] is not None:
            raise state["raises"]
        return json.dumps(state["payload"])

    import clawmes.tools.defi_swap as mod

    monkeypatch.setattr(mod, "defi_swap", _fake)
    return state


def _add_config(sender="u", eth="0.005"):
    return sniper._cmd_add(sender, [eth])


# ── helpers ────────────────────────────────────────────────────────


class TestHelpers:
    def test_short_unchanged(self):
        assert sniper._short("0x123") == "0x123"

    def test_short_truncated(self):
        assert "…" in sniper._short("0x" + "a" * 40)

    def test_short_non_str(self):
        assert sniper._short(None) == "None"  # type: ignore[arg-type]

    def test_now_iso(self):
        assert sniper._now_iso().endswith("Z")

    def test_new_id(self):
        assert sniper._new_id().startswith("snipe_")

    def test_now_epoch(self):
        assert sniper._now_epoch() > 0

    def test_split_flags(self):
        pos, flags = sniper._split_flags(["a", "--x", "1"])
        assert pos == ["a"]
        assert flags == {"x": "1"}

    def test_split_flags_trailing(self):
        pos, flags = sniper._split_flags(["--bare"])
        assert flags == {"bare": ""}


class TestStateIO:
    def test_load_missing(self, tmp_state):
        assert sniper._load_state() == {"configs": []}

    def test_load_bad_json(self, tmp_state):
        tmp_state.write_text("not-json")
        assert sniper._load_state() == {"configs": []}

    def test_load_wrong_shape(self, tmp_state):
        tmp_state.write_text(json.dumps({"configs": "not-list"}))
        assert sniper._load_state() == {"configs": []}

    def test_load_not_dict(self, tmp_state):
        tmp_state.write_text(json.dumps([]))
        assert sniper._load_state() == {"configs": []}

    def test_roundtrip(self, tmp_state):
        state = {"configs": [{"id": "x"}]}
        sniper._save_state(state)
        assert sniper._load_state() == state

    def test_default_path(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        assert sniper._configs_path().name == "configs.json"


# ── _extract_launches ──────────────────────────────────────────────


class TestExtractLaunches:
    def test_top_level_list(self):
        assert sniper._extract_launches([{"a": 1}, "junk"]) == [{"a": 1}]

    def test_dict_keys(self):
        body = {"launches": [{"x": 1}]}
        assert sniper._extract_launches(body) == [{"x": 1}]

    def test_dict_fallback(self):
        body = {"results": [{"x": 1}]}
        assert sniper._extract_launches(body) == [{"x": 1}]

    def test_empty(self):
        assert sniper._extract_launches({}) == []
        assert sniper._extract_launches("not-a-dict") == []
        assert sniper._extract_launches({"launches": "not-list"}) == []


# ── _parse_launch_epoch ────────────────────────────────────────────


class TestParseLaunchEpoch:
    def test_int_seconds(self):
        assert sniper._parse_launch_epoch({"timestamp": 1700000000}) == 1700000000

    def test_int_ms(self):
        assert sniper._parse_launch_epoch({"timestamp": 1700000000000}) == 1700000000

    def test_iso_string_z(self):
        result = sniper._parse_launch_epoch({"createdAt": "2026-05-27T01:00:00Z"})
        assert result > 0

    def test_iso_string_offset(self):
        result = sniper._parse_launch_epoch({"deployedAt": "2026-05-27T01:00:00+00:00"})
        assert result > 0

    def test_bad_string(self):
        assert sniper._parse_launch_epoch({"timestamp": "garbage"}) == 0

    def test_no_timestamp(self):
        assert sniper._parse_launch_epoch({}) == 0

    def test_fallback_keys(self):
        # ts is the last key in the lookup order.
        assert sniper._parse_launch_epoch({"ts": 1700000000}) == 1700000000


# ── /sniper add ────────────────────────────────────────────────────


class TestCmdAdd:
    def test_gate_blocks_non_unlimited(self, tmp_state, monkeypatch):
        """UNLIMITED tier required to /sniper add — gate stub returns error."""
        import clawmes.services.token_gate as tg

        monkeypatch.setattr(tg, "check_tier_or_error", lambda *a, **k: "Clawmes Unlimited required")
        out = sniper._cmd_add("u", ["0.005"])
        assert "Clawmes Unlimited required" in out

    def test_usage(self, tmp_state):
        out = sniper._cmd_add("u", [])
        assert "Usage:" in out

    def test_bad_eth(self, tmp_state):
        out = sniper._cmd_add("u", ["abc"])
        assert "must be a number" in out

    def test_zero_eth(self, tmp_state):
        out = sniper._cmd_add("u", ["0"])
        assert "must be positive" in out

    def test_bad_max_buys(self, tmp_state):
        out = sniper._cmd_add("u", ["0.005", "--max-buys", "x"])
        assert "integer" in out

    def test_max_buys_zero(self, tmp_state):
        out = sniper._cmd_add("u", ["0.005", "--max-buys", "0"])
        assert ">= 1" in out

    def test_bad_slippage(self, tmp_state):
        out = sniper._cmd_add("u", ["0.005", "--slippage", "x"])
        assert "integer" in out

    def test_slippage_range(self, tmp_state):
        out = sniper._cmd_add("u", ["0.005", "--slippage", "99999"])
        assert "0–10000" in out

    def test_bad_max_age(self, tmp_state):
        out = sniper._cmd_add("u", ["0.005", "--max-age", "x"])
        assert "integer" in out

    def test_max_age_zero(self, tmp_state):
        out = sniper._cmd_add("u", ["0.005", "--max-age", "0"])
        assert ">= 1" in out

    def test_bad_max_mcap(self, tmp_state):
        out = sniper._cmd_add("u", ["0.005", "--max-mcap", "x"])
        assert "must be a number" in out

    def test_max_mcap_non_positive(self, tmp_state):
        out = sniper._cmd_add("u", ["0.005", "--max-mcap", "0"])
        assert "must be positive" in out

    def test_invalid_regex(self, tmp_state):
        out = sniper._cmd_add("u", ["0.005", "--symbol-filter", "[unclosed"])
        assert "not a valid regex" in out

    def test_minimal_success(self, tmp_state):
        out = _add_config()
        assert "Sniper added" in out
        c = sniper._load_state()["configs"][0]
        assert c["eth_amount"] == 0.005
        assert c["source_filter"] is None
        assert c["symbol_filter"] is None

    def test_full_flag_set(self, tmp_state):
        out = sniper._cmd_add(
            "u",
            [
                "0.005",
                "--max-buys",
                "3",
                "--source",
                "clawmes",
                "--symbol-filter",
                "DOG|CAT",
                "--max-mcap",
                "100000",
                "--max-age",
                "300",
                "--slippage",
                "200",
            ],
        )
        assert "Sniper added" in out
        c = sniper._load_state()["configs"][0]
        assert c["max_buys"] == 3
        assert c["source_filter"] == "clawmes"
        assert c["symbol_filter"] == "DOG|CAT"
        assert c["max_mcap_usd"] == 100000.0
        assert c["max_age_seconds"] == 300
        assert c["slippage_bps"] == 200


# ── /sniper list / mutate / cancel ─────────────────────────────────


class TestList:
    def test_empty(self, tmp_state):
        assert "No sniper configs" in sniper._cmd_list("u")

    def test_lists_mine(self, tmp_state):
        _add_config("alice")
        _add_config("bob")
        out = sniper._cmd_list("alice")
        # Each line in the output corresponds to one config; only alice's
        # config should be present.
        assert out.count("0.005 ETH/snipe") == 1


class TestMutate:
    def test_pause_usage(self, tmp_state):
        out = sniper._cmd_mutate("u", [], status="paused", verb="paused")
        assert "Usage:" in out

    def test_pause_not_found(self, tmp_state):
        out = sniper._cmd_mutate("u", ["snipe_xxx"], status="paused", verb="paused")
        assert "No sniper config found" in out

    def test_pause_resume(self, tmp_state):
        out = _add_config()
        cid = next(w for w in out.split() if w.startswith("snipe_"))
        sniper._cmd_mutate("u", [cid], status="paused", verb="paused")
        assert sniper._load_state()["configs"][0]["status"] == "paused"
        sniper._cmd_mutate("u", [cid], status="active", verb="resumed")
        assert sniper._load_state()["configs"][0]["status"] == "active"


class TestCancel:
    def test_usage(self, tmp_state):
        assert "Usage:" in sniper._cmd_cancel("u", [])

    def test_not_found(self, tmp_state):
        assert "No sniper config" in sniper._cmd_cancel("u", ["snipe_xxx"])

    def test_success(self, tmp_state):
        out = _add_config()
        cid = next(w for w in out.split() if w.startswith("snipe_"))
        sniper._cmd_cancel("u", [cid])
        assert sniper._load_state()["configs"] == []


# ── /sniper edit ───────────────────────────────────────────────────


class TestEdit:
    def test_usage(self, tmp_state):
        assert "Usage:" in sniper._cmd_edit("u", [])

    def test_unknown_field(self, tmp_state):
        out = _add_config()
        cid = next(w for w in out.split() if w.startswith("snipe_"))
        assert "Unknown field" in sniper._cmd_edit("u", [cid, "garbage", "1"])

    def test_not_found(self, tmp_state):
        assert "No sniper config" in sniper._cmd_edit("u", ["snipe_xxx", "eth_amount", "0.01"])

    def test_eth_amount(self, tmp_state):
        out = _add_config()
        cid = next(w for w in out.split() if w.startswith("snipe_"))
        sniper._cmd_edit("u", [cid, "eth_amount", "0.01"])
        assert sniper._load_state()["configs"][0]["eth_amount"] == 0.01

    def test_eth_amount_bad(self, tmp_state):
        out = _add_config()
        cid = next(w for w in out.split() if w.startswith("snipe_"))
        assert "must be a number" in sniper._cmd_edit("u", [cid, "eth_amount", "x"])

    def test_eth_amount_non_positive(self, tmp_state):
        out = _add_config()
        cid = next(w for w in out.split() if w.startswith("snipe_"))
        assert "must be positive" in sniper._cmd_edit("u", [cid, "eth_amount", "0"])

    def test_max_buys(self, tmp_state):
        out = _add_config()
        cid = next(w for w in out.split() if w.startswith("snipe_"))
        sniper._cmd_edit("u", [cid, "max_buys", "5"])
        assert sniper._load_state()["configs"][0]["max_buys"] == 5

    def test_max_buys_bad(self, tmp_state):
        out = _add_config()
        cid = next(w for w in out.split() if w.startswith("snipe_"))
        assert "integer" in sniper._cmd_edit("u", [cid, "max_buys", "x"])

    def test_max_buys_zero(self, tmp_state):
        out = _add_config()
        cid = next(w for w in out.split() if w.startswith("snipe_"))
        assert ">= 1" in sniper._cmd_edit("u", [cid, "max_buys", "0"])

    def test_slippage_bps(self, tmp_state):
        out = _add_config()
        cid = next(w for w in out.split() if w.startswith("snipe_"))
        sniper._cmd_edit("u", [cid, "slippage_bps", "200"])
        assert sniper._load_state()["configs"][0]["slippage_bps"] == 200

    def test_slippage_bps_bad(self, tmp_state):
        out = _add_config()
        cid = next(w for w in out.split() if w.startswith("snipe_"))
        assert "integer" in sniper._cmd_edit("u", [cid, "slippage_bps", "x"])

    def test_slippage_bps_range(self, tmp_state):
        out = _add_config()
        cid = next(w for w in out.split() if w.startswith("snipe_"))
        assert "0–10000" in sniper._cmd_edit("u", [cid, "slippage_bps", "99999"])

    def test_source_filter(self, tmp_state):
        out = _add_config()
        cid = next(w for w in out.split() if w.startswith("snipe_"))
        sniper._cmd_edit("u", [cid, "source_filter", "clawmes"])
        assert sniper._load_state()["configs"][0]["source_filter"] == "clawmes"

    def test_source_filter_none(self, tmp_state):
        out = _add_config()
        cid = next(w for w in out.split() if w.startswith("snipe_"))
        sniper._cmd_edit("u", [cid, "source_filter", "NONE"])
        assert sniper._load_state()["configs"][0]["source_filter"] is None

    def test_symbol_filter(self, tmp_state):
        out = _add_config()
        cid = next(w for w in out.split() if w.startswith("snipe_"))
        sniper._cmd_edit("u", [cid, "symbol_filter", "DOG"])
        assert sniper._load_state()["configs"][0]["symbol_filter"] == "DOG"

    def test_symbol_filter_none(self, tmp_state):
        out = _add_config()
        cid = next(w for w in out.split() if w.startswith("snipe_"))
        sniper._cmd_edit("u", [cid, "symbol_filter", "none"])
        assert sniper._load_state()["configs"][0]["symbol_filter"] is None

    def test_symbol_filter_invalid(self, tmp_state):
        out = _add_config()
        cid = next(w for w in out.split() if w.startswith("snipe_"))
        assert "not a valid regex" in sniper._cmd_edit("u", [cid, "symbol_filter", "[bad"])

    def test_max_mcap_value(self, tmp_state):
        out = _add_config()
        cid = next(w for w in out.split() if w.startswith("snipe_"))
        sniper._cmd_edit("u", [cid, "max_mcap_usd", "100000"])
        assert sniper._load_state()["configs"][0]["max_mcap_usd"] == 100000.0

    def test_max_mcap_none(self, tmp_state):
        out = _add_config()
        cid = next(w for w in out.split() if w.startswith("snipe_"))
        sniper._cmd_edit("u", [cid, "max_mcap_usd", "none"])
        assert sniper._load_state()["configs"][0]["max_mcap_usd"] is None

    def test_max_mcap_bad(self, tmp_state):
        out = _add_config()
        cid = next(w for w in out.split() if w.startswith("snipe_"))
        assert "must be a number" in sniper._cmd_edit("u", [cid, "max_mcap_usd", "x"])

    def test_max_mcap_non_positive(self, tmp_state):
        out = _add_config()
        cid = next(w for w in out.split() if w.startswith("snipe_"))
        assert "must be positive" in sniper._cmd_edit("u", [cid, "max_mcap_usd", "0"])

    def test_max_age_seconds(self, tmp_state):
        out = _add_config()
        cid = next(w for w in out.split() if w.startswith("snipe_"))
        sniper._cmd_edit("u", [cid, "max_age_seconds", "300"])
        assert sniper._load_state()["configs"][0]["max_age_seconds"] == 300

    def test_max_age_seconds_bad(self, tmp_state):
        out = _add_config()
        cid = next(w for w in out.split() if w.startswith("snipe_"))
        assert "integer" in sniper._cmd_edit("u", [cid, "max_age_seconds", "x"])

    def test_max_age_seconds_zero(self, tmp_state):
        out = _add_config()
        cid = next(w for w in out.split() if w.startswith("snipe_"))
        assert ">= 1" in sniper._cmd_edit("u", [cid, "max_age_seconds", "0"])


# ── _find ───────────────────────────────────────────────────────────


class TestFind:
    def test_match(self, tmp_state):
        out = _add_config()
        cid = next(w for w in out.split() if w.startswith("snipe_"))
        assert sniper._find(sniper._load_state(), cid, "u") is not None

    def test_wrong_sender(self, tmp_state):
        out = _add_config("alice")
        cid = next(w for w in out.split() if w.startswith("snipe_"))
        assert sniper._find(sniper._load_state(), cid, "bob") is None

    def test_missing(self, tmp_state):
        assert sniper._find(sniper._load_state(), "snipe_xxx", "u") is None


# ── _submit_snipe ───────────────────────────────────────────────────


class TestSubmitSnipe:
    def test_no_wallet(self, fake_wallet, fake_defi_swap):
        fake_wallet.connected = False
        config = {"eth_amount": 0.005, "slippage_bps": 100}
        out = sniper._submit_snipe(config, "0x" + "1" * 40)
        assert out["status"] == "no_wallet"

    def test_success(self, fake_wallet, fake_defi_swap):
        fake_defi_swap["payload"] = {
            "isError": False,
            "details": {"tx_hash": "0xfeed"},
        }
        config = {"eth_amount": 0.005, "slippage_bps": 100}
        out = sniper._submit_snipe(config, "0x" + "1" * 40)
        assert out["status"] == "ok"
        assert "0xfeed" in out["tx_hash"]

    def test_swap_raises(self, fake_wallet, fake_defi_swap):
        fake_defi_swap["raises"] = RuntimeError("rpc down")
        config = {"eth_amount": 0.005, "slippage_bps": 100}
        out = sniper._submit_snipe(config, "0x" + "1" * 40)
        assert out["status"] == "error"
        assert "rpc down" in out["detail"]

    def test_swap_bad_json(self, fake_wallet, monkeypatch):
        import clawmes.tools.defi_swap as mod

        monkeypatch.setattr(mod, "defi_swap", lambda *a, **k: "not-json")
        config = {"eth_amount": 0.005, "slippage_bps": 100}
        out = sniper._submit_snipe(config, "0x" + "1" * 40)
        assert out["status"] == "error"

    def test_swap_isError(self, fake_wallet, fake_defi_swap):
        fake_defi_swap["payload"] = {
            "isError": True,
            "content": [{"text": "no route"}],
        }
        config = {"eth_amount": 0.005, "slippage_bps": 100}
        out = sniper._submit_snipe(config, "0x" + "1" * 40)
        assert out["status"] == "error"
        assert "no route" in out["detail"]

    def test_swap_isError_no_content(self, fake_wallet, fake_defi_swap):
        fake_defi_swap["payload"] = {"isError": True}
        config = {"eth_amount": 0.005, "slippage_bps": 100}
        out = sniper._submit_snipe(config, "0x" + "1" * 40)
        assert out["status"] == "error"


# ── _process_config + _run_due_sync ────────────────────────────────


def _seed_config(sender="u", **overrides):
    """Add a basic config then mutate fields in place via _save_state."""
    _add_config(sender)
    s = sniper._load_state()
    s["configs"][0].update(overrides)
    sniper._save_state(s)
    return s["configs"][0]["id"]


class TestRunDue:
    def test_no_active_configs(self, tmp_state, fake_http):
        # No configs at all → no fetch needed → no fires.
        assert sniper._run_due_sync() == 0

    def test_fetch_error(self, tmp_state, fake_http):
        _seed_config()
        fake_http["raises"] = RuntimeError("upstream down")
        n, lines = sniper._run_due_with_lines()
        assert n == 0
        assert any("fetch error" in line for line in lines)

    def test_no_launches(self, tmp_state, fake_http):
        _seed_config()
        fake_http["body"] = {"launches": []}
        # last_seen_epoch in seed is now-ish; advance and confirm no fires.
        n = sniper._run_due_sync()
        assert n == 0
        # last_seen_epoch should have advanced.
        state = sniper._load_state()
        assert state["configs"][0]["last_seen_epoch"] > 0

    def test_paused_skipped(self, tmp_state, fake_http, fake_wallet, fake_defi_swap):
        _seed_config(status="paused")
        fake_http["body"] = {
            "launches": [
                {
                    "contractAddress": "0x" + "T" * 40,
                    "symbol": "TKN",
                    "timestamp": sniper._now_epoch() - 10,
                }
            ]
        }
        assert sniper._run_due_sync() == 0

    def test_too_old(self, tmp_state, fake_http, fake_wallet, fake_defi_swap):
        _seed_config(last_seen_epoch=0, max_age_seconds=60)
        # Launch is 10 minutes old, max_age is 60 sec → skipped.
        fake_http["body"] = {
            "launches": [
                {
                    "contractAddress": "0x" + "T" * 40,
                    "symbol": "TKN",
                    "timestamp": sniper._now_epoch() - 600,
                }
            ]
        }
        assert sniper._run_due_sync() == 0

    def test_already_seen_skipped(self, tmp_state, fake_http, fake_wallet, fake_defi_swap):
        now = sniper._now_epoch()
        _seed_config(last_seen_epoch=now + 100, max_age_seconds=10_000)
        fake_http["body"] = {
            "launches": [
                {
                    "contractAddress": "0x" + "T" * 40,
                    "symbol": "TKN",
                    "timestamp": now,  # before last_seen
                }
            ]
        }
        assert sniper._run_due_sync() == 0

    def test_bad_address_skipped(self, tmp_state, fake_http, fake_wallet, fake_defi_swap):
        _seed_config(last_seen_epoch=0)
        fake_http["body"] = {
            "launches": [
                {"contractAddress": "junk", "symbol": "TKN", "timestamp": sniper._now_epoch()}
            ]
        }
        assert sniper._run_due_sync() == 0

    def test_source_filter_mismatch(self, tmp_state, fake_http, fake_wallet, fake_defi_swap):
        _seed_config(last_seen_epoch=0, source_filter="clawmes")
        fake_http["body"] = {
            "launches": [
                {
                    "contractAddress": "0x" + "T" * 40,
                    "symbol": "TKN",
                    "source": "4claw",
                    "timestamp": sniper._now_epoch(),
                }
            ]
        }
        assert sniper._run_due_sync() == 0

    def test_symbol_filter_mismatch(self, tmp_state, fake_http, fake_wallet, fake_defi_swap):
        _seed_config(last_seen_epoch=0, symbol_filter="DOG")
        fake_http["body"] = {
            "launches": [
                {
                    "contractAddress": "0x" + "T" * 40,
                    "symbol": "CAT",
                    "timestamp": sniper._now_epoch(),
                }
            ]
        }
        assert sniper._run_due_sync() == 0

    def test_mcap_too_high(self, tmp_state, fake_http, fake_wallet, fake_defi_swap):
        _seed_config(last_seen_epoch=0, max_mcap_usd=10000)
        fake_http["body"] = {
            "launches": [
                {
                    "contractAddress": "0x" + "T" * 40,
                    "symbol": "TKN",
                    "marketCap": 1000000,
                    "timestamp": sniper._now_epoch(),
                }
            ]
        }
        assert sniper._run_due_sync() == 0

    def test_mcap_unparseable_passes(self, tmp_state, fake_http, fake_wallet, fake_defi_swap):
        """Unparseable mcap should fall through to the next filter, not reject."""
        fake_defi_swap["payload"] = {
            "isError": False,
            "details": {"tx_hash": "0xfeed"},
        }
        _seed_config(last_seen_epoch=0, max_mcap_usd=10000)
        fake_http["body"] = {
            "launches": [
                {
                    "contractAddress": "0x" + "T" * 40,
                    "symbol": "TKN",
                    "marketCap": "not-a-number",
                    "timestamp": sniper._now_epoch(),
                }
            ]
        }
        n = sniper._run_due_sync()
        assert n == 1

    def test_successful_snipe(self, tmp_state, fake_http, fake_wallet, fake_defi_swap):
        fake_defi_swap["payload"] = {
            "isError": False,
            "details": {"tx_hash": "0xfeed"},
        }
        _seed_config(last_seen_epoch=0)
        fake_http["body"] = {
            "launches": [
                {
                    "contractAddress": "0x" + "T" * 40,
                    "symbol": "TKN",
                    "timestamp": sniper._now_epoch(),
                }
            ]
        }
        n = sniper._run_due_sync()
        assert n == 1
        c = sniper._load_state()["configs"][0]
        assert c["buys_made"] == 1

    def test_max_buys_exhausts(self, tmp_state, fake_http, fake_wallet, fake_defi_swap):
        """When max_buys is reached on the same tick, status flips to exhausted."""
        fake_defi_swap["payload"] = {
            "isError": False,
            "details": {"tx_hash": "0xfeed"},
        }
        _seed_config(last_seen_epoch=0, max_buys=1)
        fake_http["body"] = {
            "launches": [
                {
                    "contractAddress": "0x" + f"{i:040x}",
                    "symbol": f"TKN{i}",
                    "timestamp": sniper._now_epoch() + i,
                }
                for i in range(3)
            ]
        }
        sniper._run_due_sync()
        c = sniper._load_state()["configs"][0]
        assert c["status"] == "exhausted"
        assert c["buys_made"] == 1

    def test_per_config_error_caught(self, tmp_state, fake_http, fake_wallet, monkeypatch):
        _seed_config(last_seen_epoch=0)
        fake_http["body"] = {
            "launches": [
                {
                    "contractAddress": "0x" + "T" * 40,
                    "symbol": "TKN",
                    "timestamp": sniper._now_epoch(),
                }
            ]
        }

        def _boom(*a, **k):
            raise RuntimeError("processing exploded")

        monkeypatch.setattr(sniper, "_process_config", _boom)
        n, lines = sniper._run_due_with_lines()
        assert n == 0
        assert any("error" in line for line in lines)


class TestManualTick:
    async def test_no_fires(self, tmp_state):
        out = await sniper._cmd_tick()
        assert "No new launches matched" in out

    async def test_with_fires(self, tmp_state, fake_http, fake_wallet, fake_defi_swap):
        fake_defi_swap["payload"] = {
            "isError": False,
            "details": {"tx_hash": "0xfeed"},
        }
        _seed_config(last_seen_epoch=0)
        fake_http["body"] = {
            "launches": [
                {
                    "contractAddress": "0x" + "T" * 40,
                    "symbol": "TKN",
                    "timestamp": sniper._now_epoch(),
                }
            ]
        }
        out = await sniper._cmd_tick()
        assert "Fired 1" in out


# ── /sniper status / history ───────────────────────────────────────


class TestStatus:
    def test_empty(self, tmp_state):
        assert "idle" in sniper._cmd_status("u")

    def test_summarizes(self, tmp_state):
        _add_config("alice")
        s = sniper._load_state()
        s["configs"][0]["snipes"] = [
            {"at": "x", "result": {"status": "ok"}},
            {"at": "y", "result": {"status": "error"}},
        ]
        sniper._save_state(s)
        out = sniper._cmd_status("alice")
        assert "Total snipes: 2 attempted, 1 successful" in out

    def test_service_health_swallowed(self, monkeypatch, tmp_state):
        import clawmes.services.sniper_scheduler as svc_mod

        monkeypatch.setattr(
            svc_mod,
            "get_sniper_scheduler_service",
            lambda: (_ for _ in ()).throw(RuntimeError("svc")),
        )
        _add_config()
        out = sniper._cmd_status("u")
        assert "Service:" not in out

    def test_service_health_present(self, tmp_state):
        from clawmes.services import sniper_scheduler as svc_mod

        svc_mod._reset_for_tests()
        svc = svc_mod.get_sniper_scheduler_service()
        svc.start()
        try:
            _add_config()
            out = sniper._cmd_status("u")
            assert "Service:" in out
        finally:
            svc.stop()
            svc_mod._reset_for_tests()


class TestHistory:
    def test_usage(self, tmp_state):
        assert "Usage:" in sniper._cmd_history("u", [])

    def test_not_found(self, tmp_state):
        assert "No sniper config" in sniper._cmd_history("u", ["snipe_xxx"])

    def test_no_snipes(self, tmp_state):
        out = _add_config()
        cid = next(w for w in out.split() if w.startswith("snipe_"))
        assert "no snipes yet" in sniper._cmd_history("u", [cid])

    def test_with_snipes(self, tmp_state):
        out = _add_config()
        cid = next(w for w in out.split() if w.startswith("snipe_"))
        s = sniper._load_state()
        s["configs"][0]["snipes"] = [
            {
                "at": "2026-05-27T01:00:00Z",
                "token": "0x" + "T" * 40,
                "symbol": "TKN",
                "result": {"status": "ok"},
            }
        ]
        sniper._save_state(s)
        out = sniper._cmd_history("u", [cid])
        assert "Snipes for" in out
        assert "TKN" in out


# ── handle_sniper ──────────────────────────────────────────────────


class TestHandle:
    async def test_empty(self, tmp_state):
        out = await sniper.handle_sniper("")
        assert "Sniper" in out

    async def test_unknown(self, tmp_state):
        out = await sniper.handle_sniper("garbage")
        assert "Unknown subcommand" in out

    async def test_add(self, tmp_state):
        out = await sniper.handle_sniper("add 0.005")
        assert "Sniper added" in out

    async def test_list(self, tmp_state):
        out = await sniper.handle_sniper("list")
        assert "No sniper configs" in out

    async def test_ls(self, tmp_state):
        out = await sniper.handle_sniper("ls")
        assert "No sniper configs" in out

    async def test_pause(self, tmp_state):
        out = await sniper.handle_sniper("pause snipe_xxx")
        assert "No sniper config" in out

    async def test_resume(self, tmp_state):
        out = await sniper.handle_sniper("resume snipe_xxx")
        assert "No sniper config" in out

    async def test_cancel(self, tmp_state):
        out = await sniper.handle_sniper("cancel snipe_xxx")
        assert "No sniper config" in out

    async def test_rm_alias(self, tmp_state):
        out = await sniper.handle_sniper("rm snipe_xxx")
        assert "No sniper config" in out

    async def test_remove_alias(self, tmp_state):
        out = await sniper.handle_sniper("remove snipe_xxx")
        assert "No sniper config" in out

    async def test_edit(self, tmp_state):
        out = await sniper.handle_sniper("edit snipe_xxx eth_amount 0.01")
        assert "No sniper config" in out

    async def test_tick(self, tmp_state):
        out = await sniper.handle_sniper("tick")
        assert "No new launches" in out

    async def test_status(self, tmp_state):
        out = await sniper.handle_sniper("status")
        assert "idle" in out

    async def test_history(self, tmp_state):
        out = await sniper.handle_sniper("history snipe_xxx")
        assert "No sniper config" in out

    async def test_record_swallows(self, monkeypatch, tmp_state):
        import clawmes.services.command_history as ch

        def _boom(*a, **k):
            raise RuntimeError("hist broken")

        monkeypatch.setattr(ch, "record_command_call", _boom)
        out = await sniper.handle_sniper("")
        assert "Sniper" in out


# ── register ───────────────────────────────────────────────────────


class TestRegister:
    def test_register(self):
        registered: list[dict] = []

        class Ctx:
            def register_command(self, **kwargs):
                registered.append(kwargs)

        sniper.register(Ctx())
        assert len(registered) == 1
        assert registered[0]["name"] == "sniper"
