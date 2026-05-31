"""Tests for v0.15.0 trader-focused features:

* ``/mev-protect`` (HOLDER) — MEV protection toggle
* ``/limit_order --bracket`` (HOLDER) — TP+SL bracket orders
* ``/scan <wallet>`` (HOLDER) — wallet analysis
* ``/airdrop`` (UNLIMITED) — autonomous airdrop scanner + claimer
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest

from clawmes.commands import airdrop, limit_order, mev_protect, scan


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


# ── /mev-protect ───────────────────────────────────────────────────


@pytest.fixture
def tmp_mev_state(tmp_path, monkeypatch):
    p = tmp_path / "state.json"
    monkeypatch.setattr(mev_protect, "_state_path", lambda: p)
    return p


class TestMevStateIO:
    def test_load_missing(self, tmp_mev_state):
        assert mev_protect._load_state() == {}

    def test_load_bad_json(self, tmp_mev_state):
        tmp_mev_state.write_text("not-json")
        assert mev_protect._load_state() == {}

    def test_load_not_dict(self, tmp_mev_state):
        tmp_mev_state.write_text(json.dumps([]))
        assert mev_protect._load_state() == {}

    def test_load_coerces_values(self, tmp_mev_state):
        # Values coerced to bool; integer "1" preserved as string key by JSON.
        tmp_mev_state.write_text(json.dumps({"u": 1, "v": 0}))
        out = mev_protect._load_state()
        assert out == {"u": True, "v": False}

    def test_roundtrip(self, tmp_mev_state):
        mev_protect._save_state({"u": True})
        assert mev_protect._load_state() == {"u": True}

    def test_default_path(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        assert mev_protect._state_path().name == "state.json"


class TestMevAccessors:
    def test_is_enabled_default_false(self, tmp_mev_state):
        assert mev_protect.is_enabled("u") is False

    def test_is_enabled_after_on(self, tmp_mev_state):
        mev_protect._save_state({"u": True})
        assert mev_protect.is_enabled("u") is True

    def test_get_protected_rpc_url_ethereum(self):
        assert mev_protect.get_protected_rpc_url(1) == "https://rpc.flashbots.net/fast"

    def test_get_protected_rpc_url_base(self):
        assert mev_protect.get_protected_rpc_url(8453) is None

    def test_get_protected_rpc_url_unknown_chain(self):
        assert mev_protect.get_protected_rpc_url(99999) is None


class TestMevGate:
    async def test_gate_blocks(self, tmp_mev_state, monkeypatch):
        import clawmes.services.token_gate as tg

        monkeypatch.setattr(tg, "check_tier_or_error", lambda *a, **k: "HOLDER required")
        out = await mev_protect.handle_mev_protect("on")
        assert "HOLDER required" in out


class TestMevDispatch:
    async def test_empty_routes_to_status(self, tmp_mev_state):
        out = await mev_protect.handle_mev_protect("")
        assert "MEV protection status" in out

    async def test_status(self, tmp_mev_state):
        out = await mev_protect.handle_mev_protect("status")
        assert "disabled" in out

    async def test_on(self, tmp_mev_state):
        out = await mev_protect.handle_mev_protect("on")
        assert "MEV protection enabled" in out
        assert mev_protect.is_enabled("default") is True

    async def test_off(self, tmp_mev_state):
        # Turn on first.
        await mev_protect.handle_mev_protect("on")
        out = await mev_protect.handle_mev_protect("off")
        assert "MEV protection disabled" in out
        assert mev_protect.is_enabled("default") is False

    async def test_off_when_already_off(self, tmp_mev_state):
        """Toggling off when never toggled on shouldn't crash."""
        out = await mev_protect.handle_mev_protect("off")
        assert "disabled" in out

    async def test_unknown(self, tmp_mev_state):
        out = await mev_protect.handle_mev_protect("garbage")
        assert "Unknown subcommand" in out

    async def test_status_after_on(self, tmp_mev_state):
        await mev_protect.handle_mev_protect("on")
        out = await mev_protect.handle_mev_protect("status")
        assert "ENABLED" in out

    async def test_record_swallows(self, monkeypatch, tmp_mev_state):
        import clawmes.services.command_history as ch

        def _boom(*a, **k):
            raise RuntimeError("hist broken")

        monkeypatch.setattr(ch, "record_command_call", _boom)
        out = await mev_protect.handle_mev_protect("status")
        assert "MEV protection" in out


class TestMevRegister:
    def test_register(self):
        registered: list[dict] = []

        class Ctx:
            def register_command(self, **kwargs):
                registered.append(kwargs)

        mev_protect.register(Ctx())
        assert len(registered) == 1
        assert registered[0]["name"] == "mev-protect"


# ── /limit_order --bracket ─────────────────────────────────────────


@pytest.fixture
def tmp_limit_state(tmp_path, monkeypatch):
    p = tmp_path / "orders.json"
    monkeypatch.setattr(limit_order, "_orders_path", lambda: p)
    return p


class TestLimitOrderBracket:
    def test_bracket_gate_blocks(self, tmp_limit_state, monkeypatch):
        import clawmes.services.token_gate as tg

        monkeypatch.setattr(tg, "check_tier_or_error", lambda *a, **k: "HOLDER required")
        out = limit_order._cmd_add(
            "u",
            [
                "buy",
                "CLAWNCH",
                "0.01",
                "below",
                "0.00001",
                "--bracket",
                "20:10",
            ],
        )
        assert "HOLDER required" in out

    def test_bracket_only_on_buy(self, tmp_limit_state):
        out = limit_order._cmd_add(
            "u",
            [
                "sell",
                "CLAWNCH",
                "1000",
                "above",
                "0.001",
                "--bracket",
                "20:10",
            ],
        )
        assert "buy orders" in out

    def test_bracket_bad_shape(self, tmp_limit_state):
        out = limit_order._cmd_add(
            "u",
            [
                "buy",
                "CLAWNCH",
                "0.01",
                "below",
                "0.00001",
                "--bracket",
                "garbage",
            ],
        )
        assert "tp_pct" in out

    def test_bracket_bad_numbers(self, tmp_limit_state):
        out = limit_order._cmd_add(
            "u",
            [
                "buy",
                "CLAWNCH",
                "0.01",
                "below",
                "0.00001",
                "--bracket",
                "abc:10",
            ],
        )
        assert "must be numbers" in out

    def test_bracket_non_positive(self, tmp_limit_state):
        out = limit_order._cmd_add(
            "u",
            [
                "buy",
                "CLAWNCH",
                "0.01",
                "below",
                "0.00001",
                "--bracket",
                "0:10",
            ],
        )
        assert "must be positive" in out

    def test_bracket_success(self, tmp_limit_state):
        out = limit_order._cmd_add(
            "u",
            [
                "buy",
                "CLAWNCH",
                "0.01",
                "below",
                "0.00001",
                "--bracket",
                "20:10",
            ],
        )
        assert "Bracket:" in out
        o = limit_order._load_state()["orders"][0]
        assert o["bracket"]["tp_pct"] == 20.0
        assert o["bracket"]["sl_pct"] == 10.0


class TestMaterializeBracket:
    def test_no_bracket(self):
        children = limit_order._materialize_bracket({}, 1.0)
        assert children == []

    def test_no_fill_price(self):
        children = limit_order._materialize_bracket({"bracket": {"tp_pct": 20, "sl_pct": 10}}, None)
        assert children == []

    def test_zero_fill_price(self):
        children = limit_order._materialize_bracket({"bracket": {"tp_pct": 20, "sl_pct": 10}}, 0)
        assert children == []

    def test_bracket_with_zero_pcts(self):
        children = limit_order._materialize_bracket({"bracket": {"tp_pct": 0, "sl_pct": 10}}, 1.0)
        assert children == []

    def test_creates_tp_and_sl(self):
        parent = {
            "id": "lim_abc",
            "sender_id": "u",
            "token": "0xT",
            "amount": 0.01,
            "slippage_bps": 100,
            "bracket": {"tp_pct": 20.0, "sl_pct": 10.0},
        }
        children = limit_order._materialize_bracket(parent, 0.001)
        assert len(children) == 2
        tp = next(c for c in children if c["kind"] == "take_profit")
        sl = next(c for c in children if c["kind"] == "stop_loss")
        assert tp["direction"] == "above"
        assert tp["threshold_usd"] == pytest.approx(0.001 * 1.2)
        assert sl["direction"] == "below"
        assert sl["threshold_usd"] == pytest.approx(0.001 * 0.9)


class TestRunDueWithBracket:
    def test_fill_creates_children(self, tmp_limit_state, fake_wallet, monkeypatch):
        # Mock defi_price + defi_swap so the order fills.
        import clawmes.tools.defi_price as price_mod
        import clawmes.tools.defi_swap as swap_mod

        monkeypatch.setattr(
            price_mod,
            "defi_price",
            lambda args: json.dumps({"isError": False, "details": {"price_usd": 0.0000005}}),
        )
        monkeypatch.setattr(
            swap_mod,
            "defi_swap",
            lambda *a, **k: json.dumps({"isError": False, "details": {"tx_hash": "0xfeed"}}),
        )

        out = limit_order._cmd_add(
            "u",
            [
                "buy",
                "CLAWNCH",
                "0.01",
                "below",
                "0.00001",
                "--bracket",
                "20:10",
            ],
        )
        assert "Bracket:" in out

        n = limit_order._run_due_sync()
        assert n == 1
        state = limit_order._load_state()
        # Parent is filled, two children spawned.
        parent = next(o for o in state["orders"] if o.get("bracket"))
        assert parent["status"] == "filled"
        assert len(parent["bracket_children"]) == 2
        children = [o for o in state["orders"] if o.get("parent_order_id") == parent["id"]]
        assert len(children) == 2
        assert {c["kind"] for c in children} == {"take_profit", "stop_loss"}


# ── /scan ──────────────────────────────────────────────────────────


@pytest.fixture
def fake_scan_http(monkeypatch):
    state: dict[str, Any] = {"balance": None, "tokentx": None, "raises": None}

    def _fake(url, *, params=None, timeout=None):  # noqa: ARG001
        if state["raises"] is not None:
            raise state["raises"]
        action = (params or {}).get("action")
        if action == "balance":
            return state["balance"]
        if action == "tokentx":
            return state["tokentx"]
        return None

    monkeypatch.setattr(scan, "http_get", _fake)
    return state


class TestScanGate:
    async def test_gate_blocks(self, monkeypatch):
        import clawmes.services.token_gate as tg

        monkeypatch.setattr(tg, "check_tier_or_error", lambda *a, **k: "HOLDER required")
        out = await scan.handle_scan("0x" + "a" * 40)
        assert "HOLDER required" in out


class TestScanDispatch:
    async def test_empty_shows_usage(self):
        out = await scan.handle_scan("")
        assert "Usage:" in out

    async def test_bad_address(self):
        out = await scan.handle_scan("not-an-address")
        assert "must be a 0x" in out

    async def test_basic_scan(self, fake_scan_http):
        fake_scan_http["balance"] = {"status": "1", "result": "1000000000000000000"}
        fake_scan_http["tokentx"] = {"status": "1", "result": []}
        out = await scan.handle_scan("0x" + "a" * 40)
        assert "Wallet snapshot" in out

    async def test_json_output(self, fake_scan_http):
        fake_scan_http["balance"] = {"status": "1", "result": "0"}
        fake_scan_http["tokentx"] = {"status": "0"}
        out = await scan.handle_scan("0x" + "a" * 40 + " --json")
        data = json.loads(out)
        assert "wallet" in data

    async def test_record_swallows(self, monkeypatch, fake_scan_http):
        import clawmes.services.command_history as ch

        def _boom(*a, **k):
            raise RuntimeError("hist broken")

        monkeypatch.setattr(ch, "record_command_call", _boom)
        fake_scan_http["balance"] = {"status": "0"}
        fake_scan_http["tokentx"] = {"status": "0"}
        out = await scan.handle_scan("0x" + "a" * 40)
        assert "Wallet snapshot" in out

    async def test_record_swallows_empty(self, monkeypatch):
        import clawmes.services.command_history as ch

        def _boom(*a, **k):
            raise RuntimeError("hist broken")

        monkeypatch.setattr(ch, "record_command_call", _boom)
        out = await scan.handle_scan("")
        assert "Usage:" in out

    async def test_record_swallows_bad_address(self, monkeypatch):
        import clawmes.services.command_history as ch

        def _boom(*a, **k):
            raise RuntimeError("hist broken")

        monkeypatch.setattr(ch, "record_command_call", _boom)
        out = await scan.handle_scan("not-addr")
        assert "must be a 0x" in out


class TestScanFetchNativeBalance:
    def test_http_error(self, fake_scan_http):
        fake_scan_http["raises"] = RuntimeError("down")
        assert scan._fetch_native_balance("0xabc") == 0.0

    def test_non_dict(self, fake_scan_http):
        fake_scan_http["balance"] = "junk"
        assert scan._fetch_native_balance("0xabc") == 0.0

    def test_status_zero(self, fake_scan_http):
        fake_scan_http["balance"] = {"status": "0"}
        assert scan._fetch_native_balance("0xabc") == 0.0

    def test_bad_result(self, fake_scan_http):
        fake_scan_http["balance"] = {"status": "1", "result": "junk"}
        assert scan._fetch_native_balance("0xabc") == 0.0

    def test_success(self, fake_scan_http):
        fake_scan_http["balance"] = {"status": "1", "result": "1500000000000000000"}
        assert scan._fetch_native_balance("0xabc") == 1.5

    def test_api_key_passed(self, monkeypatch):
        captured = {}

        def _spy(url, *, params=None, timeout=None):  # noqa: ARG001
            captured.update(params)
            return {"status": "0"}

        monkeypatch.setattr(scan, "http_get", _spy)
        monkeypatch.setenv("BASESCAN_API_KEY", "MYKEY")
        scan._fetch_native_balance("0xabc")
        assert captured.get("apikey") == "MYKEY"


class TestScanFetchTokenTxs:
    def test_http_error(self, fake_scan_http):
        fake_scan_http["raises"] = RuntimeError("down")
        assert scan._fetch_recent_token_txs("0xabc") == []

    def test_non_dict(self, fake_scan_http):
        fake_scan_http["tokentx"] = "junk"
        assert scan._fetch_recent_token_txs("0xabc") == []

    def test_status_zero(self, fake_scan_http):
        fake_scan_http["tokentx"] = {"status": "0"}
        assert scan._fetch_recent_token_txs("0xabc") == []

    def test_result_not_list(self, fake_scan_http):
        fake_scan_http["tokentx"] = {"status": "1", "result": "string"}
        assert scan._fetch_recent_token_txs("0xabc") == []

    def test_success(self, fake_scan_http):
        fake_scan_http["tokentx"] = {
            "status": "1",
            "result": [{"a": 1}, "junk", {"b": 2}],
        }
        out = scan._fetch_recent_token_txs("0xabc")
        assert len(out) == 2

    def test_api_key_passed(self, monkeypatch):
        captured = {}

        def _spy(url, *, params=None, timeout=None):  # noqa: ARG001
            captured.update(params)
            return {"status": "0"}

        monkeypatch.setattr(scan, "http_get", _spy)
        monkeypatch.setenv("BASESCAN_API_KEY", "MYKEY")
        scan._fetch_recent_token_txs("0xabc")
        assert captured.get("apikey") == "MYKEY"


class TestScanAggregateTopTokens:
    def test_empty(self):
        assert scan._aggregate_top_tokens([]) == []

    def test_groups_by_token(self):
        txs = [
            {
                "contractAddress": "0x" + "T" * 40,
                "tokenSymbol": "TKN",
                "tokenName": "Token",
                "timeStamp": "100",
            },
            {
                "contractAddress": "0x" + "T" * 40,
                "tokenSymbol": "TKN",
                "timeStamp": "200",
            },
        ]
        out = scan._aggregate_top_tokens(txs)
        assert len(out) == 1
        assert out[0]["transfer_count"] == 2

    def test_filters_invalid_addresses(self):
        txs = [
            {"contractAddress": "bad", "tokenSymbol": "BAD"},
            {"contractAddress": "0x" + "T" * 40, "tokenSymbol": "OK"},
        ]
        out = scan._aggregate_top_tokens(txs)
        assert len(out) == 1

    def test_bad_timestamp_ignored(self):
        txs = [
            {
                "contractAddress": "0x" + "T" * 40,
                "tokenSymbol": "TKN",
                "timeStamp": "junk",
            }
        ]
        out = scan._aggregate_top_tokens(txs)
        assert len(out) == 1
        assert out[0]["transfer_count"] == 1

    def test_earlier_timestamp_updates_first_seen(self):
        """When a later tx has an EARLIER timestamp than first_seen, update."""
        txs = [
            {
                "contractAddress": "0x" + "T" * 40,
                "tokenSymbol": "TKN",
                "timeStamp": "200",
            },
            {
                "contractAddress": "0x" + "T" * 40,
                "tokenSymbol": "TKN",
                "timeStamp": "100",
            },
        ]
        out = scan._aggregate_top_tokens(txs)
        # first_seen should track the earlier timestamp (100), not the
        # first-encountered timestamp (200).
        assert out[0]["first_seen"] == 100


class TestScanLastActivity:
    def test_empty(self):
        assert scan._last_activity_timestamp([]) == 0

    def test_picks_max(self):
        txs = [
            {"timeStamp": "100"},
            {"timeStamp": "300"},
            {"timeStamp": "200"},
        ]
        assert scan._last_activity_timestamp(txs) == 300

    def test_skips_bad(self):
        txs = [
            {"timeStamp": "100"},
            {"timeStamp": "junk"},
        ]
        assert scan._last_activity_timestamp(txs) == 100


class TestScanComputeFlags:
    def test_no_activity(self):
        snap = {"tx_count_30d": 0, "balance_eth": 0.0, "top_tokens": []}
        flags = scan._compute_flags(snap)
        assert "no_recent_activity" in flags
        assert "empty_wallet" in flags

    def test_very_low_activity(self):
        snap = {"tx_count_30d": 2, "balance_eth": 1.0, "top_tokens": []}
        flags = scan._compute_flags(snap)
        assert "very_low_activity" in flags

    def test_single_token_concentration(self):
        snap = {
            "tx_count_30d": 10,
            "balance_eth": 1.0,
            "top_tokens": [{"transfer_count": 10}],
        }
        flags = scan._compute_flags(snap)
        assert "single_token_concentration" in flags
        assert "single_token_dominance" in flags

    def test_healthy_wallet(self):
        snap = {
            "tx_count_30d": 50,
            "balance_eth": 5.0,
            "top_tokens": [
                {"transfer_count": 10},
                {"transfer_count": 8},
                {"transfer_count": 5},
            ],
        }
        flags = scan._compute_flags(snap)
        assert flags == []


class TestScanRender:
    def test_no_tokens(self):
        snap = {
            "wallet": "0xabc",
            "balance_eth": 0.5,
            "tx_count_30d": 0,
            "last_activity": 0,
            "top_tokens": [],
            "flags": [],
        }
        out = scan._render(snap)
        assert "no token activity" in out
        assert "never" in out

    def test_with_tokens_and_flags(self):
        snap = {
            "wallet": "0xabc",
            "balance_eth": 0.5,
            "tx_count_30d": 10,
            "last_activity": 1700000000,
            "top_tokens": [{"symbol": "X", "name": "X tok", "transfer_count": 5}],
            "flags": ["very_low_activity"],
        }
        out = scan._render(snap)
        assert "X" in out
        assert "FLAGS" in out


class TestScanRegister:
    def test_register(self):
        registered: list[dict] = []

        class Ctx:
            def register_command(self, **kwargs):
                registered.append(kwargs)

        scan.register(Ctx())
        assert len(registered) == 1
        assert registered[0]["name"] == "scan"


# ── /airdrop ───────────────────────────────────────────────────────


@pytest.fixture
def tmp_airdrop_state(tmp_path, monkeypatch):
    p = tmp_path / "state.json"
    monkeypatch.setattr(airdrop, "_state_path", lambda: p)
    return p


class TestAirdropStateIO:
    def test_load_missing(self, tmp_airdrop_state):
        assert airdrop._load_state() == {"scans": [], "claims": []}

    def test_load_bad_json(self, tmp_airdrop_state):
        tmp_airdrop_state.write_text("not-json")
        assert airdrop._load_state() == {"scans": [], "claims": []}

    def test_load_not_dict(self, tmp_airdrop_state):
        tmp_airdrop_state.write_text(json.dumps([]))
        assert airdrop._load_state() == {"scans": [], "claims": []}

    def test_load_partial_shape(self, tmp_airdrop_state):
        tmp_airdrop_state.write_text(json.dumps({"scans": [{"x": 1}]}))
        s = airdrop._load_state()
        assert s["scans"] == [{"x": 1}]
        assert s["claims"] == []

    def test_load_claims_not_list(self, tmp_airdrop_state):
        tmp_airdrop_state.write_text(json.dumps({"scans": [], "claims": "not-list"}))
        s = airdrop._load_state()
        assert s["claims"] == []

    def test_load_scans_not_list(self, tmp_airdrop_state):
        tmp_airdrop_state.write_text(json.dumps({"scans": "not-list", "claims": []}))
        s = airdrop._load_state()
        assert s["scans"] == []

    def test_roundtrip(self, tmp_airdrop_state):
        airdrop._save_state({"scans": [{"a": 1}], "claims": []})
        assert airdrop._load_state() == {"scans": [{"a": 1}], "claims": []}

    def test_default_path(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        assert airdrop._state_path().name == "state.json"


class TestAirdropHelpers:
    def test_now_iso(self):
        assert airdrop._now_iso().endswith("Z")

    def test_new_id(self):
        assert airdrop._new_id().startswith("drop_")

    def test_now_epoch(self):
        assert airdrop._now_epoch() > 0


class TestAirdropGate:
    async def test_gate_blocks(self, tmp_airdrop_state, monkeypatch):
        import clawmes.services.token_gate as tg

        monkeypatch.setattr(tg, "check_tier_or_error", lambda *a, **k: "UNLIMITED required")
        out = await airdrop.handle_airdrop("list")
        assert "UNLIMITED required" in out


class TestAirdropDispatch:
    async def test_empty_shows_usage(self, tmp_airdrop_state):
        out = await airdrop.handle_airdrop("")
        assert "Autonomous airdrop scanner" in out

    async def test_list(self, tmp_airdrop_state):
        out = await airdrop.handle_airdrop("list")
        assert "Registered airdrops" in out

    async def test_unknown(self, tmp_airdrop_state):
        out = await airdrop.handle_airdrop("garbage")
        assert "Unknown subcommand" in out

    async def test_record_swallows(self, monkeypatch, tmp_airdrop_state):
        import clawmes.services.command_history as ch

        def _boom(*a, **k):
            raise RuntimeError("hist broken")

        monkeypatch.setattr(ch, "record_command_call", _boom)
        out = await airdrop.handle_airdrop("")
        assert "Autonomous" in out


class TestAirdropList:
    def test_empty_registry(self, monkeypatch, tmp_airdrop_state):
        monkeypatch.setattr(airdrop, "_REGISTRY", [])
        assert "No airdrops registered" in airdrop._cmd_list()

    def test_with_entries(self, tmp_airdrop_state):
        out = airdrop._cmd_list()
        assert "Registered airdrops" in out
        assert "demo-checker" in out


class TestCheckEligibility:
    def test_no_url(self):
        assert airdrop._check_eligibility({}, "0xabc") is None

    def test_get_method(self, monkeypatch):
        def _fake(url, *, params=None, timeout=None):  # noqa: ARG001
            return {"amount": 100}

        monkeypatch.setattr(airdrop, "http_get", _fake)
        entry = {
            "check_url": "https://x.example.com",
            "check_method": "GET",
            "eligibility_path": "amount",
        }
        assert airdrop._check_eligibility(entry, "0xabc") == 100

    def test_post_method(self, monkeypatch):
        monkeypatch.setattr(airdrop, "_post_json", lambda *a, **k: {"amount": 50})
        entry = {
            "check_url": "https://x.example.com",
            "check_method": "POST",
            "eligibility_path": "amount",
        }
        assert airdrop._check_eligibility(entry, "0xabc") == 50

    def test_http_raises(self, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("down")

        monkeypatch.setattr(airdrop, "http_get", _boom)
        entry = {"check_url": "https://x.example.com", "check_method": "GET"}
        assert airdrop._check_eligibility(entry, "0xabc") is None

    def test_non_dict_response(self, monkeypatch):
        monkeypatch.setattr(airdrop, "http_get", lambda *a, **k: "junk")
        entry = {"check_url": "https://x.example.com", "check_method": "GET"}
        assert airdrop._check_eligibility(entry, "0xabc") is None


class TestPostJson:
    def test_success(self, monkeypatch):
        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def read(self):
                return json.dumps({"ok": True}).encode("utf-8")

        def _fake_urlopen(req, timeout):
            return _Resp()

        import urllib.request as _req

        monkeypatch.setattr(_req, "urlopen", _fake_urlopen)
        out = airdrop._post_json("https://x.example.com", {"a": 1})
        assert out == {"ok": True}

    def test_failure(self, monkeypatch):
        import urllib.request as _req

        def _boom(req, timeout):
            raise RuntimeError("connect failed")

        monkeypatch.setattr(_req, "urlopen", _boom)
        out = airdrop._post_json("https://x.example.com", {"a": 1})
        assert out is None


class TestAirdropScan:
    async def test_no_wallet(self, tmp_airdrop_state, monkeypatch):
        import clawmes.services.wallet as wallet_mod

        class _Disc:
            connected = False
            address = ""

        monkeypatch.setattr(wallet_mod, "get_wallet_state", lambda: _Disc())
        out = await airdrop._cmd_scan("u")
        assert "No wallet connected" in out

    async def test_none_eligible(self, tmp_airdrop_state, fake_wallet, monkeypatch):
        monkeypatch.setattr(airdrop, "_check_eligibility", lambda *a, **k: None)
        out = await airdrop._cmd_scan("u")
        assert "none eligible" in out

    async def test_eligible_results(self, tmp_airdrop_state, fake_wallet, monkeypatch):
        monkeypatch.setattr(airdrop, "_check_eligibility", lambda *a, **k: 100)
        out = await airdrop._cmd_scan("u")
        assert "eligible" in out
        # Persists the scan.
        s = airdrop._load_state()
        assert len(s["scans"]) == 1


class TestAirdropClaim:
    async def test_usage(self, tmp_airdrop_state):
        out = await airdrop._cmd_claim("u", [])
        assert "Usage:" in out

    async def test_unknown(self, tmp_airdrop_state):
        out = await airdrop._cmd_claim("u", ["unknown"])
        assert "No airdrop registered" in out

    async def test_check_only(self, tmp_airdrop_state):
        # demo-checker has no claim contract.
        out = await airdrop._cmd_claim("u", ["demo-checker"])
        assert "check-only" in out

    async def test_no_recent_scan(self, tmp_airdrop_state, monkeypatch):
        # Mock the registry to include a claimable entry.
        monkeypatch.setattr(
            airdrop,
            "_REGISTRY",
            [
                {
                    "name": "fake",
                    "check_url": "https://x.example.com",
                    "check_method": "GET",
                    "claim_contract": "0x" + "c" * 40,
                    "claim_selector": "0xdeadbeef",
                    "eligibility_path": "amount",
                }
            ],
        )
        out = await airdrop._cmd_claim("u", ["fake"])
        assert "No recent /airdrop scan" in out

    async def test_no_wallet_after_scan(self, tmp_airdrop_state, monkeypatch):
        monkeypatch.setattr(
            airdrop,
            "_REGISTRY",
            [
                {
                    "name": "fake",
                    "check_url": "https://x.example.com",
                    "check_method": "GET",
                    "claim_contract": "0x" + "c" * 40,
                    "claim_selector": "0xdeadbeef",
                    "eligibility_path": "amount",
                }
            ],
        )
        # Seed a recent eligible scan.
        s = airdrop._load_state()
        s["scans"].append(
            {
                "id": "drop_x",
                "sender_id": "u",
                "address": "0x" + "1" * 40,
                "at": airdrop._now_iso(),
                "results": [{"name": "fake", "eligible_amount": 100, "claim_contract": "0xC"}],
            }
        )
        airdrop._save_state(s)

        import clawmes.services.wallet as wallet_mod

        class _Disc:
            connected = False
            address = ""

        monkeypatch.setattr(wallet_mod, "get_wallet_state", lambda: _Disc())
        out = await airdrop._cmd_claim("u", ["fake"])
        assert "No wallet connected" in out

    async def test_no_mode(self, tmp_airdrop_state, monkeypatch, fake_wallet):
        monkeypatch.setattr(
            airdrop,
            "_REGISTRY",
            [
                {
                    "name": "fake",
                    "check_url": "https://x.example.com",
                    "check_method": "GET",
                    "claim_contract": "0x" + "c" * 40,
                    "claim_selector": "0xdeadbeef",
                    "eligibility_path": "amount",
                }
            ],
        )
        s = airdrop._load_state()
        s["scans"].append(
            {
                "id": "drop_x",
                "sender_id": "u",
                "address": fake_wallet.address,
                "at": airdrop._now_iso(),
                "results": [{"name": "fake", "eligible_amount": 100, "claim_contract": "0xC"}],
            }
        )
        airdrop._save_state(s)

        import clawmes.services.wallet as wallet_mod

        monkeypatch.setattr(
            wallet_mod,
            "get_wallet_service",
            lambda: type("S", (), {"active_mode": None})(),
        )
        out = await airdrop._cmd_claim("u", ["fake"])
        assert "No active wallet mode" in out

    async def test_tx_failure(self, tmp_airdrop_state, monkeypatch, fake_wallet):
        monkeypatch.setattr(
            airdrop,
            "_REGISTRY",
            [
                {
                    "name": "fake",
                    "check_url": "https://x.example.com",
                    "check_method": "GET",
                    "claim_contract": "0x" + "c" * 40,
                    "claim_selector": "0xdeadbeef",
                    "eligibility_path": "amount",
                }
            ],
        )
        s = airdrop._load_state()
        s["scans"].append(
            {
                "id": "drop_x",
                "sender_id": "u",
                "address": fake_wallet.address,
                "at": airdrop._now_iso(),
                "results": [{"name": "fake", "eligible_amount": 100, "claim_contract": "0xC"}],
            }
        )
        airdrop._save_state(s)

        import clawmes.services.wallet as wallet_mod

        class _Mode:
            def send_transaction(self, **kw):
                raise RuntimeError("rpc down")

        monkeypatch.setattr(
            wallet_mod, "get_wallet_service", lambda: type("S", (), {"active_mode": _Mode()})()
        )
        out = await airdrop._cmd_claim("u", ["fake"])
        assert "Claim tx failed" in out

    async def test_tx_success(self, tmp_airdrop_state, monkeypatch, fake_wallet):
        monkeypatch.setattr(
            airdrop,
            "_REGISTRY",
            [
                {
                    "name": "fake",
                    "check_url": "https://x.example.com",
                    "check_method": "GET",
                    "claim_contract": "0x" + "c" * 40,
                    "claim_selector": "0xdeadbeef",
                    "eligibility_path": "amount",
                }
            ],
        )
        s = airdrop._load_state()
        s["scans"].append(
            {
                "id": "drop_x",
                "sender_id": "u",
                "address": fake_wallet.address,
                "at": airdrop._now_iso(),
                "results": [{"name": "fake", "eligible_amount": 100, "claim_contract": "0xC"}],
            }
        )
        airdrop._save_state(s)

        import clawmes.services.wallet as wallet_mod

        class _Mode:
            def send_transaction(self, **kw):
                return "0xclaim_tx_hash"

        monkeypatch.setattr(
            wallet_mod, "get_wallet_service", lambda: type("S", (), {"active_mode": _Mode()})()
        )
        out = await airdrop._cmd_claim("u", ["fake"])
        assert "Claim submitted" in out
        assert "0xclaim_tx_hash" in out
        s2 = airdrop._load_state()
        assert len(s2["claims"]) == 1


class TestAirdropHistory:
    def test_empty(self, tmp_airdrop_state):
        out = airdrop._cmd_history("u")
        assert "No airdrop history" in out

    def test_with_entries(self, tmp_airdrop_state):
        s = airdrop._load_state()
        s["scans"].append(
            {
                "id": "drop_x",
                "sender_id": "u",
                "at": airdrop._now_iso(),
                "results": [{"name": "demo"}],
            }
        )
        s["claims"].append(
            {
                "id": "drop_y",
                "sender_id": "u",
                "name": "demo",
                "tx_hash": "0xabcdef1234567890",
                "at": airdrop._now_iso(),
            }
        )
        airdrop._save_state(s)
        out = airdrop._cmd_history("u")
        assert "Scans" in out
        assert "Claims" in out


class TestAirdropFullDispatch:
    async def test_scan_dispatch(self, tmp_airdrop_state, fake_wallet, monkeypatch):
        monkeypatch.setattr(airdrop, "_check_eligibility", lambda *a, **k: None)
        out = await airdrop.handle_airdrop("scan")
        assert "none eligible" in out

    async def test_claim_dispatch(self, tmp_airdrop_state):
        out = await airdrop.handle_airdrop("claim demo-checker")
        assert "check-only" in out

    async def test_history_dispatch(self, tmp_airdrop_state):
        out = await airdrop.handle_airdrop("history")
        assert "No airdrop history" in out


class TestAirdropRegister:
    def test_register(self):
        registered: list[dict] = []

        class Ctx:
            def register_command(self, **kwargs):
                registered.append(kwargs)

        airdrop.register(Ctx())
        assert len(registered) == 1
        assert registered[0]["name"] == "airdrop"
