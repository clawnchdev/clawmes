"""Tests for /balance and /portfolio slash commands."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from clawmes.commands import balance as balance_cmd
from clawmes.wallet.state import WalletState


@pytest.fixture
def disconnected_state():
    return WalletState.disconnected()


@pytest.fixture
def connected_state():
    return WalletState.for_chain(
        mode="local",
        address="0x" + "a" * 40,
        chain_id=8453,
    )


# --- /balance -----------------------------------------------------------


class TestHandleBalance:
    async def test_no_wallet(self, disconnected_state):
        with patch(
            "clawmes.commands.balance.get_wallet_state",
            return_value=disconnected_state,
        ):
            out = await balance_cmd.handle_balance("")
        assert "No wallet connected" in out

    async def test_no_chain_when_disconnected_chain(self):
        # Wallet "connected" but with no chain id — defensive edge case.
        state = WalletState(
            connected=True,
            mode="local",
            address="0x" + "a" * 40,
            chain_id=None,
        )
        with patch(
            "clawmes.commands.balance.get_wallet_state",
            return_value=state,
        ):
            out = await balance_cmd.handle_balance("")
        assert "No chain specified" in out

    async def test_uses_wallet_chain_when_no_arg(self, monkeypatch, connected_state):
        captured = {}

        def fake_defi_balance(args, **kw):
            captured["args"] = args
            return json.dumps(
                {
                    "content": [{"type": "text", "text": "0.5 ETH on Base"}],
                    "details": {
                        "address": args["address"],
                        "chain": "base",
                        "chain_id": 8453,
                        "native_balance_wei": "500000000000000000",
                        "native_balance": "0.5 ETH",
                    },
                }
            )

        monkeypatch.setattr("clawmes.tools.defi_balance.defi_balance", fake_defi_balance)
        with patch(
            "clawmes.commands.balance.get_wallet_state",
            return_value=connected_state,
        ):
            out = await balance_cmd.handle_balance("")
        assert "Native balance" in out
        assert "0.5 ETH" in out
        assert captured["args"]["chain"] == "8453"

    async def test_uses_explicit_chain_arg(self, monkeypatch, connected_state):
        captured = {}

        def fake_defi_balance(args, **kw):
            captured["args"] = args
            return json.dumps(
                {
                    "content": [{"type": "text", "text": "0.0 ETH"}],
                    "details": {
                        "address": args["address"],
                        "chain": "ethereum",
                        "native_balance": "0.0 ETH",
                    },
                }
            )

        monkeypatch.setattr("clawmes.tools.defi_balance.defi_balance", fake_defi_balance)
        with patch(
            "clawmes.commands.balance.get_wallet_state",
            return_value=connected_state,
        ):
            out = await balance_cmd.handle_balance("ethereum")
        assert captured["args"]["chain"] == "ethereum"
        assert "Native balance" in out

    async def test_propagates_tool_error(self, monkeypatch, connected_state):
        def fake_defi_balance(args, **kw):
            return json.dumps(
                {
                    "content": [{"type": "text", "text": "RPC error: timeout"}],
                    "isError": True,
                    "details": {"error_code": "rpc_error"},
                }
            )

        monkeypatch.setattr("clawmes.tools.defi_balance.defi_balance", fake_defi_balance)
        with patch(
            "clawmes.commands.balance.get_wallet_state",
            return_value=connected_state,
        ):
            out = await balance_cmd.handle_balance("")
        assert "RPC error" in out

    async def test_falls_back_when_native_balance_missing(self, monkeypatch, connected_state):
        # If the tool envelope changes shape and 'native_balance' is
        # missing, the command should fall back to the wei value.
        def fake_defi_balance(args, **kw):
            return json.dumps(
                {
                    "content": [{"type": "text", "text": "?"}],
                    "details": {
                        "chain": "base",
                        "native_balance_wei": "1000000000000000000",
                    },
                }
            )

        monkeypatch.setattr("clawmes.tools.defi_balance.defi_balance", fake_defi_balance)
        with patch(
            "clawmes.commands.balance.get_wallet_state",
            return_value=connected_state,
        ):
            out = await balance_cmd.handle_balance("")
        assert "1000000000000000000" in out


# --- /portfolio --------------------------------------------------------


class TestHandlePortfolio:
    async def test_no_wallet(self, disconnected_state):
        with patch(
            "clawmes.commands.balance.get_wallet_state",
            return_value=disconnected_state,
        ):
            out = await balance_cmd.handle_portfolio("")
        assert "No wallet connected" in out

    async def test_no_chain_when_disconnected_chain(self):
        state = WalletState(
            connected=True,
            mode="local",
            address="0x" + "a" * 40,
            chain_id=None,
        )
        with patch(
            "clawmes.commands.balance.get_wallet_state",
            return_value=state,
        ):
            out = await balance_cmd.handle_portfolio("")
        assert "No chain specified" in out

    async def test_uses_tool_content_directly(self, monkeypatch, connected_state):
        def fake_defi_balance(args, **kw):
            return json.dumps(
                {
                    "content": [
                        {
                            "type": "text",
                            "text": ("Balances for 0x… on Base:\n  ETH      0.5\n  USDC     100.0"),
                        }
                    ],
                    "details": {
                        "address": args["address"],
                        "balances": [
                            {"symbol": "ETH", "human": "0.5"},
                            {"symbol": "USDC", "human": "100.0"},
                        ],
                    },
                }
            )

        monkeypatch.setattr("clawmes.tools.defi_balance.defi_balance", fake_defi_balance)
        with patch(
            "clawmes.commands.balance.get_wallet_state",
            return_value=connected_state,
        ):
            out = await balance_cmd.handle_portfolio("")
        assert "Balances for 0x…" in out
        assert "ETH" in out
        assert "USDC" in out

    async def test_propagates_tool_error(self, monkeypatch, connected_state):
        def fake_defi_balance(args, **kw):
            return json.dumps(
                {
                    "content": [{"type": "text", "text": "no RPC endpoint"}],
                    "isError": True,
                    "details": {"error_code": "rpc_unconfigured"},
                }
            )

        monkeypatch.setattr("clawmes.tools.defi_balance.defi_balance", fake_defi_balance)
        with patch(
            "clawmes.commands.balance.get_wallet_state",
            return_value=connected_state,
        ):
            out = await balance_cmd.handle_portfolio("")
        assert "no RPC endpoint" in out

    async def test_falls_back_when_content_missing(self, monkeypatch, connected_state):
        # Defensive — defi_balance shouldn't return a payload without
        # content, but if it ever does, we surface the details dict
        # rather than crashing.
        def fake_defi_balance(args, **kw):
            return json.dumps(
                {
                    "content": [],
                    "details": {"address": args["address"], "balances": []},
                }
            )

        monkeypatch.setattr("clawmes.tools.defi_balance.defi_balance", fake_defi_balance)
        with patch(
            "clawmes.commands.balance.get_wallet_state",
            return_value=connected_state,
        ):
            out = await balance_cmd.handle_portfolio("")
        assert "Portfolio for" in out


# --- registration ------------------------------------------------------


class TestRegister:
    def test_registers_two_commands(self):
        captured = []

        class FakeCtx:
            def register_command(self, **kw):
                captured.append(kw["name"])

        balance_cmd.register(FakeCtx())
        assert set(captured) == {"balance", "portfolio"}
