"""Tests for v0.13.0 UNLIMITED-tier additions:

* ``/sniper --auto-trail`` (trailing stop-loss)
* ``/dca --conditional`` (conditional execution)
* ``/strategy`` (preset templates)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest

from clawmes.commands import dca, sniper, strategy


@dataclass
class _FakeWalletState:
    connected: bool = True
    address: str = "0x" + "1" * 40


@pytest.fixture
def fake_wallet(monkeypatch):
    state = _FakeWalletState()
    import clawmes.services.wallet as wallet_mod

    monkeypatch.setattr(wallet_mod, "get_wallet_state", lambda: state)
    return state


# ── /sniper --auto-trail ──────────────────────────────────────────


@pytest.fixture
def tmp_sniper_state(tmp_path, monkeypatch):
    p = tmp_path / "configs.json"
    monkeypatch.setattr(sniper, "_configs_path", lambda: p)
    return p


class TestSniperAutoTrailFlag:
    def test_bad_value(self, tmp_sniper_state):
        out = sniper._cmd_add("u", ["0.005", "--auto-trail", "abc"])
        assert "must be a number" in out

    def test_zero(self, tmp_sniper_state):
        out = sniper._cmd_add("u", ["0.005", "--auto-trail", "0"])
        assert "between 0 and 100" in out

    def test_too_high(self, tmp_sniper_state):
        out = sniper._cmd_add("u", ["0.005", "--auto-trail", "100"])
        assert "between 0 and 100" in out

    def test_success(self, tmp_sniper_state):
        out = sniper._cmd_add("u", ["0.005", "--auto-trail", "20"])
        assert "Auto-trail:     20.0% trailing stop" in out
        c = sniper._load_state()["configs"][0]
        assert c["auto_trail_pct"] == 20.0


class TestSnipeCreatesTrailingWatch:
    def test_trailing_only_creates_watch(self, tmp_sniper_state, monkeypatch, fake_wallet):
        sniper._cmd_add("u", ["0.005", "--auto-trail", "20"])
        s = sniper._load_state()
        s["configs"][0]["last_seen_epoch"] = 0
        sniper._save_state(s)

        monkeypatch.setattr(
            sniper,
            "http_get",
            lambda *a, **k: {
                "status": "1",
                "launches": [
                    {
                        "contractAddress": "0x" + "T" * 40,
                        "symbol": "TKN",
                        "timestamp": sniper._now_epoch(),
                    }
                ],
            },
        )
        import clawmes.tools.defi_swap as mod

        monkeypatch.setattr(
            mod,
            "defi_swap",
            lambda *a, **k: json.dumps({"isError": False, "details": {"tx_hash": "0xfeed"}}),
        )
        monkeypatch.setattr(sniper, "_fetch_price", lambda t: 1.0)

        sniper._run_due_sync()
        c = sniper._load_state()["configs"][0]
        assert len(c["auto_sell_watches"]) == 1
        watch = c["auto_sell_watches"][0]
        assert watch["high_water_price_usd"] == 1.0


class TestTrailingStopEvaluation:
    def test_no_trail_no_auto_sell_skipped(self, tmp_sniper_state):
        config = {
            "auto_sell": None,
            "auto_trail_pct": None,
            "auto_sell_watches": [{"token": "0xT", "buy_price_usd": 1.0, "status": "active"}],
        }
        assert sniper._evaluate_auto_sell_watches(config, []) == 0

    def test_high_water_advances_no_trigger(self, tmp_sniper_state, monkeypatch, fake_wallet):
        """Price rising past high-water mark updates anchor without selling."""
        prices = iter([1.5, 2.0])
        monkeypatch.setattr(sniper, "_fetch_price", lambda t: next(prices))
        # First call: price=1.5, high_water 1.0 → updated to 1.5; trailing 20% off
        # 1.5 = 1.2 floor. Current 1.5 > 1.2 → hold.
        config = {
            "auto_sell": None,
            "auto_trail_pct": 20,
            "slippage_bps": 100,
            "auto_sell_watches": [
                {
                    "token": "0xT",
                    "buy_price_usd": 1.0,
                    "high_water_price_usd": 1.0,
                    "status": "active",
                }
            ],
        }
        n = sniper._evaluate_auto_sell_watches(config, [])
        assert n == 0
        # High water should have advanced to 1.5.
        assert config["auto_sell_watches"][0]["high_water_price_usd"] == 1.5

    def test_trailing_stop_fires(self, tmp_sniper_state, monkeypatch, fake_wallet):
        """Price drops 25% from high-water → trailing-stop fires (threshold 20%)."""
        # Watch already has high_water_price_usd=2.0 from a prior tick.
        # Current price 1.5 → drawdown = (1.5-2.0)/2.0 = -25% ≤ -20% → trigger.
        monkeypatch.setattr(sniper, "_fetch_price", lambda t: 1.5)
        monkeypatch.setattr(sniper, "_read_our_token_balance", lambda *a: 10**18)
        import clawmes.tools.defi_swap as mod

        monkeypatch.setattr(
            mod,
            "defi_swap",
            lambda *a, **k: json.dumps({"isError": False, "details": {"tx_hash": "0xfeed"}}),
        )
        config = {
            "id": "snipe_test",
            "auto_sell": None,
            "auto_trail_pct": 20,
            "slippage_bps": 100,
            "auto_sell_watches": [
                {
                    "token": "0x" + "T" * 40,
                    "symbol": "TKN",
                    "buy_price_usd": 1.0,
                    "high_water_price_usd": 2.0,
                    "status": "active",
                }
            ],
        }
        sold = sniper._evaluate_auto_sell_watches(config, [])
        assert sold == 1
        assert config["auto_sell_watches"][0]["close_reason"] == "trailing_stop"

    def test_take_profit_priority_over_trail(self, tmp_sniper_state, monkeypatch, fake_wallet):
        """When both auto_sell + auto_trail are configured, take-profit wins."""
        monkeypatch.setattr(sniper, "_fetch_price", lambda t: 2.5)  # +150%
        monkeypatch.setattr(sniper, "_read_our_token_balance", lambda *a: 10**18)
        import clawmes.tools.defi_swap as mod

        monkeypatch.setattr(
            mod,
            "defi_swap",
            lambda *a, **k: json.dumps({"isError": False, "details": {"tx_hash": "0xfeed"}}),
        )
        config = {
            "id": "x",
            "auto_sell": {"gain_pct": 100, "loss_pct": 50},
            "auto_trail_pct": 20,
            "slippage_bps": 100,
            "auto_sell_watches": [
                {
                    "token": "0xT",
                    "symbol": "TKN",
                    "buy_price_usd": 1.0,
                    "high_water_price_usd": 1.0,
                    "status": "active",
                }
            ],
        }
        sniper._evaluate_auto_sell_watches(config, [])
        assert config["auto_sell_watches"][0]["close_reason"] == "take_profit"

    def test_watch_without_high_water_field(self, tmp_sniper_state, monkeypatch, fake_wallet):
        """Backward compat: watch with no high_water_price_usd uses buy_price."""
        monkeypatch.setattr(sniper, "_fetch_price", lambda t: 0.5)
        monkeypatch.setattr(sniper, "_read_our_token_balance", lambda *a: 10**18)
        import clawmes.tools.defi_swap as mod

        monkeypatch.setattr(
            mod,
            "defi_swap",
            lambda *a, **k: json.dumps({"isError": False, "details": {"tx_hash": "0xfeed"}}),
        )
        config = {
            "id": "x",
            "auto_sell": None,
            "auto_trail_pct": 20,
            "slippage_bps": 100,
            "auto_sell_watches": [
                {
                    "token": "0xT",
                    "symbol": "TKN",
                    "buy_price_usd": 1.0,
                    "status": "active",
                }
            ],
        }
        # buy 1.0, current 0.5, high_water seeded from buy_price=1.0,
        # drawdown = (0.5-1.0)/1.0 = -50% ≤ -20% → trailing stop fires.
        sniper._evaluate_auto_sell_watches(config, [])
        assert config["auto_sell_watches"][0]["close_reason"] == "trailing_stop"


# ── /dca --conditional ─────────────────────────────────────────────


@pytest.fixture
def tmp_dca_state(tmp_path, monkeypatch):
    p = tmp_path / "schedules.json"
    monkeypatch.setattr(dca, "_schedules_path", lambda: p)
    return p


@pytest.fixture
def fake_defi_price(monkeypatch):
    state: dict[str, Any] = {"payload": None, "raises": None}

    def _fake(args):  # noqa: ARG001
        if state["raises"]:
            raise state["raises"]
        return json.dumps(state["payload"])

    import clawmes.tools.defi_price as mod

    monkeypatch.setattr(mod, "defi_price", _fake)
    return state


class TestParseConditional:
    def test_wrong_shape(self):
        cond, err = dca._parse_conditional("garbage")
        assert cond is None
        assert "expected" in err

    def test_unknown_op(self):
        cond, err = dca._parse_conditional("price_sideways:CLAWNCH:0.001")
        assert cond is None
        assert "unknown operator" in err

    def test_bad_usd(self):
        cond, err = dca._parse_conditional("price_above:CLAWNCH:abc")
        assert cond is None
        assert "must be a number" in err

    def test_non_positive_usd(self):
        cond, err = dca._parse_conditional("price_above:CLAWNCH:0")
        assert cond is None
        assert "must be positive" in err

    def test_success(self):
        cond, err = dca._parse_conditional("price_below:CLAWNCH:0.0001")
        assert err is None
        assert cond == {"op": "price_below", "token": "CLAWNCH", "threshold_usd": 0.0001}


class TestDescribeConditional:
    def test_above(self):
        out = dca._describe_conditional({"op": "price_above", "token": "X", "threshold_usd": 1.0})
        assert "above $1.0" in out

    def test_below(self):
        out = dca._describe_conditional({"op": "price_below", "token": "Y", "threshold_usd": 0.5})
        assert "below $0.5" in out


class TestConditionalSatisfied:
    def test_above_true(self, fake_defi_price):
        fake_defi_price["payload"] = {
            "isError": False,
            "details": {"price_usd": 2.0},
        }
        cond = {"op": "price_above", "token": "X", "threshold_usd": 1.0}
        assert dca._conditional_satisfied(cond) is True

    def test_above_false(self, fake_defi_price):
        fake_defi_price["payload"] = {
            "isError": False,
            "details": {"price_usd": 0.5},
        }
        cond = {"op": "price_above", "token": "X", "threshold_usd": 1.0}
        assert dca._conditional_satisfied(cond) is False

    def test_below_true(self, fake_defi_price):
        fake_defi_price["payload"] = {
            "isError": False,
            "details": {"price_usd": 0.5},
        }
        cond = {"op": "price_below", "token": "X", "threshold_usd": 1.0}
        assert dca._conditional_satisfied(cond) is True

    def test_below_false(self, fake_defi_price):
        fake_defi_price["payload"] = {
            "isError": False,
            "details": {"price_usd": 2.0},
        }
        cond = {"op": "price_below", "token": "X", "threshold_usd": 1.0}
        assert dca._conditional_satisfied(cond) is False

    def test_unknown_op(self, fake_defi_price):
        fake_defi_price["payload"] = {
            "isError": False,
            "details": {"price_usd": 2.0},
        }
        cond = {"op": "garbage", "token": "X", "threshold_usd": 1.0}
        assert dca._conditional_satisfied(cond) is False

    def test_isError(self, fake_defi_price):
        fake_defi_price["payload"] = {"isError": True}
        cond = {"op": "price_above", "token": "X", "threshold_usd": 1.0}
        assert dca._conditional_satisfied(cond) is False

    def test_price_raises(self, fake_defi_price):
        fake_defi_price["raises"] = RuntimeError("rate limit")
        cond = {"op": "price_above", "token": "X", "threshold_usd": 1.0}
        assert dca._conditional_satisfied(cond) is False

    def test_bad_json(self, monkeypatch):
        import clawmes.tools.defi_price as mod

        monkeypatch.setattr(mod, "defi_price", lambda args: "not-json")
        cond = {"op": "price_above", "token": "X", "threshold_usd": 1.0}
        assert dca._conditional_satisfied(cond) is False

    def test_non_numeric_price(self, fake_defi_price):
        fake_defi_price["payload"] = {
            "isError": False,
            "details": {"price_usd": "junk"},
        }
        cond = {"op": "price_above", "token": "X", "threshold_usd": 1.0}
        assert dca._conditional_satisfied(cond) is False


class TestDcaAddConditional:
    def test_requires_unlimited(self, tmp_dca_state, monkeypatch):
        import clawmes.services.token_gate as tg

        monkeypatch.setattr(tg, "check_tier_or_error", lambda *a, **k: "UNLIMITED required")
        out = dca._cmd_add(
            "u",
            [
                "0x" + "a" * 40,
                "0.01",
                "1h",
                "--conditional",
                "price_above:CLAWNCH:0.0001",
            ],
        )
        assert "UNLIMITED required" in out

    def test_parse_error(self, tmp_dca_state):
        out = dca._cmd_add(
            "u",
            ["0x" + "a" * 40, "0.01", "1h", "--conditional", "garbage"],
        )
        assert "--conditional parse error" in out

    def test_success(self, tmp_dca_state):
        out = dca._cmd_add(
            "u",
            [
                "0x" + "a" * 40,
                "0.01",
                "1h",
                "--conditional",
                "price_below:CLAWNCH:0.00001",
            ],
        )
        assert "Conditional:" in out
        s = dca._load_state()["schedules"][0]
        assert s["conditional"]["op"] == "price_below"


class TestConditionalBlocksRun:
    def test_blocked_when_false(self, tmp_dca_state, fake_defi_price, fake_wallet):
        # Add schedule with conditional that will fail (price below 0.00001
        # but actual price is 2.0).
        dca._cmd_add(
            "u",
            [
                "0x" + "a" * 40,
                "0.01",
                "1h",
                "--conditional",
                "price_below:CLAWNCH:0.00001",
            ],
        )
        s = dca._load_state()
        s["schedules"][0]["next_run_epoch"] = 0
        dca._save_state(s)

        # Conditional check uses defi_price; current price 2.0 > 0.00001
        # so price_below conditional is False → blocked.
        fake_defi_price["payload"] = {
            "isError": False,
            "details": {"price_usd": 2.0},
        }
        n = dca._run_due_sync()
        assert n == 1  # Conditional counts as a "fired schedule" even if blocked
        s2 = dca._load_state()
        ex = s2["schedules"][0]["executions"][0]
        assert ex["result"]["status"] == "conditional_blocked"

    def test_allowed_when_true(self, tmp_dca_state, fake_defi_price, fake_wallet, monkeypatch):
        dca._cmd_add(
            "u",
            [
                "0x" + "a" * 40,
                "0.01",
                "1h",
                "--conditional",
                "price_above:CLAWNCH:0.00001",
            ],
        )
        s = dca._load_state()
        s["schedules"][0]["next_run_epoch"] = 0
        dca._save_state(s)

        # Conditional is True (price 2.0 > 0.00001) → execute.
        fake_defi_price["payload"] = {
            "isError": False,
            "details": {"price_usd": 2.0},
        }

        import clawmes.tools.defi_swap as swap_mod

        monkeypatch.setattr(
            swap_mod,
            "defi_swap",
            lambda *a, **k: json.dumps({"isError": False, "details": {"tx_hash": "0xfeed"}}),
        )
        n = dca._run_due_sync()
        assert n == 1
        s2 = dca._load_state()
        ex = s2["schedules"][0]["executions"][0]
        assert ex["result"]["status"] == "ok"


# ── /strategy ──────────────────────────────────────────────────────


@pytest.fixture
def tmp_strategy_history(tmp_path, monkeypatch):
    p = tmp_path / "history.json"
    monkeypatch.setattr(strategy, "_history_path", lambda: p)
    return p


class TestStrategyHelpers:
    def test_load_missing(self, tmp_strategy_history):
        assert strategy._load_history() == {"applied": []}

    def test_load_bad_json(self, tmp_strategy_history):
        tmp_strategy_history.write_text("not-json")
        assert strategy._load_history() == {"applied": []}

    def test_load_wrong_shape(self, tmp_strategy_history):
        tmp_strategy_history.write_text(json.dumps({"applied": "not-list"}))
        assert strategy._load_history() == {"applied": []}

    def test_load_not_dict(self, tmp_strategy_history):
        tmp_strategy_history.write_text(json.dumps([]))
        assert strategy._load_history() == {"applied": []}

    def test_roundtrip(self, tmp_strategy_history):
        s = {"applied": [{"id": "x"}]}
        strategy._save_history(s)
        assert strategy._load_history() == s

    def test_now_iso(self):
        assert strategy._now_iso().endswith("Z")

    def test_new_id(self):
        assert strategy._new_id().startswith("strat_")

    def test_now_epoch(self):
        assert strategy._now_epoch() > 0

    def test_default_path(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        assert strategy._history_path().name == "history.json"


class TestPresetParsers:
    def test_whale_shadow_usage(self):
        steps, err = strategy._preset_whale_shadow([])
        assert "usage:" in err

    def test_whale_shadow_bad_wallet(self):
        steps, err = strategy._preset_whale_shadow(["not-an-address", "0.001"])
        assert "must be 0x" in err

    def test_whale_shadow_bad_eth(self):
        steps, err = strategy._preset_whale_shadow(["0x" + "a" * 40, "abc"])
        assert "must be a number" in err

    def test_whale_shadow_success(self):
        steps, err = strategy._preset_whale_shadow(["0x" + "a" * 40, "0.001"])
        assert err is None
        assert len(steps) == 2
        assert steps[0]["command"] == "copy"
        assert "--invert" in steps[0]["args"]
        assert steps[1]["command"] == "alerts"

    def test_dca_and_snipe_usage(self):
        steps, err = strategy._preset_dca_and_snipe([])
        assert "usage:" in err

    def test_dca_and_snipe_bad_token(self):
        steps, err = strategy._preset_dca_and_snipe(["bad", "0.01", "1h", "0.005"])
        assert "must be 0x" in err

    def test_dca_and_snipe_bad_amounts(self):
        steps, err = strategy._preset_dca_and_snipe(["0x" + "a" * 40, "abc", "1h", "0.005"])
        assert "must be numbers" in err

    def test_dca_and_snipe_success(self):
        steps, err = strategy._preset_dca_and_snipe(["0x" + "a" * 40, "0.01", "1h", "0.005"])
        assert err is None
        assert len(steps) == 2
        assert steps[0]["command"] == "dca"
        assert steps[1]["command"] == "sniper"

    def test_laddered_tp_usage(self):
        steps, err = strategy._preset_laddered_tp([])
        assert "usage:" in err

    def test_laddered_tp_bad_wallet(self):
        steps, err = strategy._preset_laddered_tp(["0.001", "not-addr", "50:100:200"])
        assert "must be 0x" in err

    def test_laddered_tp_bad_eth(self):
        steps, err = strategy._preset_laddered_tp(["abc", "0x" + "a" * 40, "50:100:200"])
        assert "must be a number" in err

    def test_laddered_tp_bad_ladder_shape(self):
        steps, err = strategy._preset_laddered_tp(["0.001", "0x" + "a" * 40, "50:100"])
        assert "tp1:tp2:tp3" in err

    def test_laddered_tp_bad_ladder_values(self):
        steps, err = strategy._preset_laddered_tp(["0.001", "0x" + "a" * 40, "abc:100:200"])
        assert "must be numbers" in err

    def test_laddered_tp_non_positive_ladder(self):
        steps, err = strategy._preset_laddered_tp(["0.001", "0x" + "a" * 40, "0:100:200"])
        assert "must be positive" in err

    def test_laddered_tp_success(self):
        steps, err = strategy._preset_laddered_tp(["0.001", "0x" + "a" * 40, "50:100:200"])
        assert err is None
        assert len(steps) == 2
        # Primary TP uses the smallest value (50%).
        assert "50.0:50" in steps[1]["args"]


# ── dispatch ──────────────────────────────────────────────────────


class TestStrategyDispatch:
    async def test_empty(self, tmp_strategy_history):
        out = await strategy.handle_strategy("")
        assert "Strategy" in out

    async def test_unknown(self, tmp_strategy_history):
        out = await strategy.handle_strategy("garbage")
        assert "Unknown subcommand" in out

    async def test_list(self, tmp_strategy_history):
        out = await strategy.handle_strategy("list")
        assert "whale-shadow" in out
        assert "dca-and-snipe" in out
        assert "laddered-tp" in out

    async def test_preview_usage(self, tmp_strategy_history):
        out = await strategy.handle_strategy("preview")
        assert "Usage:" in out

    async def test_preview_unknown_preset(self, tmp_strategy_history):
        out = await strategy.handle_strategy("preview unknown")
        assert "Unknown preset" in out

    async def test_preview_parse_error(self, tmp_strategy_history):
        out = await strategy.handle_strategy("preview whale-shadow")
        assert "usage:" in out

    async def test_preview_success(self, tmp_strategy_history):
        out = await strategy.handle_strategy("preview whale-shadow 0x" + "a" * 40 + " 0.001")
        assert "Preview" in out
        assert "/copy add" in out
        assert "--invert" in out

    async def test_apply_usage(self, tmp_strategy_history):
        out = await strategy.handle_strategy("apply")
        assert "Usage:" in out

    async def test_apply_gate(self, tmp_strategy_history, monkeypatch):
        import clawmes.services.token_gate as tg

        monkeypatch.setattr(tg, "check_tier_or_error", lambda *a, **k: "UNLIMITED required")
        out = await strategy.handle_strategy("apply whale-shadow 0x" + "a" * 40 + " 0.001")
        assert "UNLIMITED required" in out

    async def test_apply_unknown_preset(self, tmp_strategy_history):
        out = await strategy.handle_strategy("apply unknown")
        assert "Unknown preset" in out

    async def test_apply_parse_error(self, tmp_strategy_history):
        out = await strategy.handle_strategy("apply whale-shadow")
        assert "usage:" in out

    async def test_apply_success(self, tmp_strategy_history, monkeypatch):
        # Stub each downstream command to return a known string.
        async def _fake_copy(args, sender_id="default", **kw):
            return f"copy result for {args}"

        async def _fake_alerts(args, sender_id="default", **kw):
            return f"alerts result for {args}"

        import clawmes.commands.alerts as alerts_mod
        import clawmes.commands.copy as copy_mod

        monkeypatch.setattr(copy_mod, "handle_copy", _fake_copy)
        monkeypatch.setattr(alerts_mod, "handle_alerts", _fake_alerts)

        out = await strategy.handle_strategy("apply whale-shadow 0x" + "a" * 40 + " 0.001")
        assert "Applying strategy" in out
        assert "copy result for" in out
        assert "alerts result for" in out
        # History recorded.
        h = strategy._load_history()
        assert len(h["applied"]) == 1
        assert h["applied"][0]["preset"] == "whale-shadow"

    async def test_history_empty(self, tmp_strategy_history):
        out = await strategy.handle_strategy("history")
        assert "No strategy history" in out

    async def test_history_with_entries(self, tmp_strategy_history):
        s = strategy._load_history()
        s["applied"].append(
            {
                "id": "strat_x",
                "sender_id": "default",
                "preset": "whale-shadow",
                "args": ["0x" + "a" * 40, "0.001"],
                "at": "2026-05-28T01:00:00Z",
                "results": [],
            }
        )
        strategy._save_history(s)
        out = await strategy.handle_strategy("history")
        assert "Strategy history" in out
        assert "whale-shadow" in out

    async def test_record_swallows(self, monkeypatch, tmp_strategy_history):
        import clawmes.services.command_history as ch

        def _boom(*a, **k):
            raise RuntimeError("hist broken")

        monkeypatch.setattr(ch, "record_command_call", _boom)
        out = await strategy.handle_strategy("")
        assert "Strategy" in out


class TestDispatchStepCommands:
    async def test_dispatch_copy(self, monkeypatch):
        async def _fake(args, sender_id="default", **kw):
            return f"copy {args}"

        import clawmes.commands.copy as mod

        monkeypatch.setattr(mod, "handle_copy", _fake)
        out = await strategy._dispatch_step({"command": "copy", "args": "add 0xa 0.001"}, "u")
        assert "copy add" in out

    async def test_dispatch_dca(self, monkeypatch):
        async def _fake(args, sender_id="default", **kw):
            return f"dca {args}"

        import clawmes.commands.dca as mod

        monkeypatch.setattr(mod, "handle_dca", _fake)
        out = await strategy._dispatch_step({"command": "dca", "args": "add 0xa 0.01 1h"}, "u")
        assert "dca add" in out

    async def test_dispatch_sniper(self, monkeypatch):
        async def _fake(args, sender_id="default", **kw):
            return f"sniper {args}"

        import clawmes.commands.sniper as mod

        monkeypatch.setattr(mod, "handle_sniper", _fake)
        out = await strategy._dispatch_step({"command": "sniper", "args": "add 0.005"}, "u")
        assert "sniper add" in out

    async def test_dispatch_alerts(self, monkeypatch):
        async def _fake(args, sender_id="default", **kw):
            return f"alerts {args}"

        import clawmes.commands.alerts as mod

        monkeypatch.setattr(mod, "handle_alerts", _fake)
        out = await strategy._dispatch_step({"command": "alerts", "args": "add wallet 0xa"}, "u")
        assert "alerts add" in out


class TestRegister:
    def test_register(self):
        registered: list[dict] = []

        class Ctx:
            def register_command(self, **kwargs):
                registered.append(kwargs)

        strategy.register(Ctx())
        assert len(registered) == 1
        assert registered[0]["name"] == "strategy"
