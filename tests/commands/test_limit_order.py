"""Tests for the /limit_order slash command."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest

from clawmes.commands import limit_order


@dataclass
class _FakeWalletState:
    connected: bool = True
    address: str = "0x" + "1" * 40


@pytest.fixture
def tmp_state(tmp_path, monkeypatch):
    p = tmp_path / "orders.json"
    monkeypatch.setattr(limit_order, "_orders_path", lambda: p)
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
def fake_defi_price(monkeypatch):
    state: dict[str, Any] = {"payload": None, "raises": None}

    def _fake(args):  # noqa: ARG001
        if state["raises"] is not None:
            raise state["raises"]
        return json.dumps(state["payload"])

    import clawmes.tools.defi_price as mod

    monkeypatch.setattr(mod, "defi_price", _fake)
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


def _add_buy(sender="u", token="CLAWNCH", eth="0.01", usd="0.00001"):
    return limit_order._cmd_add(sender, ["buy", token, eth, "below", usd])


def _add_sell(sender="u", token="CLAWNCH", amount="1000000", usd="0.0001"):
    return limit_order._cmd_add(sender, ["sell", token, amount, "above", usd])


# ── helpers ────────────────────────────────────────────────────────


class TestHelpers:
    def test_short_unchanged(self):
        assert limit_order._short("0x123") == "0x123"

    def test_short_truncated(self):
        out = limit_order._short("0x" + "a" * 40)
        assert "…" in out

    def test_short_non_str(self):
        assert limit_order._short(None) == "None"  # type: ignore[arg-type]

    def test_now_iso(self):
        s = limit_order._now_iso()
        assert "T" in s and s.endswith("Z")

    def test_new_id(self):
        assert limit_order._new_id().startswith("lim_")

    def test_now_epoch(self):
        assert limit_order._now_epoch() > 0

    def test_split_flags(self):
        pos, flags = limit_order._split_flags(["a", "--x", "1"])
        assert pos == ["a"]
        assert flags == {"x": "1"}

    def test_split_flags_trailing(self):
        pos, flags = limit_order._split_flags(["--bare"])
        assert flags == {"bare": ""}


class TestStateIO:
    def test_load_missing(self, tmp_state):
        assert limit_order._load_state() == {"orders": []}

    def test_load_bad_json(self, tmp_state):
        tmp_state.write_text("not-json")
        assert limit_order._load_state() == {"orders": []}

    def test_load_wrong_shape(self, tmp_state):
        tmp_state.write_text(json.dumps({"orders": "not-list"}))
        assert limit_order._load_state() == {"orders": []}

    def test_load_not_dict(self, tmp_state):
        tmp_state.write_text(json.dumps([]))
        assert limit_order._load_state() == {"orders": []}

    def test_roundtrip(self, tmp_state):
        s = {"orders": [{"id": "x"}]}
        limit_order._save_state(s)
        assert limit_order._load_state() == s

    def test_default_path(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        assert limit_order._orders_path().name == "orders.json"


# ── /limit_order add ───────────────────────────────────────────────


class TestCmdAdd:
    def test_usage(self, tmp_state):
        assert "Usage:" in limit_order._cmd_add("u", [])

    def test_unknown_kind(self, tmp_state):
        out = limit_order._cmd_add("u", ["garbage"])
        assert "Unknown order type" in out

    def test_buy_usage(self, tmp_state):
        out = limit_order._cmd_add("u", ["buy"])
        assert "Usage:" in out

    def test_buy_missing_below(self, tmp_state):
        out = limit_order._cmd_add("u", ["buy", "CLAWNCH", "0.01", "wrong", "0.0001"])
        assert "Usage:" in out

    def test_sell_usage(self, tmp_state):
        out = limit_order._cmd_add("u", ["sell"])
        assert "Usage:" in out

    def test_sell_missing_above(self, tmp_state):
        out = limit_order._cmd_add("u", ["sell", "CLAWNCH", "1000", "wrong", "0.0001"])
        assert "Usage:" in out

    def test_bad_amount(self, tmp_state):
        out = limit_order._cmd_add("u", ["buy", "CLAWNCH", "abc", "below", "0.0001"])
        assert "must be a number" in out

    def test_zero_amount(self, tmp_state):
        out = limit_order._cmd_add("u", ["buy", "CLAWNCH", "0", "below", "0.0001"])
        assert "must be positive" in out

    def test_bad_usd(self, tmp_state):
        out = limit_order._cmd_add("u", ["buy", "CLAWNCH", "0.01", "below", "abc"])
        assert "usd threshold must be a number" in out

    def test_zero_usd(self, tmp_state):
        out = limit_order._cmd_add("u", ["buy", "CLAWNCH", "0.01", "below", "0"])
        assert "usd threshold must be positive" in out

    def test_bad_slippage(self, tmp_state):
        out = limit_order._cmd_add(
            "u",
            ["buy", "CLAWNCH", "0.01", "below", "0.0001", "--slippage", "x"],
        )
        assert "integer" in out

    def test_slippage_range(self, tmp_state):
        out = limit_order._cmd_add(
            "u",
            ["buy", "CLAWNCH", "0.01", "below", "0.0001", "--slippage", "99999"],
        )
        assert "0–10000" in out

    def test_bad_max_attempts(self, tmp_state):
        out = limit_order._cmd_add(
            "u",
            ["buy", "CLAWNCH", "0.01", "below", "0.0001", "--max-attempts", "x"],
        )
        assert "integer" in out

    def test_max_attempts_zero(self, tmp_state):
        out = limit_order._cmd_add(
            "u",
            ["buy", "CLAWNCH", "0.01", "below", "0.0001", "--max-attempts", "0"],
        )
        assert ">= 1" in out

    def test_buy_success(self, tmp_state):
        out = _add_buy()
        assert "Limit order added" in out
        o = limit_order._load_state()["orders"][0]
        assert o["type"] == "buy"
        assert o["direction"] == "below"

    def test_sell_success(self, tmp_state):
        out = _add_sell()
        assert "Limit order added" in out
        o = limit_order._load_state()["orders"][0]
        assert o["type"] == "sell"
        assert o["direction"] == "above"

    def test_all_flags(self, tmp_state):
        out = limit_order._cmd_add(
            "u",
            [
                "buy",
                "CLAWNCH",
                "0.01",
                "below",
                "0.0001",
                "--slippage",
                "50",
                "--max-attempts",
                "5",
            ],
        )
        assert "Limit order added" in out
        o = limit_order._load_state()["orders"][0]
        assert o["slippage_bps"] == 50
        assert o["max_attempts"] == 5

    def test_free_tier_cap_rejects(self, tmp_state, monkeypatch):
        import clawmes.services.token_gate as tg

        monkeypatch.setattr(tg, "check_cap_or_error", lambda *a, **k: "Free tier allows 1.")
        out = _add_buy()
        assert "Free tier allows" in out


# ── /limit_order list / mutate / cancel / resume ───────────────────


class TestList:
    def test_empty(self, tmp_state):
        assert "No limit orders" in limit_order._cmd_list("u")

    def test_lists_buy_and_sell(self, tmp_state):
        _add_buy("alice")
        _add_sell("alice")
        out = limit_order._cmd_list("alice")
        assert "BUY" in out
        assert "SELL" in out

    def test_isolation(self, tmp_state):
        _add_buy("alice")
        _add_sell("bob")
        out = limit_order._cmd_list("alice")
        assert "BUY" in out
        assert "SELL" not in out


class TestMutate:
    def test_pause_usage(self, tmp_state):
        out = limit_order._cmd_mutate("u", [], status="paused", verb="paused")
        assert "Usage:" in out

    def test_pause_not_found(self, tmp_state):
        out = limit_order._cmd_mutate("u", ["lim_xxx"], status="paused", verb="paused")
        assert "No limit order found" in out

    def test_pause_active(self, tmp_state):
        out = _add_buy()
        oid = next(w for w in out.split() if w.startswith("lim_"))
        limit_order._cmd_mutate("u", [oid], status="paused", verb="paused")
        assert limit_order._load_state()["orders"][0]["status"] == "paused"

    def test_pause_terminal_rejected(self, tmp_state):
        out = _add_buy()
        oid = next(w for w in out.split() if w.startswith("lim_"))
        # Force order into terminal state.
        s = limit_order._load_state()
        s["orders"][0]["status"] = "filled"
        limit_order._save_state(s)
        result = limit_order._cmd_mutate("u", [oid], status="paused", verb="paused")
        assert "terminal" in result


class TestResume:
    def test_usage(self, tmp_state):
        assert "Usage:" in limit_order._cmd_resume("u", [])

    def test_not_found(self, tmp_state):
        assert "No limit order found" in limit_order._cmd_resume("u", ["lim_xxx"])

    def test_only_paused_resumable(self, tmp_state):
        out = _add_buy()
        oid = next(w for w in out.split() if w.startswith("lim_"))
        # Active → can't resume (it's already active).
        result = limit_order._cmd_resume("u", [oid])
        assert "only paused" in result

    def test_resumes_paused(self, tmp_state):
        out = _add_buy()
        oid = next(w for w in out.split() if w.startswith("lim_"))
        limit_order._cmd_mutate("u", [oid], status="paused", verb="paused")
        result = limit_order._cmd_resume("u", [oid])
        assert "resumed" in result
        assert limit_order._load_state()["orders"][0]["status"] == "active"


class TestCancel:
    def test_usage(self, tmp_state):
        assert "Usage:" in limit_order._cmd_cancel("u", [])

    def test_not_found(self, tmp_state):
        assert "No limit order" in limit_order._cmd_cancel("u", ["lim_xxx"])

    def test_success(self, tmp_state):
        out = _add_buy()
        oid = next(w for w in out.split() if w.startswith("lim_"))
        result = limit_order._cmd_cancel("u", [oid])
        assert "Cancelled" in result
        assert limit_order._load_state()["orders"] == []


# ── /limit_order edit ──────────────────────────────────────────────


class TestEdit:
    def test_usage(self, tmp_state):
        assert "Usage:" in limit_order._cmd_edit("u", [])

    def test_unknown_field(self, tmp_state):
        out = _add_buy()
        oid = next(w for w in out.split() if w.startswith("lim_"))
        result = limit_order._cmd_edit("u", [oid, "garbage", "1"])
        assert "Unknown field" in result

    def test_not_found(self, tmp_state):
        out = limit_order._cmd_edit("u", ["lim_xxx", "amount", "1"])
        assert "No limit order found" in out

    def test_threshold_usd(self, tmp_state):
        out = _add_buy()
        oid = next(w for w in out.split() if w.startswith("lim_"))
        limit_order._cmd_edit("u", [oid, "threshold_usd", "0.00002"])
        assert limit_order._load_state()["orders"][0]["threshold_usd"] == 0.00002

    def test_threshold_usd_bad(self, tmp_state):
        out = _add_buy()
        oid = next(w for w in out.split() if w.startswith("lim_"))
        result = limit_order._cmd_edit("u", [oid, "threshold_usd", "abc"])
        assert "must be a number" in result

    def test_threshold_usd_non_positive(self, tmp_state):
        out = _add_buy()
        oid = next(w for w in out.split() if w.startswith("lim_"))
        result = limit_order._cmd_edit("u", [oid, "threshold_usd", "0"])
        assert "must be positive" in result

    def test_amount(self, tmp_state):
        out = _add_buy()
        oid = next(w for w in out.split() if w.startswith("lim_"))
        limit_order._cmd_edit("u", [oid, "amount", "0.05"])
        assert limit_order._load_state()["orders"][0]["amount"] == 0.05

    def test_slippage_bps(self, tmp_state):
        out = _add_buy()
        oid = next(w for w in out.split() if w.startswith("lim_"))
        limit_order._cmd_edit("u", [oid, "slippage_bps", "200"])
        assert limit_order._load_state()["orders"][0]["slippage_bps"] == 200

    def test_slippage_bps_bad(self, tmp_state):
        out = _add_buy()
        oid = next(w for w in out.split() if w.startswith("lim_"))
        result = limit_order._cmd_edit("u", [oid, "slippage_bps", "x"])
        assert "integer" in result

    def test_slippage_bps_range(self, tmp_state):
        out = _add_buy()
        oid = next(w for w in out.split() if w.startswith("lim_"))
        result = limit_order._cmd_edit("u", [oid, "slippage_bps", "99999"])
        assert "0–10000" in result

    def test_max_attempts(self, tmp_state):
        out = _add_buy()
        oid = next(w for w in out.split() if w.startswith("lim_"))
        limit_order._cmd_edit("u", [oid, "max_attempts", "10"])
        assert limit_order._load_state()["orders"][0]["max_attempts"] == 10

    def test_max_attempts_bad(self, tmp_state):
        out = _add_buy()
        oid = next(w for w in out.split() if w.startswith("lim_"))
        result = limit_order._cmd_edit("u", [oid, "max_attempts", "x"])
        assert "integer" in result

    def test_max_attempts_zero(self, tmp_state):
        out = _add_buy()
        oid = next(w for w in out.split() if w.startswith("lim_"))
        result = limit_order._cmd_edit("u", [oid, "max_attempts", "0"])
        assert ">= 1" in result


# ── _find ──────────────────────────────────────────────────────────


class TestFind:
    def test_match(self, tmp_state):
        out = _add_buy()
        oid = next(w for w in out.split() if w.startswith("lim_"))
        assert limit_order._find(limit_order._load_state(), oid, "u") is not None

    def test_wrong_sender(self, tmp_state):
        out = _add_buy("alice")
        oid = next(w for w in out.split() if w.startswith("lim_"))
        assert limit_order._find(limit_order._load_state(), oid, "bob") is None

    def test_missing(self, tmp_state):
        assert limit_order._find(limit_order._load_state(), "lim_xxx", "u") is None


# ── _fetch_price ───────────────────────────────────────────────────


class TestFetchPrice:
    def test_success(self, fake_defi_price):
        fake_defi_price["payload"] = {
            "isError": False,
            "details": {"price_usd": 0.5},
        }
        assert limit_order._fetch_price("CLAWNCH") == 0.5

    def test_alias_key(self, fake_defi_price):
        fake_defi_price["payload"] = {
            "isError": False,
            "details": {"price": 0.7},
        }
        assert limit_order._fetch_price("X") == 0.7

    def test_isError(self, fake_defi_price):
        fake_defi_price["payload"] = {"isError": True}
        assert limit_order._fetch_price("X") is None

    def test_raises(self, fake_defi_price):
        fake_defi_price["raises"] = RuntimeError("rate limit")
        assert limit_order._fetch_price("X") is None

    def test_bad_json(self, monkeypatch):
        import clawmes.tools.defi_price as mod

        monkeypatch.setattr(mod, "defi_price", lambda *a, **k: "not-json")
        assert limit_order._fetch_price("X") is None

    def test_non_numeric(self, fake_defi_price):
        fake_defi_price["payload"] = {
            "isError": False,
            "details": {"price_usd": "not-a-number"},
        }
        assert limit_order._fetch_price("X") is None


# ── _submit_swap ───────────────────────────────────────────────────


class TestSubmitSwap:
    def test_no_wallet(self, fake_wallet, fake_defi_swap):
        fake_wallet.connected = False
        order = {
            "type": "buy",
            "token": "X",
            "amount": 0.01,
            "slippage_bps": 100,
        }
        out = limit_order._submit_swap(order, 0.5)
        assert out["status"] == "no_wallet"

    def test_buy_success(self, fake_wallet, fake_defi_swap):
        fake_defi_swap["payload"] = {
            "isError": False,
            "details": {"tx_hash": "0xfeed"},
        }
        order = {
            "type": "buy",
            "token": "X",
            "amount": 0.01,
            "slippage_bps": 100,
        }
        out = limit_order._submit_swap(order, 0.5)
        assert out["status"] == "ok"
        assert "0xfeed" in out["tx_hash"]

    def test_sell_success(self, fake_wallet, fake_defi_swap):
        fake_defi_swap["payload"] = {
            "isError": False,
            "details": {"tx_hash": "0xfeed"},
        }
        order = {
            "type": "sell",
            "token": "X",
            "amount": 1000,
            "slippage_bps": 100,
        }
        out = limit_order._submit_swap(order, 0.5)
        assert out["status"] == "ok"

    def test_swap_raises(self, fake_wallet, fake_defi_swap):
        fake_defi_swap["raises"] = RuntimeError("rpc down")
        order = {
            "type": "buy",
            "token": "X",
            "amount": 0.01,
            "slippage_bps": 100,
        }
        out = limit_order._submit_swap(order, 0.5)
        assert out["status"] == "error"
        assert "rpc down" in out["detail"]

    def test_swap_bad_json(self, fake_wallet, monkeypatch):
        import clawmes.tools.defi_swap as mod

        monkeypatch.setattr(mod, "defi_swap", lambda *a, **k: "not-json")
        order = {
            "type": "buy",
            "token": "X",
            "amount": 0.01,
            "slippage_bps": 100,
        }
        out = limit_order._submit_swap(order, 0.5)
        assert out["status"] == "error"

    def test_swap_isError(self, fake_wallet, fake_defi_swap):
        fake_defi_swap["payload"] = {
            "isError": True,
            "content": [{"text": "no route"}],
        }
        order = {
            "type": "buy",
            "token": "X",
            "amount": 0.01,
            "slippage_bps": 100,
        }
        out = limit_order._submit_swap(order, 0.5)
        assert out["status"] == "error"
        assert "no route" in out["detail"]

    def test_swap_isError_no_content(self, fake_wallet, fake_defi_swap):
        fake_defi_swap["payload"] = {"isError": True}
        order = {
            "type": "buy",
            "token": "X",
            "amount": 0.01,
            "slippage_bps": 100,
        }
        out = limit_order._submit_swap(order, 0.5)
        assert out["status"] == "error"


# ── _evaluate_order + _run_due_sync ────────────────────────────────


class TestEvaluate:
    def test_price_unavailable(self, tmp_state, fake_defi_price):
        fake_defi_price["payload"] = {"isError": True}
        _add_buy()
        order = limit_order._load_state()["orders"][0]
        assert limit_order._evaluate_order(order) is None

    def test_below_not_crossed(self, tmp_state, fake_defi_price):
        fake_defi_price["payload"] = {
            "isError": False,
            "details": {"price_usd": 1.0},  # above threshold of 0.00001
        }
        _add_buy()
        order = limit_order._load_state()["orders"][0]
        assert limit_order._evaluate_order(order) is None

    def test_below_crossed_submits(self, tmp_state, fake_defi_price, fake_wallet, fake_defi_swap):
        fake_defi_price["payload"] = {
            "isError": False,
            "details": {"price_usd": 0.000005},  # below threshold of 0.00001
        }
        fake_defi_swap["payload"] = {
            "isError": False,
            "details": {"tx_hash": "0xfeed"},
        }
        _add_buy()
        order = limit_order._load_state()["orders"][0]
        result = limit_order._evaluate_order(order)
        assert result is not None
        assert result["status"] == "ok"

    def test_above_crossed_submits(self, tmp_state, fake_defi_price, fake_wallet, fake_defi_swap):
        fake_defi_price["payload"] = {
            "isError": False,
            "details": {"price_usd": 0.001},  # above threshold of 0.0001
        }
        fake_defi_swap["payload"] = {
            "isError": False,
            "details": {"tx_hash": "0xfeed"},
        }
        _add_sell()
        order = limit_order._load_state()["orders"][0]
        result = limit_order._evaluate_order(order)
        assert result["status"] == "ok"


class TestRunDue:
    def test_no_active(self, tmp_state):
        assert limit_order._run_due_sync() == 0

    def test_paused_skipped(self, tmp_state, fake_defi_price):
        fake_defi_price["payload"] = {
            "isError": False,
            "details": {"price_usd": 0.000005},
        }
        out = _add_buy()
        oid = next(w for w in out.split() if w.startswith("lim_"))
        limit_order._cmd_mutate("u", [oid], status="paused", verb="paused")
        assert limit_order._run_due_sync() == 0

    def test_filled_skipped(self, tmp_state, fake_defi_price):
        _add_buy()
        s = limit_order._load_state()
        s["orders"][0]["status"] = "filled"
        limit_order._save_state(s)
        assert limit_order._run_due_sync() == 0

    def test_threshold_not_crossed(self, tmp_state, fake_defi_price):
        fake_defi_price["payload"] = {
            "isError": False,
            "details": {"price_usd": 1.0},  # well above 0.00001
        }
        _add_buy()
        assert limit_order._run_due_sync() == 0

    def test_threshold_crossed_filled(
        self, tmp_state, fake_defi_price, fake_wallet, fake_defi_swap
    ):
        fake_defi_price["payload"] = {
            "isError": False,
            "details": {"price_usd": 0.000005},
        }
        fake_defi_swap["payload"] = {
            "isError": False,
            "details": {"tx_hash": "0xfeed"},
        }
        _add_buy()
        n = limit_order._run_due_sync()
        assert n == 1
        s = limit_order._load_state()
        assert s["orders"][0]["status"] == "filled"
        assert len(s["orders"][0]["attempts"]) == 1

    def test_error_below_max_stays_active(
        self, tmp_state, fake_defi_price, fake_wallet, fake_defi_swap
    ):
        """A swap error doesn't immediately fail the order — only after max_attempts."""
        fake_defi_price["payload"] = {
            "isError": False,
            "details": {"price_usd": 0.000005},
        }
        fake_defi_swap["payload"] = {
            "isError": True,
            "content": [{"text": "slippage"}],
        }
        _add_buy()
        limit_order._run_due_sync()
        s = limit_order._load_state()
        # Default max_attempts=3. After 1 attempt: still active.
        assert s["orders"][0]["status"] == "active"
        assert len(s["orders"][0]["attempts"]) == 1

    def test_error_max_attempts_fails(
        self, tmp_state, fake_defi_price, fake_wallet, fake_defi_swap
    ):
        fake_defi_price["payload"] = {
            "isError": False,
            "details": {"price_usd": 0.000005},
        }
        fake_defi_swap["payload"] = {
            "isError": True,
            "content": [{"text": "slippage"}],
        }
        _add_buy()
        for _ in range(3):
            limit_order._run_due_sync()
        s = limit_order._load_state()
        assert s["orders"][0]["status"] == "failed"

    def test_runner_swallows_evaluate_errors(self, tmp_state, monkeypatch):
        _add_buy()

        def _boom(_order):
            raise RuntimeError("eval crashed")

        monkeypatch.setattr(limit_order, "_evaluate_order", _boom)
        n, lines = limit_order._run_due_with_lines()
        assert n == 0
        assert any("error" in line for line in lines)


class TestManualTick:
    async def test_no_orders(self, tmp_state):
        assert "No limit orders ready" in await limit_order._cmd_tick()

    async def test_with_fired(self, tmp_state, fake_defi_price, fake_wallet, fake_defi_swap):
        fake_defi_price["payload"] = {
            "isError": False,
            "details": {"price_usd": 0.000005},
        }
        fake_defi_swap["payload"] = {
            "isError": False,
            "details": {"tx_hash": "0xfeed"},
        }
        _add_buy()
        out = await limit_order._cmd_tick()
        assert "Evaluated 1" in out


# ── /limit_order status / history ──────────────────────────────────


class TestStatus:
    def test_empty(self, tmp_state):
        assert "idle" in limit_order._cmd_status("u")

    def test_summarizes(self, tmp_state):
        _add_buy("alice")
        _add_sell("alice")
        s = limit_order._load_state()
        s["orders"][0]["attempts"] = [{"at": "x", "status": "ok"}]
        limit_order._save_state(s)
        out = limit_order._cmd_status("u")
        assert "2 order(s)" in out
        assert "buy=1" in out
        assert "sell=1" in out

    def test_service_health_swallowed(self, monkeypatch, tmp_state):
        import clawmes.services.limit_order_scheduler as svc_mod

        monkeypatch.setattr(
            svc_mod,
            "get_limit_order_scheduler_service",
            lambda: (_ for _ in ()).throw(RuntimeError("svc down")),
        )
        _add_buy()
        out = limit_order._cmd_status("u")
        assert "Service:" not in out

    def test_service_health_present(self, tmp_state):
        from clawmes.services import limit_order_scheduler as svc_mod

        svc_mod._reset_for_tests()
        svc = svc_mod.get_limit_order_scheduler_service()
        svc.start()
        try:
            _add_buy()
            out = limit_order._cmd_status("u")
            assert "Service:" in out
        finally:
            svc.stop()
            svc_mod._reset_for_tests()


class TestHistory:
    def test_usage(self, tmp_state):
        assert "Usage:" in limit_order._cmd_history("u", [])

    def test_not_found(self, tmp_state):
        assert "No limit order" in limit_order._cmd_history("u", ["lim_xxx"])

    def test_no_attempts(self, tmp_state):
        out = _add_buy()
        oid = next(w for w in out.split() if w.startswith("lim_"))
        assert "no attempts" in limit_order._cmd_history("u", [oid])

    def test_with_attempts(self, tmp_state):
        out = _add_buy()
        oid = next(w for w in out.split() if w.startswith("lim_"))
        s = limit_order._load_state()
        s["orders"][0]["attempts"] = [
            {
                "at": "2026-05-27T01:00:00Z",
                "status": "error",
                "detail": "slippage too high",
            }
        ]
        limit_order._save_state(s)
        out = limit_order._cmd_history("u", [oid])
        assert "Attempts for" in out
        assert "slippage too high" in out


# ── handle_limit_order ─────────────────────────────────────────────


class TestHandle:
    async def test_empty(self, tmp_state):
        out = await limit_order.handle_limit_order("")
        assert "Limit orders" in out

    async def test_unknown(self, tmp_state):
        out = await limit_order.handle_limit_order("garbage")
        assert "Unknown subcommand" in out

    async def test_add(self, tmp_state):
        out = await limit_order.handle_limit_order("add buy CLAWNCH 0.01 below 0.00001")
        assert "Limit order added" in out

    async def test_list(self, tmp_state):
        out = await limit_order.handle_limit_order("list")
        assert "No limit orders" in out

    async def test_ls(self, tmp_state):
        out = await limit_order.handle_limit_order("ls")
        assert "No limit orders" in out

    async def test_pause(self, tmp_state):
        out = await limit_order.handle_limit_order("pause lim_xxx")
        assert "No limit order" in out

    async def test_resume(self, tmp_state):
        out = await limit_order.handle_limit_order("resume lim_xxx")
        assert "No limit order" in out

    async def test_cancel(self, tmp_state):
        out = await limit_order.handle_limit_order("cancel lim_xxx")
        assert "No limit order" in out

    async def test_rm_alias(self, tmp_state):
        out = await limit_order.handle_limit_order("rm lim_xxx")
        assert "No limit order" in out

    async def test_remove_alias(self, tmp_state):
        out = await limit_order.handle_limit_order("remove lim_xxx")
        assert "No limit order" in out

    async def test_edit(self, tmp_state):
        out = await limit_order.handle_limit_order("edit lim_xxx amount 0.02")
        assert "No limit order" in out

    async def test_tick(self, tmp_state):
        out = await limit_order.handle_limit_order("tick")
        assert "No limit orders" in out

    async def test_status(self, tmp_state):
        out = await limit_order.handle_limit_order("status")
        assert "idle" in out

    async def test_history(self, tmp_state):
        out = await limit_order.handle_limit_order("history lim_xxx")
        assert "No limit order" in out

    async def test_record_swallows(self, monkeypatch, tmp_state):
        import clawmes.services.command_history as ch

        def _boom(*a, **k):
            raise RuntimeError("hist broken")

        monkeypatch.setattr(ch, "record_command_call", _boom)
        out = await limit_order.handle_limit_order("")
        assert "Limit orders" in out


# ── register ───────────────────────────────────────────────────────


class TestRegister:
    def test_register(self):
        registered: list[dict] = []

        class Ctx:
            def register_command(self, **kwargs):
                registered.append(kwargs)

        limit_order.register(Ctx())
        assert len(registered) == 1
        assert registered[0]["name"] == "limit_order"
