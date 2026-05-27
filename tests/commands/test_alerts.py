"""Tests for the /alerts slash command."""

from __future__ import annotations

import json
from typing import Any

import pytest

from clawmes.commands import alerts


@pytest.fixture
def tmp_state(tmp_path, monkeypatch):
    p = tmp_path / "alerts.json"
    monkeypatch.setattr(alerts, "_alerts_path", lambda: p)
    return p


@pytest.fixture
def fake_basescan(monkeypatch):
    state: dict[str, Any] = {"tokentx": None, "blocknum": None, "raises": None}

    def _fake(url, *, params=None, timeout=None):  # noqa: ARG001
        if state["raises"] is not None:
            raise state["raises"]
        action = (params or {}).get("action")
        if action == "tokentx":
            return state["tokentx"]
        if action == "eth_blockNumber":
            return state["blocknum"]
        return None

    monkeypatch.setattr(alerts, "http_get", _fake)
    return state


@pytest.fixture
def fake_defi_price(monkeypatch):
    state: dict[str, Any] = {"payload": None, "raises": None}

    def _fake(args):  # noqa: ARG001
        if state["raises"] is not None:
            raise state["raises"]
        return json.dumps(state["payload"])

    import clawmes.tools.defi_price as price_mod

    monkeypatch.setattr(price_mod, "defi_price", _fake)
    return state


def _add_price(sender="u", token="CLAWNCH", direction="above", usd="0.0001"):
    return alerts._cmd_add(sender, ["price", token, direction, usd])


def _add_wallet(sender="u", wallet=None, monkeypatch=None, height=1000):
    addr = wallet or "0x" + "a" * 40
    if monkeypatch is not None:
        monkeypatch.setattr(alerts, "_current_block_height", lambda: height)
    return alerts._cmd_add(sender, ["wallet", addr])


# ── helpers ────────────────────────────────────────────────────────


class TestHelpers:
    def test_short_unchanged(self):
        assert alerts._short("0x123") == "0x123"

    def test_short_truncated(self):
        out = alerts._short("0x" + "a" * 40)
        assert "…" in out

    def test_short_non_str(self):
        assert alerts._short(None) == "None"  # type: ignore[arg-type]

    def test_now_iso_format(self):
        s = alerts._now_iso()
        assert s.endswith("Z") and "T" in s

    def test_new_id(self):
        assert alerts._new_id().startswith("alert_")

    def test_now_epoch(self):
        assert alerts._now_epoch() > 0


# ── state I/O ───────────────────────────────────────────────────────


class TestStateIO:
    def test_load_missing(self, tmp_state):
        assert alerts._load_state() == {"alerts": []}

    def test_load_bad_json(self, tmp_state):
        tmp_state.write_text("not-json")
        assert alerts._load_state() == {"alerts": []}

    def test_load_wrong_shape(self, tmp_state):
        tmp_state.write_text(json.dumps({"alerts": "not-a-list"}))
        assert alerts._load_state() == {"alerts": []}

    def test_load_not_a_dict(self, tmp_state):
        tmp_state.write_text(json.dumps([]))
        assert alerts._load_state() == {"alerts": []}

    def test_roundtrip(self, tmp_state):
        s = {"alerts": [{"id": "x"}]}
        alerts._save_state(s)
        assert alerts._load_state() == s

    def test_default_path(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        assert alerts._alerts_path().name == "alerts.json"


# ── /alerts add ─────────────────────────────────────────────────────


class TestCmdAdd:
    def test_usage(self, tmp_state):
        out = alerts._cmd_add("u", [])
        assert "Usage:" in out

    def test_unknown_kind(self, tmp_state):
        out = alerts._cmd_add("u", ["garbage", "x"])
        assert "Unknown alert type" in out

    def test_free_tier_cap_rejection(self, tmp_state, monkeypatch):
        """When the free-tier cap helper returns an error, /alerts add bails."""
        import clawmes.services.token_gate as tg

        monkeypatch.setattr(
            tg,
            "check_cap_or_error",
            lambda *a, **k: "Free tier allows N active alert(s).",
        )
        out = alerts._cmd_add("u", ["price", "CLAWNCH", "above", "0.001"])
        assert "Free tier allows" in out


class TestAddPrice:
    def test_usage(self, tmp_state):
        out = alerts._cmd_add("u", ["price"])
        assert "Usage:" in out

    def test_bad_direction(self, tmp_state):
        out = alerts._cmd_add("u", ["price", "CLAWNCH", "sideways", "0.001"])
        assert "above" in out and "below" in out

    def test_bad_usd_non_numeric(self, tmp_state):
        out = alerts._cmd_add("u", ["price", "CLAWNCH", "above", "abc"])
        assert "must be a number" in out

    def test_bad_usd_zero(self, tmp_state):
        out = alerts._cmd_add("u", ["price", "CLAWNCH", "above", "0"])
        assert "must be positive" in out

    def test_success(self, tmp_state):
        out = _add_price()
        assert "Alert added" in out
        a = alerts._load_state()["alerts"][0]
        assert a["type"] == "price"
        assert a["direction"] == "above"
        assert a["threshold_usd"] == 0.0001


class TestAddWallet:
    def test_usage(self, tmp_state):
        out = alerts._cmd_add("u", ["wallet"])
        assert "Usage:" in out

    def test_bad_address(self, tmp_state):
        out = alerts._cmd_add("u", ["wallet", "not-an-addr"])
        assert "must be a 0x… address" in out

    def test_success(self, tmp_state, monkeypatch):
        monkeypatch.setattr(alerts, "_current_block_height", lambda: 1000)
        out = _add_wallet(monkeypatch=monkeypatch)
        assert "Alert added" in out
        a = alerts._load_state()["alerts"][0]
        assert a["type"] == "wallet"
        assert a["last_seen_block"] == 990  # 1000 - 10

    def test_block_seed_floor_at_zero(self, tmp_state, monkeypatch):
        _add_wallet(monkeypatch=monkeypatch, height=3)
        assert alerts._load_state()["alerts"][0]["last_seen_block"] == 0


# ── /alerts list / mutate / cancel ─────────────────────────────────


class TestList:
    def test_empty(self, tmp_state):
        assert "No alerts" in alerts._cmd_list("u")

    def test_lists_mine_only(self, tmp_state, monkeypatch):
        _add_price("alice")
        _add_wallet("bob", monkeypatch=monkeypatch)
        out = alerts._cmd_list("alice")
        # Only alice's alert in output.
        assert "price" in out
        assert "wallet" not in out

    def test_lists_wallet_alert(self, tmp_state, monkeypatch):
        """Render path for wallet-type alerts (covers else-branch in _cmd_list)."""
        _add_wallet("bob", monkeypatch=monkeypatch)
        out = alerts._cmd_list("bob")
        assert "wallet" in out
        assert "wallet receipt" in out


class TestMutate:
    def test_pause_usage(self, tmp_state):
        out = alerts._cmd_mutate("u", [], status="paused", verb="paused")
        assert "Usage:" in out

    def test_pause_not_found(self, tmp_state):
        out = alerts._cmd_mutate("u", ["alert_xxx"], status="paused", verb="paused")
        assert "No alert found" in out

    def test_pause_resume(self, tmp_state):
        out = _add_price()
        aid = next(w for w in out.split() if w.startswith("alert_"))
        alerts._cmd_mutate("u", [aid], status="paused", verb="paused")
        assert alerts._load_state()["alerts"][0]["status"] == "paused"
        alerts._cmd_mutate("u", [aid], status="active", verb="resumed")
        assert alerts._load_state()["alerts"][0]["status"] == "active"


class TestCancel:
    def test_usage(self, tmp_state):
        assert "Usage:" in alerts._cmd_cancel("u", [])

    def test_not_found(self, tmp_state):
        assert "No alert" in alerts._cmd_cancel("u", ["alert_xxx"])

    def test_success(self, tmp_state):
        out = _add_price()
        aid = next(w for w in out.split() if w.startswith("alert_"))
        result = alerts._cmd_cancel("u", [aid])
        assert "Cancelled" in result
        assert alerts._load_state()["alerts"] == []

    def test_isolated_by_sender(self, tmp_state):
        out = _add_price("alice")
        aid = next(w for w in out.split() if w.startswith("alert_"))
        assert "No alert" in alerts._cmd_cancel("bob", [aid])


# ── /alerts edit ───────────────────────────────────────────────────


class TestEdit:
    def test_usage(self, tmp_state):
        out = alerts._cmd_edit("u", [])
        assert "Usage:" in out

    def test_not_found(self, tmp_state):
        out = alerts._cmd_edit("u", ["alert_xxx", "direction", "above"])
        assert "No alert found" in out

    def test_wallet_alert_rejected(self, tmp_state, monkeypatch):
        out = _add_wallet(monkeypatch=monkeypatch)
        aid = next(w for w in out.split() if w.startswith("alert_"))
        result = alerts._cmd_edit("u", [aid, "direction", "above"])
        assert "Only price alerts are editable" in result

    def test_direction(self, tmp_state):
        out = _add_price()
        aid = next(w for w in out.split() if w.startswith("alert_"))
        alerts._cmd_edit("u", [aid, "direction", "below"])
        assert alerts._load_state()["alerts"][0]["direction"] == "below"

    def test_direction_bad(self, tmp_state):
        out = _add_price()
        aid = next(w for w in out.split() if w.startswith("alert_"))
        result = alerts._cmd_edit("u", [aid, "direction", "garbage"])
        assert "above" in result and "below" in result

    def test_threshold_usd(self, tmp_state):
        out = _add_price()
        aid = next(w for w in out.split() if w.startswith("alert_"))
        alerts._cmd_edit("u", [aid, "threshold_usd", "0.002"])
        assert alerts._load_state()["alerts"][0]["threshold_usd"] == 0.002

    def test_threshold_usd_bad(self, tmp_state):
        out = _add_price()
        aid = next(w for w in out.split() if w.startswith("alert_"))
        result = alerts._cmd_edit("u", [aid, "threshold_usd", "abc"])
        assert "must be a number" in result

    def test_threshold_usd_non_positive(self, tmp_state):
        out = _add_price()
        aid = next(w for w in out.split() if w.startswith("alert_"))
        result = alerts._cmd_edit("u", [aid, "threshold_usd", "0"])
        assert "must be positive" in result

    def test_token(self, tmp_state):
        out = _add_price()
        aid = next(w for w in out.split() if w.startswith("alert_"))
        alerts._cmd_edit("u", [aid, "token", "USDC"])
        assert alerts._load_state()["alerts"][0]["token"] == "USDC"

    def test_unknown_field(self, tmp_state):
        out = _add_price()
        aid = next(w for w in out.split() if w.startswith("alert_"))
        result = alerts._cmd_edit("u", [aid, "garbage", "1"])
        assert "Unknown field" in result


# ── _find ──────────────────────────────────────────────────────────


class TestFind:
    def test_found(self, tmp_state):
        out = _add_price()
        aid = next(w for w in out.split() if w.startswith("alert_"))
        s = alerts._load_state()
        assert alerts._find(s, aid, "u") is not None

    def test_wrong_sender(self, tmp_state):
        out = _add_price("alice")
        aid = next(w for w in out.split() if w.startswith("alert_"))
        s = alerts._load_state()
        assert alerts._find(s, aid, "bob") is None

    def test_missing_id(self, tmp_state):
        assert alerts._find(alerts._load_state(), "alert_xxx", "u") is None


# ── price alert evaluation ─────────────────────────────────────────


class TestCheckPriceAlert:
    def test_above_crosses(self, fake_defi_price):
        fake_defi_price["payload"] = {
            "isError": False,
            "details": {"price_usd": 0.0002},
        }
        alert = {"token": "CLAWNCH", "direction": "above", "threshold_usd": 0.0001}
        out = alerts._check_price_alert(alert)
        assert out["status"] == "fired"

    def test_below_crosses(self, fake_defi_price):
        fake_defi_price["payload"] = {
            "isError": False,
            "details": {"price_usd": 0.00005},
        }
        alert = {"token": "CLAWNCH", "direction": "below", "threshold_usd": 0.0001}
        out = alerts._check_price_alert(alert)
        assert out["status"] == "fired"

    def test_above_not_crossed(self, fake_defi_price):
        fake_defi_price["payload"] = {
            "isError": False,
            "details": {"price_usd": 0.00005},
        }
        alert = {"token": "CLAWNCH", "direction": "above", "threshold_usd": 0.0001}
        assert alerts._check_price_alert(alert) is None

    def test_below_not_crossed(self, fake_defi_price):
        fake_defi_price["payload"] = {
            "isError": False,
            "details": {"price_usd": 0.0005},
        }
        alert = {"token": "CLAWNCH", "direction": "below", "threshold_usd": 0.0001}
        assert alerts._check_price_alert(alert) is None

    def test_price_alias_key(self, fake_defi_price):
        # ``price`` instead of ``price_usd`` should still parse.
        fake_defi_price["payload"] = {"isError": False, "details": {"price": 1.0}}
        alert = {"token": "CLAWNCH", "direction": "above", "threshold_usd": 0.5}
        assert alerts._check_price_alert(alert)["status"] == "fired"

    def test_price_raises(self, fake_defi_price):
        fake_defi_price["raises"] = RuntimeError("rate limit")
        alert = {"token": "CLAWNCH", "direction": "above", "threshold_usd": 0.5}
        out = alerts._check_price_alert(alert)
        assert out["status"] == "error"
        assert "rate limit" in out["detail"]

    def test_price_bad_json(self, monkeypatch):
        import clawmes.tools.defi_price as price_mod

        monkeypatch.setattr(price_mod, "defi_price", lambda *a, **k: "not-json")
        alert = {"token": "CLAWNCH", "direction": "above", "threshold_usd": 0.5}
        out = alerts._check_price_alert(alert)
        assert out["status"] == "error"

    def test_price_isError(self, fake_defi_price):
        fake_defi_price["payload"] = {
            "isError": True,
            "content": [{"text": "unknown symbol"}],
        }
        alert = {"token": "BOGUS", "direction": "above", "threshold_usd": 0.5}
        out = alerts._check_price_alert(alert)
        assert out["status"] == "error"

    def test_price_isError_no_content(self, fake_defi_price):
        fake_defi_price["payload"] = {"isError": True}
        alert = {"token": "BOGUS", "direction": "above", "threshold_usd": 0.5}
        out = alerts._check_price_alert(alert)
        assert out["status"] == "error"

    def test_price_non_numeric(self, fake_defi_price):
        fake_defi_price["payload"] = {
            "isError": False,
            "details": {"price_usd": "not-a-number"},
        }
        alert = {"token": "CLAWNCH", "direction": "above", "threshold_usd": 0.5}
        assert alerts._check_price_alert(alert) is None


# ── wallet alert evaluation ────────────────────────────────────────


class TestCheckWalletAlert:
    def test_no_new_tx(self, fake_basescan):
        fake_basescan["tokentx"] = {"status": "0", "message": "No transactions found"}
        alert = {"wallet": "0x" + "a" * 40, "last_seen_block": 0}
        assert alerts._check_wallet_alert(alert) is None

    def test_fires_on_new_tx(self, fake_basescan):
        wallet = "0x" + "a" * 40
        fake_basescan["tokentx"] = {
            "status": "1",
            "result": [
                {"to": wallet, "contractAddress": "0x" + "T" * 40, "blockNumber": "2000"},
                {"to": wallet, "contractAddress": "0x" + "U" * 40, "blockNumber": "2001"},
            ],
        }
        alert = {"wallet": wallet, "last_seen_block": 1000}
        out = alerts._check_wallet_alert(alert)
        assert out["status"] == "fired"
        assert out["tx_count"] == 2
        assert alert["last_seen_block"] == 2001

    def test_many_tokens_truncates_sample(self, fake_basescan):
        wallet = "0x" + "a" * 40
        fake_basescan["tokentx"] = {
            "status": "1",
            "result": [
                {
                    "to": wallet,
                    "contractAddress": "0x" + f"{i:040x}",
                    "blockNumber": "2000",
                }
                for i in range(10)
            ],
        }
        alert = {"wallet": wallet, "last_seen_block": 0}
        out = alerts._check_wallet_alert(alert)
        assert "+7 more" in out["detail"]


# ── Basescan helpers ───────────────────────────────────────────────


class TestBasescanReceipts:
    def test_non_dict(self, fake_basescan):
        fake_basescan["tokentx"] = "junk"
        assert alerts._basescan_token_receipts("0xabc", start_block=0) == []

    def test_status_zero(self, fake_basescan):
        fake_basescan["tokentx"] = {"status": "0"}
        assert alerts._basescan_token_receipts("0xabc", start_block=0) == []

    def test_result_not_list(self, fake_basescan):
        fake_basescan["tokentx"] = {"status": "1", "result": "string"}
        assert alerts._basescan_token_receipts("0xabc", start_block=0) == []

    def test_filters_to(self, fake_basescan):
        wallet = "0x" + "a" * 40
        fake_basescan["tokentx"] = {
            "status": "1",
            "result": [
                {"to": wallet, "contractAddress": "0xT1"},
                {"to": "0x" + "f" * 40, "contractAddress": "0xT2"},
                "junk",
            ],
        }
        out = alerts._basescan_token_receipts(wallet, start_block=0)
        assert len(out) == 1

    def test_api_key_passed(self, monkeypatch):
        captured = {}

        def _spy(url, *, params=None, timeout=None):  # noqa: ARG001
            captured.update(params)
            return {"status": "0"}

        monkeypatch.setattr(alerts, "http_get", _spy)
        monkeypatch.setenv("BASESCAN_API_KEY", "MYKEY")
        alerts._basescan_token_receipts("0xabc", start_block=0)
        assert captured.get("apikey") == "MYKEY"


class TestCurrentBlockHeight:
    def test_http_error(self, fake_basescan):
        fake_basescan["raises"] = RuntimeError("down")
        assert alerts._current_block_height() == 0

    def test_non_dict(self, fake_basescan):
        fake_basescan["blocknum"] = "junk"
        assert alerts._current_block_height() == 0

    def test_no_result_string(self, fake_basescan):
        fake_basescan["blocknum"] = {"result": 123}
        assert alerts._current_block_height() == 0

    def test_bad_hex(self, fake_basescan):
        fake_basescan["blocknum"] = {"result": "0xZZZ"}
        assert alerts._current_block_height() == 0

    def test_success(self, fake_basescan):
        fake_basescan["blocknum"] = {"result": "0x3e8"}
        assert alerts._current_block_height() == 1000

    def test_api_key(self, monkeypatch):
        captured = {}

        def _spy(url, *, params=None, timeout=None):  # noqa: ARG001
            captured.update(params)
            return {"result": "0x0"}

        monkeypatch.setattr(alerts, "http_get", _spy)
        monkeypatch.setenv("BASESCAN_API_KEY", "MYKEY")
        alerts._current_block_height()
        assert captured.get("apikey") == "MYKEY"


# ── _check_alert dispatch ──────────────────────────────────────────


class TestCheckAlertDispatch:
    def test_unknown_type(self):
        assert alerts._check_alert({"type": "garbage"}) is None

    def test_price_dispatch(self, fake_defi_price):
        fake_defi_price["payload"] = {
            "isError": False,
            "details": {"price_usd": 1.0},
        }
        alert = {"type": "price", "token": "X", "direction": "above", "threshold_usd": 0.5}
        out = alerts._check_alert(alert)
        assert out["status"] == "fired"

    def test_wallet_dispatch(self, fake_basescan):
        fake_basescan["tokentx"] = {"status": "0"}
        alert = {"type": "wallet", "wallet": "0x" + "a" * 40, "last_seen_block": 0}
        assert alerts._check_alert(alert) is None


# ── tick / runner ──────────────────────────────────────────────────


class TestRunner:
    def test_no_active_alerts(self, tmp_state):
        assert alerts._run_due_sync() == 0

    def test_paused_skipped(self, tmp_state, fake_defi_price):
        fake_defi_price["payload"] = {
            "isError": False,
            "details": {"price_usd": 1.0},
        }
        out = _add_price()
        aid = next(w for w in out.split() if w.startswith("alert_"))
        alerts._cmd_mutate("u", [aid], status="paused", verb="paused")
        assert alerts._run_due_sync() == 0

    def test_price_fires_and_auto_deactivates(self, tmp_state, fake_defi_price):
        fake_defi_price["payload"] = {
            "isError": False,
            "details": {"price_usd": 1.0},
        }
        _add_price()
        n = alerts._run_due_sync()
        assert n == 1
        s = alerts._load_state()
        # Auto-deactivated after firing.
        assert s["alerts"][0]["status"] == "fired"
        assert len(s["alerts"][0]["fires"]) == 1

    def test_wallet_fire_does_not_deactivate(self, tmp_state, fake_basescan, monkeypatch):
        _add_wallet(monkeypatch=monkeypatch)
        wallet = alerts._load_state()["alerts"][0]["wallet"]
        fake_basescan["tokentx"] = {
            "status": "1",
            "result": [{"to": wallet, "contractAddress": "0x" + "T" * 40, "blockNumber": "2000"}],
        }
        n = alerts._run_due_sync()
        assert n == 1
        s = alerts._load_state()
        # Wallet alert stays active so it keeps firing on new txs.
        assert s["alerts"][0]["status"] == "active"
        assert s["alerts"][0]["last_seen_block"] == 2000

    def test_runner_swallows_exceptions(self, tmp_state, monkeypatch):
        _add_price()

        def _boom(_alert):
            raise RuntimeError("price service down")

        monkeypatch.setattr(alerts, "_check_alert", _boom)
        n, lines = alerts._run_due_with_lines()
        assert n == 0
        assert any("error" in line for line in lines)


class TestManualTick:
    async def test_no_fires(self, tmp_state):
        out = await alerts._cmd_tick()
        assert "No alerts fired" in out

    async def test_with_fires(self, tmp_state, fake_defi_price):
        fake_defi_price["payload"] = {
            "isError": False,
            "details": {"price_usd": 1.0},
        }
        _add_price()
        out = await alerts._cmd_tick()
        assert "Fired 1" in out


# ── /alerts status ─────────────────────────────────────────────────


class TestStatus:
    def test_empty(self, tmp_state):
        assert "idle" in alerts._cmd_status("u")

    def test_summarizes(self, tmp_state, monkeypatch):
        _add_price("alice")
        _add_wallet("bob", monkeypatch=monkeypatch)
        s = alerts._load_state()
        s["alerts"][0]["fires"] = [{"at": "x", "detail": "y"}]
        alerts._save_state(s)
        out = alerts._cmd_status("u")
        assert "2 alert(s)" in out
        assert "price=1" in out
        assert "wallet=1" in out
        assert "Total fires:  1" in out

    def test_service_health_swallowed(self, monkeypatch, tmp_state):
        import clawmes.services.alerts_scheduler as svc_mod

        monkeypatch.setattr(
            svc_mod,
            "get_alerts_scheduler_service",
            lambda: (_ for _ in ()).throw(RuntimeError("svc down")),
        )
        _add_price()
        out = alerts._cmd_status("u")
        assert "Service:" not in out

    def test_service_health_present(self, tmp_state):
        from clawmes.services import alerts_scheduler as svc_mod

        svc_mod._reset_for_tests()
        svc = svc_mod.get_alerts_scheduler_service()
        svc.start()
        try:
            _add_price()
            out = alerts._cmd_status("u")
            assert "Service:" in out
            assert "running" in out
        finally:
            svc.stop()
            svc_mod._reset_for_tests()


# ── /alerts history ────────────────────────────────────────────────


class TestHistory:
    def test_usage(self, tmp_state):
        assert "Usage:" in alerts._cmd_history("u", [])

    def test_not_found(self, tmp_state):
        assert "No alert" in alerts._cmd_history("u", ["alert_xxx"])

    def test_no_fires(self, tmp_state):
        out = _add_price()
        aid = next(w for w in out.split() if w.startswith("alert_"))
        assert "no fires" in alerts._cmd_history("u", [aid])

    def test_with_fires(self, tmp_state):
        out = _add_price()
        aid = next(w for w in out.split() if w.startswith("alert_"))
        s = alerts._load_state()
        s["alerts"][0]["fires"] = [
            {"at": "2026-05-27T01:00:00Z", "detail": "x crossed y"},
            {"at": "2026-05-27T02:00:00Z", "detail": "z"},
        ]
        alerts._save_state(s)
        result = alerts._cmd_history("u", [aid])
        assert "Fires for" in result
        assert "crossed y" in result


# ── handle_alerts (dispatch) ───────────────────────────────────────


class TestHandle:
    async def test_empty(self, tmp_state):
        out = await alerts.handle_alerts("")
        assert "Price + wallet alerts" in out

    async def test_unknown(self, tmp_state):
        out = await alerts.handle_alerts("garbage")
        assert "Unknown subcommand" in out

    async def test_add(self, tmp_state):
        out = await alerts.handle_alerts("add price CLAWNCH above 0.0001")
        assert "Alert added" in out

    async def test_list(self, tmp_state):
        out = await alerts.handle_alerts("list")
        assert "No alerts" in out

    async def test_ls_alias(self, tmp_state):
        out = await alerts.handle_alerts("ls")
        assert "No alerts" in out

    async def test_pause(self, tmp_state):
        out = await alerts.handle_alerts("pause alert_xxx")
        assert "No alert" in out

    async def test_resume(self, tmp_state):
        out = await alerts.handle_alerts("resume alert_xxx")
        assert "No alert" in out

    async def test_cancel(self, tmp_state):
        out = await alerts.handle_alerts("cancel alert_xxx")
        assert "No alert" in out

    async def test_rm_alias(self, tmp_state):
        out = await alerts.handle_alerts("rm alert_xxx")
        assert "No alert" in out

    async def test_remove_alias(self, tmp_state):
        out = await alerts.handle_alerts("remove alert_xxx")
        assert "No alert" in out

    async def test_edit(self, tmp_state):
        out = await alerts.handle_alerts("edit alert_xxx direction below")
        assert "No alert" in out

    async def test_tick(self, tmp_state):
        out = await alerts.handle_alerts("tick")
        assert "No alerts" in out

    async def test_status(self, tmp_state):
        out = await alerts.handle_alerts("status")
        assert "idle" in out

    async def test_history(self, tmp_state):
        out = await alerts.handle_alerts("history alert_xxx")
        assert "No alert" in out

    async def test_record_swallows(self, monkeypatch, tmp_state):
        import clawmes.services.command_history as ch

        def _boom(*a, **k):
            raise RuntimeError("hist broken")

        monkeypatch.setattr(ch, "record_command_call", _boom)
        out = await alerts.handle_alerts("")
        assert "Price" in out


# ── register ───────────────────────────────────────────────────────


class TestRegister:
    def test_register(self):
        registered: list[dict] = []

        class Ctx:
            def register_command(self, **kwargs):
                registered.append(kwargs)

        alerts.register(Ctx())
        assert len(registered) == 1
        assert registered[0]["name"] == "alerts"
