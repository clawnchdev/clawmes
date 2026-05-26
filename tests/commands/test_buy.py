"""Tests for the /buy slash command."""

from __future__ import annotations

import json

import pytest

from clawmes.commands import buy as buy_mod
from clawmes.lib import dexscreener
from clawmes.services.clawnch import ClawnchError
from clawmes.wallet.state import WalletState


@pytest.fixture(autouse=True)
def _isolate():
    buy_mod._draft_state.clear()
    yield
    buy_mod._draft_state.clear()


@pytest.fixture
def fake_wallet(monkeypatch):
    state: dict = {"connected": False, "address": "0x" + "a" * 40}

    def _state():
        if state["connected"]:
            return WalletState.for_chain(mode="local", address=state["address"], chain_id=8453)
        return WalletState.disconnected()

    monkeypatch.setattr(buy_mod, "get_wallet_state", _state)
    return state


@pytest.fixture
def fake_find_token(monkeypatch):
    state: dict = {"result": None}

    def _fake(q, *, chain="base"):  # noqa: ARG001
        return state["result"]

    monkeypatch.setattr(dexscreener, "find_token", _fake)
    return state


@pytest.fixture
def fake_defi_swap(monkeypatch):
    state: dict = {
        "responses": {},  # action -> JSON-encoded envelope
        "calls": [],
    }

    def _fake(args, **_kw):
        state["calls"].append(args)
        return state["responses"].get(args["action"], json.dumps({"isError": False, "details": {}}))

    import clawmes.tools.defi_swap as ds_mod

    monkeypatch.setattr(ds_mod, "defi_swap", _fake)
    return state


@pytest.fixture
def fake_clawnch_get_launch(monkeypatch):
    state: dict = {"return": {"address": "0x" + "1" * 40}, "raise": None}

    class _Svc:
        def get_launch(self, addr):  # noqa: ARG002
            if state["raise"]:
                raise state["raise"]
            return state["return"]

    import clawmes.services.clawnch as cl_mod

    monkeypatch.setattr(cl_mod, "get_clawnch_service", lambda: _Svc())
    return state


# ── usage / status / cancel ────────────────────────────────────────


class TestUsage:
    async def test_empty_shows_usage(self):
        out = await buy_mod.handle_buy("")
        assert "Buy a token with ETH" in out
        assert "--clawnch" in out
        assert "--all" in out

    async def test_usage_includes_existing_draft(self):
        buy_mod._set_draft("alice", {"token": "MNEME", "sell_eth": "0.01"})
        out = await buy_mod.handle_buy("", sender_id="alice")
        assert "Current draft" in out
        assert "MNEME" in out

    async def test_status_empty(self):
        out = await buy_mod.handle_buy("status", sender_id="alice")
        assert "No buy draft" in out

    async def test_status_with_draft(self):
        buy_mod._set_draft("alice", {"token": "X"})
        out = await buy_mod.handle_buy("status", sender_id="alice")
        assert "Buy draft" in out
        assert "X" in out

    async def test_cancel_clears(self):
        buy_mod._set_draft("alice", {"token": "X"})
        out = await buy_mod.handle_buy("cancel", sender_id="alice")
        assert "cleared" in out
        assert "alice" not in buy_mod._draft_state

    async def test_default_sender(self):
        await buy_mod.handle_buy("status")
        # Just ensure no crash with sender_id missing
        assert "default" not in buy_mod._draft_state  # no draft created


# ── flag parsing ────────────────────────────────────────────────────


class TestParseFlags:
    def test_default_is_all(self):
        positional, universe = buy_mod._parse_flags(["a", "b"])
        assert positional == ["a", "b"]
        assert universe == "all"

    def test_clawnch_flag(self):
        _, u = buy_mod._parse_flags(["a", "--clawnch", "b"])
        assert u == "clawnch"

    def test_all_flag_overrides(self):
        _, u = buy_mod._parse_flags(["--clawnch", "--all"])
        assert u == "all"

    def test_unknown_flag_ignored(self):
        pos, u = buy_mod._parse_flags(["a", "--unknown", "b"])
        assert pos == ["a", "b"]
        assert u == "all"


# ── helpers ─────────────────────────────────────────────────────────


class TestLooksLikeAddress:
    def test_valid(self):
        assert buy_mod._looks_like_address("0x" + "a" * 40)

    def test_wrong_prefix(self):
        assert not buy_mod._looks_like_address("a" * 42)

    def test_wrong_length(self):
        assert not buy_mod._looks_like_address("0x" + "a" * 39)

    def test_non_hex(self):
        assert not buy_mod._looks_like_address("0x" + "z" * 40)


class TestShort:
    def test_short_input(self):
        assert buy_mod._short("abc") == "abc"

    def test_truncates(self):
        out = buy_mod._short("0x" + "a" * 40)
        assert "…" in out


# ── quote path ─────────────────────────────────────────────────────


class TestQuote:
    async def test_too_few_args(self):
        out = await buy_mod.handle_buy("MNEME")
        assert "Usage" in out

    async def test_bad_amount(self):
        out = await buy_mod.handle_buy("MNEME notanumber")
        assert "Invalid ETH amount" in out

    async def test_zero_amount(self):
        out = await buy_mod.handle_buy("MNEME 0")
        assert "Invalid ETH amount" in out

    async def test_negative_amount(self):
        out = await buy_mod.handle_buy("MNEME -1")
        assert "Invalid ETH amount" in out

    async def test_no_wallet(self, fake_wallet):
        fake_wallet["connected"] = False
        out = await buy_mod.handle_buy("MNEME 0.01")
        assert "No wallet connected" in out

    async def test_address_input_skips_resolution(self, fake_wallet, fake_defi_swap):
        fake_wallet["connected"] = True
        addr = "0x" + "1" * 40
        fake_defi_swap["responses"]["quote"] = json.dumps(
            {"isError": False, "details": {"buy_amount": "1000000"}}
        )
        out = await buy_mod.handle_buy(f"{addr} 0.01", sender_id="alice")
        assert "Quote" in out
        assert addr in out
        assert "alice" in buy_mod._draft_state
        # defi_swap was called with the address verbatim
        assert fake_defi_swap["calls"][0]["buy_token"] == addr

    async def test_symbol_no_match(self, fake_wallet, fake_find_token):
        fake_wallet["connected"] = True
        fake_find_token["result"] = None
        out = await buy_mod.handle_buy("UNKNOWN 0.01")
        assert "No Base pair found" in out

    async def test_symbol_pair_missing_address(self, fake_wallet, fake_find_token):
        fake_wallet["connected"] = True
        fake_find_token["result"] = {"baseToken": {"symbol": "X"}}
        out = await buy_mod.handle_buy("X 0.01")
        assert "no token address" in out

    async def test_clawnch_universe_rejects_non_clawnch(
        self, fake_wallet, fake_find_token, fake_clawnch_get_launch
    ):
        fake_wallet["connected"] = True
        addr = "0x" + "1" * 40
        fake_find_token["result"] = {"baseToken": {"symbol": "MNEME", "address": addr}}
        fake_clawnch_get_launch["raise"] = ClawnchError("not_found", "no row")
        out = await buy_mod.handle_buy("MNEME 0.01 --clawnch")
        assert "not a Clawnch-launched token" in out
        assert "not_found" in out

    async def test_clawnch_universe_accepts_clawnch(
        self, fake_wallet, fake_find_token, fake_clawnch_get_launch, fake_defi_swap
    ):
        fake_wallet["connected"] = True
        addr = "0x" + "1" * 40
        fake_find_token["result"] = {"baseToken": {"symbol": "FOO", "address": addr}}
        fake_clawnch_get_launch["return"] = {"address": addr, "name": "Foo"}
        fake_defi_swap["responses"]["quote"] = json.dumps(
            {"isError": False, "details": {"buy_amount": "1000"}}
        )
        out = await buy_mod.handle_buy("FOO 0.01 --clawnch", sender_id="alice")
        assert "Quote" in out
        assert "clawnch" in out
        assert "alice" in buy_mod._draft_state

    async def test_clawnch_lookup_error_other_than_not_found(
        self, fake_wallet, fake_find_token, fake_clawnch_get_launch
    ):
        fake_wallet["connected"] = True
        fake_find_token["result"] = {"baseToken": {"symbol": "FOO", "address": "0x" + "1" * 40}}
        fake_clawnch_get_launch["raise"] = RuntimeError("upstream down")
        out = await buy_mod.handle_buy("FOO 0.01 --clawnch")
        assert "not a Clawnch-launched token" in out
        assert "upstream down" in out

    async def test_clawnch_empty_body_rejected(
        self, fake_wallet, fake_find_token, fake_clawnch_get_launch
    ):
        fake_wallet["connected"] = True
        fake_find_token["result"] = {"baseToken": {"symbol": "FOO", "address": "0x" + "1" * 40}}
        fake_clawnch_get_launch["return"] = None
        out = await buy_mod.handle_buy("FOO 0.01 --clawnch")
        assert "not a Clawnch-launched token" in out

    async def test_clawnch_body_with_error_field_rejected(
        self, fake_wallet, fake_find_token, fake_clawnch_get_launch
    ):
        fake_wallet["connected"] = True
        fake_find_token["result"] = {"baseToken": {"symbol": "FOO", "address": "0x" + "1" * 40}}
        fake_clawnch_get_launch["return"] = {"error": "no such token"}
        out = await buy_mod.handle_buy("FOO 0.01 --clawnch")
        assert "not a Clawnch-launched token" in out

    async def test_clawnch_non_dict_body_accepted(
        self, fake_wallet, fake_find_token, fake_clawnch_get_launch, fake_defi_swap
    ):
        # Some upstream branches return a list — treat as ok.
        fake_wallet["connected"] = True
        fake_find_token["result"] = {"baseToken": {"symbol": "FOO", "address": "0x" + "1" * 40}}
        fake_clawnch_get_launch["return"] = ["some", "list"]
        fake_defi_swap["responses"]["quote"] = json.dumps(
            {"isError": False, "details": {"buy_amount": "1000"}}
        )
        out = await buy_mod.handle_buy("FOO 0.01 --clawnch")
        assert "Quote" in out

    async def test_quote_error_envelope(self, fake_wallet, fake_find_token, fake_defi_swap):
        fake_wallet["connected"] = True
        fake_find_token["result"] = {"baseToken": {"symbol": "MNEME", "address": "0x" + "1" * 40}}
        fake_defi_swap["responses"]["quote"] = json.dumps(
            {"isError": True, "content": [{"text": "out of inventory"}]}
        )
        out = await buy_mod.handle_buy("MNEME 0.01")
        assert "Quote failed" in out
        assert "out of inventory" in out

    async def test_quote_unparseable_response(self, fake_wallet, fake_find_token, fake_defi_swap):
        fake_wallet["connected"] = True
        fake_find_token["result"] = {"baseToken": {"symbol": "MNEME", "address": "0x" + "1" * 40}}
        fake_defi_swap["responses"]["quote"] = "not-json"
        out = await buy_mod.handle_buy("MNEME 0.01")
        assert "Quote failed (bad response)" in out

    async def test_quote_renders_price_and_route(
        self, fake_wallet, fake_find_token, fake_defi_swap
    ):
        fake_wallet["connected"] = True
        fake_find_token["result"] = {"baseToken": {"symbol": "MNEME", "address": "0x" + "1" * 40}}
        fake_defi_swap["responses"]["quote"] = json.dumps(
            {
                "isError": False,
                "details": {
                    "buyAmount": "1000000",
                    "guaranteedPrice": "0.00001",
                    "sources": "uniswap-v3 -> aerodrome",
                },
            }
        )
        out = await buy_mod.handle_buy("MNEME 0.01")
        assert "Price: 0.00001" in out
        assert "Route: uniswap-v3 -> aerodrome" in out


# ── confirm path ───────────────────────────────────────────────────


class TestConfirm:
    async def test_no_draft(self):
        out = await buy_mod.handle_buy("confirm", sender_id="alice")
        assert "No buy draft" in out

    async def test_no_wallet(self, fake_wallet):
        fake_wallet["connected"] = False
        buy_mod._set_draft(
            "alice",
            {
                "token": "X",
                "buy_token": "0x" + "1" * 40,
                "sell_eth": "0.01",
                "expected_out": "1000",
                "universe": "all",
            },
        )
        out = await buy_mod.handle_buy("confirm", sender_id="alice")
        assert "No wallet connected" in out

    async def test_swap_error_envelope(self, fake_wallet, fake_defi_swap):
        fake_wallet["connected"] = True
        buy_mod._set_draft(
            "alice",
            {
                "token": "X",
                "buy_token": "0x" + "1" * 40,
                "sell_eth": "0.01",
                "expected_out": "1000",
                "universe": "all",
            },
        )
        fake_defi_swap["responses"]["swap"] = json.dumps(
            {"isError": True, "content": [{"text": "slippage too high"}]}
        )
        out = await buy_mod.handle_buy("confirm", sender_id="alice")
        assert "Swap failed" in out
        assert "slippage" in out

    async def test_swap_unparseable_response(self, fake_wallet, fake_defi_swap):
        fake_wallet["connected"] = True
        buy_mod._set_draft(
            "alice",
            {
                "token": "X",
                "buy_token": "0x" + "1" * 40,
                "sell_eth": "0.01",
                "expected_out": "1000",
                "universe": "all",
            },
        )
        fake_defi_swap["responses"]["swap"] = "garbage"
        out = await buy_mod.handle_buy("confirm", sender_id="alice")
        assert "Swap failed (bad response)" in out

    async def test_success_clears_draft(self, fake_wallet, fake_defi_swap):
        fake_wallet["connected"] = True
        buy_mod._set_draft(
            "alice",
            {
                "token": "MNEME",
                "buy_token": "0x" + "1" * 40,
                "sell_eth": "0.01",
                "expected_out": "1000",
                "universe": "all",
            },
        )
        fake_defi_swap["responses"]["swap"] = json.dumps(
            {"isError": False, "details": {"tx_hash": "0xabc"}}
        )
        out = await buy_mod.handle_buy("confirm", sender_id="alice")
        assert "Buy submitted" in out
        assert "0xabc" in out
        assert "basescan.org/tx/0xabc" in out
        assert "alice" not in buy_mod._draft_state

    async def test_success_no_tx_hash(self, fake_wallet, fake_defi_swap):
        # Edge: swap returns success envelope without tx_hash (shouldn't
        # happen in practice, but the renderer handles it gracefully).
        fake_wallet["connected"] = True
        buy_mod._set_draft(
            "alice",
            {
                "token": "MNEME",
                "buy_token": "0x" + "1" * 40,
                "sell_eth": "0.01",
                "expected_out": "1000",
                "universe": "all",
            },
        )
        fake_defi_swap["responses"]["swap"] = json.dumps({"isError": False, "details": {}})
        out = await buy_mod.handle_buy("confirm", sender_id="alice")
        assert "Buy submitted" in out
        # No Tx line when hash missing
        assert "Tx:" not in out


# ── command_history best-effort ────────────────────────────────────


class TestRecordingBestEffort:
    async def test_recording_failure(self, monkeypatch):
        from clawmes.services import command_history as ch_mod

        def _boom(*a, **kw):
            raise RuntimeError("history broken")

        monkeypatch.setattr(ch_mod, "record_command_call", _boom)
        out = await buy_mod.handle_buy("")
        assert isinstance(out, str)


class TestRegister:
    def test_registers(self):
        captured = []

        class FakeCtx:
            def register_command(self, **kw):
                captured.append(kw["name"])

        buy_mod.register(FakeCtx())
        assert captured == ["buy"]
