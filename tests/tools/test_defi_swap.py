"""Tests for the ``defi_swap`` tool."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from clawmes.tools.defi_swap import defi_swap
from clawmes.wallet.state import WalletState

OWNER = "0x" + "a" * 40
USDC = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"  # USDC on Base
WETH = "0x4200000000000000000000000000000000000006"


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    from clawmes.policy import storage as policy_storage
    from clawmes.services import token_decimals as td_mod
    from clawmes.services import wallet as wallet_mod
    from clawmes.services import zerox as zerox_mod

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(wallet_mod, "_instance", None)
    monkeypatch.setattr(td_mod, "_instance", None)
    monkeypatch.setattr(zerox_mod, "_instance", None)
    policy_storage.save_policies([])


@pytest.fixture
def connected(monkeypatch):
    state = WalletState.for_chain(mode="local", address=OWNER, chain_id=8453)
    monkeypatch.setattr("clawmes.tools.defi_swap.get_wallet_state", lambda: state)
    return state


@pytest.fixture
def fake_zerox(monkeypatch):
    from clawmes.services import zerox as zerox_mod

    svc = MagicMock()
    svc.get_price.return_value = {
        "sellAmount": "1000000",
        "buyAmount": "950",
        "minBuyAmount": "940",
        "gas": "180000",
        "route": {"fills": [{"source": "Uniswap_V3", "proportionBps": 10000}]},
    }
    svc.get_quote.return_value = {
        "sellAmount": "1000000",
        "buyAmount": "950",
        "minBuyAmount": "940",
        "transaction": {
            "to": "0x" + "1" * 40,
            "data": "0xdeadbeef",
            "value": "0x0",
            "gas": "0x30000",
        },
    }
    monkeypatch.setattr(zerox_mod, "_instance", svc)
    return svc


@pytest.fixture
def fake_decimals(monkeypatch):
    from clawmes.services import token_decimals as td_mod

    svc = MagicMock()

    # USDC = 6 decimals, everything else = 18
    def fake_strict(token, chain_id):
        return 6 if token.lower() == USDC.lower() else 18

    svc.get_strict.side_effect = fake_strict
    monkeypatch.setattr(td_mod, "_instance", svc)
    return svc


@pytest.fixture
def fake_mode(monkeypatch):
    from clawmes.services import wallet as wallet_mod

    mode = MagicMock()
    mode.send_transaction.return_value = "0x" + "f" * 64
    svc = MagicMock()
    svc.active_mode = mode
    monkeypatch.setattr(wallet_mod, "get_wallet_service", lambda: svc)
    return mode


class TestNoWallet:
    def test_no_wallet(self, monkeypatch):
        monkeypatch.setattr(
            "clawmes.tools.defi_swap.get_wallet_state",
            lambda: WalletState.disconnected(),
        )
        out = json.loads(
            defi_swap(
                {
                    "action": "quote",
                    "sell_token": "ETH",
                    "buy_token": USDC,
                    "sell_amount": "0.1",
                }
            )
        )
        assert out["isError"] is True
        assert out["details"]["error_code"] == "wallet_not_connected"


class TestQuote:
    def test_basic_quote(self, connected, fake_zerox, fake_decimals):
        out = json.loads(
            defi_swap(
                {
                    "action": "quote",
                    "sell_token": USDC,
                    "buy_token": WETH,
                    "sell_amount": "1",
                }
            )
        )
        assert "isError" not in out
        details = out["details"]
        assert details["chain_id"] == 8453
        assert details["buy_amount"] == "950"
        assert details["min_buy_amount"] == "940"

    def test_native_eth_quoted(self, connected, fake_zerox, fake_decimals):
        out = json.loads(
            defi_swap(
                {
                    "action": "quote",
                    "sell_token": "ETH",
                    "buy_token": USDC,
                    "sell_amount": "0.1",
                }
            )
        )
        assert "isError" not in out
        # 0x service was called with the native sentinel address
        kwargs = fake_zerox.get_price.call_args.kwargs
        assert kwargs["sell_token"].lower() == "0x" + "e" * 40

    def test_buy_amount_quote(self, connected, fake_zerox, fake_decimals):
        out = json.loads(
            defi_swap(
                {
                    "action": "quote",
                    "sell_token": USDC,
                    "buy_token": WETH,
                    "buy_amount": "0.5",
                }
            )
        )
        assert "isError" not in out
        kwargs = fake_zerox.get_price.call_args.kwargs
        assert kwargs["buy_amount"] is not None
        assert kwargs["sell_amount"] is None

    def test_both_amounts_rejected(self, connected, fake_zerox, fake_decimals):
        out = json.loads(
            defi_swap(
                {
                    "action": "quote",
                    "sell_token": USDC,
                    "buy_token": WETH,
                    "sell_amount": "1",
                    "buy_amount": "1",
                }
            )
        )
        assert out["isError"] is True
        assert out["details"]["error_code"] == "param_error"

    def test_no_amount_rejected(self, connected, fake_zerox, fake_decimals):
        out = json.loads(
            defi_swap(
                {
                    "action": "quote",
                    "sell_token": USDC,
                    "buy_token": WETH,
                }
            )
        )
        assert out["isError"] is True
        assert out["details"]["error_code"] == "param_error"

    def test_zerox_error_propagates(self, connected, fake_zerox, fake_decimals):
        from clawmes.services.zerox import ZeroxError

        fake_zerox.get_price.side_effect = ZeroxError("rate_limited", "throttled")
        out = json.loads(
            defi_swap(
                {
                    "action": "quote",
                    "sell_token": USDC,
                    "buy_token": WETH,
                    "sell_amount": "1",
                }
            )
        )
        assert out["isError"] is True
        assert out["details"]["error_code"] == "rate_limited"

    def test_decimals_lookup_failure(self, connected, fake_zerox, fake_decimals):
        from clawmes.services.rpc import RpcError
        from clawmes.services.token_decimals import TokenDecimalsError

        fake_decimals.get_strict.side_effect = TokenDecimalsError(
            USDC, 8453, RpcError(-32000, "no node", method="eth_call")
        )
        out = json.loads(
            defi_swap(
                {
                    "action": "quote",
                    "sell_token": USDC,
                    "buy_token": WETH,
                    "sell_amount": "1",
                }
            )
        )
        assert out["isError"] is True
        assert out["details"]["error_code"] == "param_error"

    def test_bad_amount(self, connected, fake_zerox, fake_decimals):
        out = json.loads(
            defi_swap(
                {
                    "action": "quote",
                    "sell_token": USDC,
                    "buy_token": WETH,
                    "sell_amount": "-1",
                }
            )
        )
        assert out["isError"] is True
        assert out["details"]["error_code"] == "param_error"

    def test_slippage_passed_through(self, connected, fake_zerox, fake_decimals):
        defi_swap(
            {
                "action": "quote",
                "sell_token": USDC,
                "buy_token": WETH,
                "sell_amount": "1",
                "slippage_bps": 250,
            }
        )
        kwargs = fake_zerox.get_price.call_args.kwargs
        assert kwargs["slippage_bps"] == 250

    def test_explicit_chain_id(self, connected, fake_zerox, fake_decimals):
        defi_swap(
            {
                "action": "quote",
                "sell_token": USDC,
                "buy_token": WETH,
                "sell_amount": "1",
                "chain_id": 1,
            }
        )
        kwargs = fake_zerox.get_price.call_args.kwargs
        assert kwargs["chain_id"] == 1

    def test_chain_id_falls_back(self, monkeypatch, fake_zerox, fake_decimals):
        state = WalletState(connected=True, mode="local", address=OWNER, chain_id=None)
        monkeypatch.setattr("clawmes.tools.defi_swap.get_wallet_state", lambda: state)
        defi_swap(
            {
                "action": "quote",
                "sell_token": USDC,
                "buy_token": WETH,
                "sell_amount": "1",
            }
        )
        kwargs = fake_zerox.get_price.call_args.kwargs
        assert kwargs["chain_id"] == 8453


class TestRoute:
    def test_route_returns_aggregator_list(self, connected, fake_zerox, fake_decimals):
        out = json.loads(
            defi_swap(
                {
                    "action": "route",
                    "sell_token": USDC,
                    "buy_token": WETH,
                    "sell_amount": "1",
                }
            )
        )
        assert "isError" not in out
        details = out["details"]
        assert "routes" in details
        assert len(details["routes"]) == 1
        assert details["routes"][0]["aggregator"] == "0x"
        assert details["best"] == "0x"

    def test_route_records_aggregator_failure(self, connected, fake_zerox, fake_decimals):
        from clawmes.services.zerox import ZeroxError

        fake_zerox.get_price.side_effect = ZeroxError("insufficient_liquidity", "no path")
        out = json.loads(
            defi_swap(
                {
                    "action": "route",
                    "sell_token": USDC,
                    "buy_token": WETH,
                    "sell_amount": "1",
                }
            )
        )
        # All aggregators failed → error result
        assert out["isError"] is True
        assert out["details"]["error_code"] == "api_error"


class TestSwap:
    def test_basic_swap(self, connected, fake_zerox, fake_decimals, fake_mode):
        out = json.loads(
            defi_swap(
                {
                    "action": "swap",
                    "sell_token": USDC,
                    "buy_token": WETH,
                    "sell_amount": "1",
                }
            )
        )
        assert "isError" not in out
        details = out["details"]
        assert details["tx_hash"] == "0x" + "f" * 64
        assert details["router"] == "0x" + "1" * 40
        # Mode received the calldata
        kwargs = fake_mode.send_transaction.call_args.kwargs
        assert kwargs["to"] == "0x" + "1" * 40
        assert kwargs["data"] == "0xdeadbeef"
        assert kwargs["value"] == 0

    def test_swap_with_native_eth_value(self, connected, fake_zerox, fake_decimals, fake_mode):
        # Modify the quote to include a value (native ETH swap)
        fake_zerox.get_quote.return_value = {
            "sellAmount": "100000000000000000",
            "buyAmount": "100",
            "minBuyAmount": "99",
            "transaction": {
                "to": "0x" + "1" * 40,
                "data": "0xdead",
                "value": "0x16345785d8a0000",  # 0.1 ETH
                "gas": "0x30000",
            },
        }
        defi_swap(
            {
                "action": "swap",
                "sell_token": "ETH",
                "buy_token": USDC,
                "sell_amount": "0.1",
            }
        )
        kwargs = fake_mode.send_transaction.call_args.kwargs
        assert kwargs["value"] == 0x16345785D8A0000

    def test_swap_zerox_failure(self, connected, fake_zerox, fake_decimals, fake_mode):
        from clawmes.services.zerox import ZeroxError

        fake_zerox.get_quote.side_effect = ZeroxError("insufficient_liquidity", "no path")
        out = json.loads(
            defi_swap(
                {
                    "action": "swap",
                    "sell_token": USDC,
                    "buy_token": WETH,
                    "sell_amount": "1",
                }
            )
        )
        assert out["isError"] is True
        assert out["details"]["error_code"] == "insufficient_liquidity"
        fake_mode.send_transaction.assert_not_called()

    def test_swap_no_calldata(self, connected, fake_zerox, fake_decimals, fake_mode):
        # 0x somehow returns a quote without `transaction.to` or `data`
        fake_zerox.get_quote.return_value = {"sellAmount": "1", "buyAmount": "1"}
        out = json.loads(
            defi_swap(
                {
                    "action": "swap",
                    "sell_token": USDC,
                    "buy_token": WETH,
                    "sell_amount": "1",
                }
            )
        )
        assert out["isError"] is True
        assert out["details"]["error_code"] == "api_error"

    def test_swap_malformed_gas(self, connected, fake_zerox, fake_decimals, fake_mode):
        fake_zerox.get_quote.return_value = {
            "sellAmount": "1",
            "buyAmount": "1",
            "transaction": {
                "to": "0x" + "1" * 40,
                "data": "0xdead",
                "value": "garbage",
                "gas": "0x30000",
            },
        }
        out = json.loads(
            defi_swap(
                {
                    "action": "swap",
                    "sell_token": USDC,
                    "buy_token": WETH,
                    "sell_amount": "1",
                }
            )
        )
        assert out["isError"] is True
        assert out["details"]["error_code"] == "api_error"

    def test_swap_no_active_mode(self, connected, fake_zerox, fake_decimals, monkeypatch):
        from clawmes.services import wallet as wallet_mod

        svc = MagicMock()
        svc.active_mode = None
        monkeypatch.setattr(wallet_mod, "get_wallet_service", lambda: svc)
        out = json.loads(
            defi_swap(
                {
                    "action": "swap",
                    "sell_token": USDC,
                    "buy_token": WETH,
                    "sell_amount": "1",
                }
            )
        )
        assert out["isError"] is True
        assert out["details"]["error_code"] == "wallet_not_connected"

    def test_swap_send_failure(self, connected, fake_zerox, fake_decimals, fake_mode):
        fake_mode.send_transaction.side_effect = RuntimeError("rejected")
        out = json.loads(
            defi_swap(
                {
                    "action": "swap",
                    "sell_token": USDC,
                    "buy_token": WETH,
                    "sell_amount": "1",
                }
            )
        )
        assert out["isError"] is True
        assert out["details"]["error_code"] == "send_failed"

    def test_swap_no_address_in_state(self, monkeypatch, fake_zerox, fake_decimals, fake_mode):
        # Wallet state.connected=True but address is None — defensive
        state = WalletState(connected=True, mode="local", address=None, chain_id=8453)
        monkeypatch.setattr("clawmes.tools.defi_swap.get_wallet_state", lambda: state)
        out = json.loads(
            defi_swap(
                {
                    "action": "swap",
                    "sell_token": USDC,
                    "buy_token": WETH,
                    "sell_amount": "1",
                }
            )
        )
        assert out["isError"] is True
        assert out["details"]["error_code"] == "wallet_not_connected"


class TestHelpers:
    def test_resolve_token_passes_through_none(self):
        from clawmes.tools.defi_swap import _resolve_token

        # Defensive: should never receive None in normal flow but we
        # exercise the early-return branch.
        assert _resolve_token(None) is None  # type: ignore[arg-type]

    def test_buy_amount_negative_rejected(self, connected, fake_zerox, fake_decimals):
        # Exercises the buy_amount → ValueError branch in _resolve_amounts.
        out = json.loads(
            defi_swap(
                {
                    "action": "quote",
                    "sell_token": USDC,
                    "buy_token": WETH,
                    "buy_amount": "-5",
                }
            )
        )
        assert out["isError"] is True
        assert out["details"]["error_code"] == "param_error"

    def test_route_renders_error_aggregator_line(self, connected, fake_zerox, fake_decimals):
        # We need at least one successful aggregator AND one failed for
        # the rendering branch to be exercised. Today we only have 0x
        # so simulate the 'mixed' case by mocking the helper directly.
        from clawmes.tools.defi_swap import _render_routes

        mixed = [
            {
                "aggregator": "0x",
                "buy_amount": "100",
                "min_buy_amount": "99",
                "estimated_gas": "200000",
            },
            {"aggregator": "1inch", "error": "no liquidity", "error_code": "x"},
        ]
        rendered = _render_routes(mixed, USDC, WETH)
        assert "0x: buy=100" in rendered
        assert "1inch: no liquidity" in rendered


class TestRegister:
    def test_register_calls_ctx(self):
        from clawmes.tools import defi_swap as defi_swap_mod

        recorded = []

        class FakeCtx:
            def register_tool(self, **kw):
                recorded.append(kw)

        defi_swap_mod.register(FakeCtx())
        assert len(recorded) == 1
        assert recorded[0]["name"] == "defi_swap"
