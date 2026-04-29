"""Tests for the ``bridge`` tool."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from clawmes.tools.bridge import bridge
from clawmes.wallet.state import WalletState

OWNER = "0x" + "a" * 40
USDC_BASE = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
USDC_MAINNET = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    from clawmes.policy import storage as policy_storage
    from clawmes.services import lifi as lifi_mod
    from clawmes.services import token_decimals as td_mod
    from clawmes.services import wallet as wallet_mod

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(wallet_mod, "_instance", None)
    monkeypatch.setattr(td_mod, "_instance", None)
    monkeypatch.setattr(lifi_mod, "_instance", None)
    policy_storage.save_policies([])


@pytest.fixture
def connected(monkeypatch):
    state = WalletState.for_chain(mode="local", address=OWNER, chain_id=8453)
    monkeypatch.setattr("clawmes.tools.bridge.get_wallet_state", lambda: state)
    return state


@pytest.fixture
def fake_lifi(monkeypatch):
    from clawmes.services import lifi as lifi_mod

    svc = MagicMock()
    svc.get_quote.return_value = {
        "id": "quote-id-123",
        "tool": "stargate",
        "estimate": {
            "fromAmount": "1000000",
            "toAmount": "990000",
            "toAmountMin": "985000",
            "executionDuration": 240,
            "feeCosts": [],
        },
        "transactionRequest": {
            "to": "0x" + "1" * 40,
            "data": "0xdeadbeef",
            "value": "0x0",
            "gasLimit": "0x40000",
            "chainId": "0x2105",  # Base = 8453 = 0x2105
        },
        "includedSteps": [{"tool": "stargate"}],
    }
    svc.get_status.return_value = {
        "status": "DONE",
        "substatus": "COMPLETED",
        "fromChainId": 8453,
        "toChainId": 1,
        "sending": {"txHash": "0x" + "f" * 64},
        "receiving": {"txHash": "0x" + "1" * 64},
    }
    svc.get_connections.return_value = {
        "connections": [
            {"fromChainId": 8453, "toChainId": 1, "fromTokens": []},
        ]
    }
    monkeypatch.setattr(lifi_mod, "_instance", svc)
    return svc


@pytest.fixture
def fake_decimals(monkeypatch):
    from clawmes.services import token_decimals as td_mod

    svc = MagicMock()
    svc.get_strict.return_value = 6
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
    def test_quote_no_wallet(self, monkeypatch, fake_lifi, fake_decimals):
        monkeypatch.setattr(
            "clawmes.tools.bridge.get_wallet_state",
            lambda: WalletState.disconnected(),
        )
        out = json.loads(
            bridge(
                {
                    "action": "quote",
                    "from_token": USDC_BASE,
                    "to_token": USDC_MAINNET,
                    "to_chain": 1,
                    "amount": "1",
                }
            )
        )
        assert out["isError"] is True
        assert out["details"]["error_code"] == "wallet_not_connected"

    def test_status_no_wallet_ok(self, monkeypatch, fake_lifi):
        # status doesn't need a wallet
        monkeypatch.setattr(
            "clawmes.tools.bridge.get_wallet_state",
            lambda: WalletState.disconnected(),
        )
        out = json.loads(bridge({"action": "status", "tx_hash": "0xabc"}))
        assert "isError" not in out

    def test_routes_no_wallet_ok(self, monkeypatch, fake_lifi):
        monkeypatch.setattr(
            "clawmes.tools.bridge.get_wallet_state",
            lambda: WalletState.disconnected(),
        )
        out = json.loads(bridge({"action": "routes"}))
        assert "isError" not in out


class TestQuote:
    def test_basic(self, connected, fake_lifi, fake_decimals):
        out = json.loads(
            bridge(
                {
                    "action": "quote",
                    "from_token": USDC_BASE,
                    "to_token": USDC_MAINNET,
                    "to_chain": 1,
                    "amount": "1",
                }
            )
        )
        assert "isError" not in out
        details = out["details"]
        assert details["tool"] == "stargate"
        assert details["to_amount"] == "990000"

    def test_quote_native(self, connected, fake_lifi, fake_decimals):
        out = json.loads(
            bridge(
                {
                    "action": "quote",
                    "from_token": "ETH",
                    "to_token": "ETH",
                    "to_chain": 1,
                    "amount": "0.1",
                }
            )
        )
        assert "isError" not in out
        kwargs = fake_lifi.get_quote.call_args.kwargs
        assert kwargs["from_token"].lower() == "0x" + "e" * 40

    def test_missing_to_chain(self, connected, fake_lifi, fake_decimals):
        out = json.loads(
            bridge(
                {
                    "action": "quote",
                    "from_token": USDC_BASE,
                    "to_token": USDC_MAINNET,
                    "amount": "1",
                }
            )
        )
        assert out["isError"] is True
        assert out["details"]["error_code"] == "param_error"

    def test_same_chain_rejected(self, connected, fake_lifi, fake_decimals):
        out = json.loads(
            bridge(
                {
                    "action": "quote",
                    "from_token": USDC_BASE,
                    "to_token": USDC_BASE,
                    "to_chain": 8453,
                    "amount": "1",
                }
            )
        )
        assert out["isError"] is True
        assert out["details"]["error_code"] == "param_error"

    def test_lifi_no_route(self, connected, fake_lifi, fake_decimals):
        from clawmes.services.lifi import LifiError

        fake_lifi.get_quote.side_effect = LifiError("no_route", "no path")
        out = json.loads(
            bridge(
                {
                    "action": "quote",
                    "from_token": USDC_BASE,
                    "to_token": USDC_MAINNET,
                    "to_chain": 1,
                    "amount": "1",
                }
            )
        )
        assert out["isError"] is True
        assert out["details"]["error_code"] == "no_route"

    def test_decimals_lookup_failure(self, connected, fake_lifi, fake_decimals):
        from clawmes.services.rpc import RpcError
        from clawmes.services.token_decimals import TokenDecimalsError

        fake_decimals.get_strict.side_effect = TokenDecimalsError(
            USDC_BASE, 8453, RpcError(-32000, "no node", method="eth_call")
        )
        out = json.loads(
            bridge(
                {
                    "action": "quote",
                    "from_token": USDC_BASE,
                    "to_token": USDC_MAINNET,
                    "to_chain": 1,
                    "amount": "1",
                }
            )
        )
        assert out["isError"] is True
        assert out["details"]["error_code"] == "param_error"

    def test_bad_amount(self, connected, fake_lifi, fake_decimals):
        out = json.loads(
            bridge(
                {
                    "action": "quote",
                    "from_token": USDC_BASE,
                    "to_token": USDC_MAINNET,
                    "to_chain": 1,
                    "amount": "-1",
                }
            )
        )
        assert out["isError"] is True
        assert out["details"]["error_code"] == "param_error"

    def test_garbage_slippage_uses_default(self, connected, fake_lifi, fake_decimals):
        bridge(
            {
                "action": "quote",
                "from_token": USDC_BASE,
                "to_token": USDC_MAINNET,
                "to_chain": 1,
                "amount": "1",
                "slippage": "garbage",
            }
        )
        kwargs = fake_lifi.get_quote.call_args.kwargs
        assert kwargs["slippage"] == 0.005

    def test_explicit_to_address(self, connected, fake_lifi, fake_decimals):
        bridge(
            {
                "action": "quote",
                "from_token": USDC_BASE,
                "to_token": USDC_MAINNET,
                "to_chain": 1,
                "amount": "1",
                "to_address": "0x" + "9" * 40,
            }
        )
        kwargs = fake_lifi.get_quote.call_args.kwargs
        assert kwargs["to_address"] == "0x" + "9" * 40


class TestBridge:
    def test_basic(self, connected, fake_lifi, fake_decimals, fake_mode):
        out = json.loads(
            bridge(
                {
                    "action": "bridge",
                    "from_token": USDC_BASE,
                    "to_token": USDC_MAINNET,
                    "to_chain": 1,
                    "amount": "1",
                }
            )
        )
        assert "isError" not in out
        details = out["details"]
        assert details["tx_hash"] == "0x" + "f" * 64
        assert details["tool"] == "stargate"
        kwargs = fake_mode.send_transaction.call_args.kwargs
        assert kwargs["to"] == "0x" + "1" * 40
        assert kwargs["data"] == "0xdeadbeef"

    def test_no_calldata(self, connected, fake_lifi, fake_decimals, fake_mode):
        fake_lifi.get_quote.return_value = {"id": "x", "estimate": {}}
        out = json.loads(
            bridge(
                {
                    "action": "bridge",
                    "from_token": USDC_BASE,
                    "to_token": USDC_MAINNET,
                    "to_chain": 1,
                    "amount": "1",
                }
            )
        )
        assert out["isError"] is True
        assert out["details"]["error_code"] == "api_error"

    def test_malformed_value(self, connected, fake_lifi, fake_decimals, fake_mode):
        fake_lifi.get_quote.return_value = {
            "id": "x",
            "tool": "x",
            "estimate": {},
            "transactionRequest": {
                "to": "0x" + "1" * 40,
                "data": "0xdead",
                "value": "garbage",
                "gasLimit": "0x1",
                "chainId": "0x1",
            },
        }
        out = json.loads(
            bridge(
                {
                    "action": "bridge",
                    "from_token": USDC_BASE,
                    "to_token": USDC_MAINNET,
                    "to_chain": 1,
                    "amount": "1",
                }
            )
        )
        assert out["isError"] is True
        assert out["details"]["error_code"] == "api_error"

    def test_no_active_mode(self, connected, fake_lifi, fake_decimals, monkeypatch):
        from clawmes.services import wallet as wallet_mod

        svc = MagicMock()
        svc.active_mode = None
        monkeypatch.setattr(wallet_mod, "get_wallet_service", lambda: svc)
        out = json.loads(
            bridge(
                {
                    "action": "bridge",
                    "from_token": USDC_BASE,
                    "to_token": USDC_MAINNET,
                    "to_chain": 1,
                    "amount": "1",
                }
            )
        )
        assert out["isError"] is True
        assert out["details"]["error_code"] == "wallet_not_connected"

    def test_send_failure(self, connected, fake_lifi, fake_decimals, fake_mode):
        fake_mode.send_transaction.side_effect = RuntimeError("rejected")
        out = json.loads(
            bridge(
                {
                    "action": "bridge",
                    "from_token": USDC_BASE,
                    "to_token": USDC_MAINNET,
                    "to_chain": 1,
                    "amount": "1",
                }
            )
        )
        assert out["isError"] is True
        assert out["details"]["error_code"] == "send_failed"

    def test_lifi_error(self, connected, fake_lifi, fake_decimals, fake_mode):
        from clawmes.services.lifi import LifiError

        fake_lifi.get_quote.side_effect = LifiError("rate_limited", "throttled")
        out = json.loads(
            bridge(
                {
                    "action": "bridge",
                    "from_token": USDC_BASE,
                    "to_token": USDC_MAINNET,
                    "to_chain": 1,
                    "amount": "1",
                }
            )
        )
        assert out["isError"] is True
        assert out["details"]["error_code"] == "rate_limited"


class TestStatus:
    def test_basic(self, connected, fake_lifi):
        out = json.loads(bridge({"action": "status", "tx_hash": "0xabc"}))
        assert "isError" not in out
        assert out["details"]["status"] == "DONE"

    def test_lifi_error(self, connected, fake_lifi):
        from clawmes.services.lifi import LifiError

        fake_lifi.get_status.side_effect = LifiError("api_error", "boom")
        out = json.loads(bridge({"action": "status", "tx_hash": "0xabc"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "api_error"


class TestRoutes:
    def test_basic(self, connected, fake_lifi):
        out = json.loads(bridge({"action": "routes"}))
        assert "isError" not in out
        assert out["details"]["count"] == 1

    def test_with_filter(self, connected, fake_lifi):
        bridge({"action": "routes", "from_chain": 8453, "to_chain": 1})
        kwargs = fake_lifi.get_connections.call_args.kwargs
        assert kwargs["from_chain"] == 8453
        assert kwargs["to_chain"] == 1

    def test_lifi_error(self, connected, fake_lifi):
        from clawmes.services.lifi import LifiError

        fake_lifi.get_connections.side_effect = LifiError("api_error", "boom")
        out = json.loads(bridge({"action": "routes"}))
        assert out["isError"] is True


class TestHelpers:
    def test_resolve_token_none(self):
        from clawmes.tools.bridge import _resolve_token

        assert _resolve_token(None) is None  # type: ignore[arg-type]

    def test_bridge_action_fails_on_input_validation(
        self, connected, fake_lifi, fake_decimals, fake_mode
    ):
        # bridge action also short-circuits on _read_quote_inputs
        # error result. Same input issue as the quote test but
        # exercising the bridge code path.
        out = json.loads(
            bridge(
                {
                    "action": "bridge",
                    "from_token": USDC_BASE,
                    "to_token": USDC_MAINNET,
                    "amount": "1",
                    # Missing to_chain
                }
            )
        )
        assert out["isError"] is True
        assert out["details"]["error_code"] == "param_error"
        # Wallet wasn't called
        fake_mode.send_transaction.assert_not_called()


class TestRegister:
    def test_register_calls_ctx(self):
        from clawmes.tools import bridge as bridge_mod

        recorded = []

        class FakeCtx:
            def register_tool(self, **kw):
                recorded.append(kw)

        bridge_mod.register(FakeCtx())
        assert len(recorded) == 1
        assert recorded[0]["name"] == "bridge"
