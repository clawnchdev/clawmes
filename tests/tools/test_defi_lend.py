"""Tests for the ``defi_lend`` tool."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from clawmes.tools.defi_lend import defi_lend
from clawmes.wallet.state import WalletState

OWNER = "0x" + "a" * 40
USDC = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    from clawmes.policy import storage as policy_storage
    from clawmes.services import rpc as rpc_mod
    from clawmes.services import token_decimals as td_mod
    from clawmes.services import wallet as wallet_mod

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(wallet_mod, "_instance", None)
    monkeypatch.setattr(td_mod, "_instance", None)
    monkeypatch.setattr(rpc_mod, "_instance", None)
    policy_storage.save_policies([])


@pytest.fixture
def connected(monkeypatch):
    state = WalletState.for_chain(mode="local", address=OWNER, chain_id=8453)
    monkeypatch.setattr("clawmes.tools.defi_lend.get_wallet_state", lambda: state)
    return state


@pytest.fixture
def fake_decimals(monkeypatch):
    from clawmes.services import token_decimals as td_mod

    svc = MagicMock()
    svc.get_strict.return_value = 6  # USDC
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


@pytest.fixture
def fake_rpc(monkeypatch):
    from clawmes.services import rpc as rpc_mod

    svc = MagicMock()
    # Default: position with $1500 collateral, $500 debt, healthy
    chunks = [
        10**8 * 1500,
        10**8 * 500,
        10**8 * 1000,
        8500,
        7500,
        int(2.5 * 10**18),
    ]
    body = "".join(format(c, "064x") for c in chunks)
    svc.eth_call.return_value = "0x" + body
    monkeypatch.setattr(rpc_mod, "_instance", svc)
    return svc


class TestNoWallet:
    def test_no_wallet(self, monkeypatch):
        monkeypatch.setattr(
            "clawmes.tools.defi_lend.get_wallet_state",
            lambda: WalletState.disconnected(),
        )
        out = json.loads(defi_lend({"action": "health_factor"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "wallet_not_connected"


class TestUnsupportedChain:
    def test_health_factor_on_bsc(self, connected, fake_rpc):
        out = json.loads(defi_lend({"action": "health_factor", "chain_id": 56}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "unsupported_chain"

    def test_supply_on_bsc(self, connected, fake_decimals, fake_mode):
        out = json.loads(
            defi_lend(
                {
                    "action": "supply",
                    "asset": USDC,
                    "amount": "100",
                    "chain_id": 56,
                }
            )
        )
        assert out["isError"] is True
        assert out["details"]["error_code"] == "unsupported_chain"


class TestHealthFactor:
    def test_basic(self, connected, fake_rpc):
        out = json.loads(defi_lend({"action": "health_factor"}))
        assert "isError" not in out
        details = out["details"]
        assert details["total_collateral_base"] == 10**8 * 1500
        assert details["total_debt_base"] == 10**8 * 500
        assert details["health_factor_human"] == 2.5
        assert details["risk_level"] == "safe"

    def test_no_debt(self, connected, fake_rpc):
        # All zeros — no position
        fake_rpc.eth_call.return_value = "0x" + "0" * (64 * 6)
        out = json.loads(defi_lend({"action": "health_factor"}))
        assert out["details"]["risk_level"] == "no_debt"

    def test_critical(self, connected, fake_rpc):
        chunks = [10**8 * 1500, 10**8 * 1400, 0, 8500, 7500, int(1.05 * 10**18)]
        fake_rpc.eth_call.return_value = "0x" + "".join(format(c, "064x") for c in chunks)
        out = json.loads(defi_lend({"action": "health_factor"}))
        assert out["details"]["risk_level"] == "critical"

    def test_liquidatable(self, connected, fake_rpc):
        chunks = [10**8 * 1500, 10**8 * 1500, 0, 8500, 7500, int(0.95 * 10**18)]
        fake_rpc.eth_call.return_value = "0x" + "".join(format(c, "064x") for c in chunks)
        out = json.loads(defi_lend({"action": "health_factor"}))
        assert out["details"]["risk_level"] == "liquidatable"

    def test_risky(self, connected, fake_rpc):
        chunks = [10**8 * 1500, 10**8 * 1100, 0, 8500, 7500, int(1.3 * 10**18)]
        fake_rpc.eth_call.return_value = "0x" + "".join(format(c, "064x") for c in chunks)
        out = json.loads(defi_lend({"action": "health_factor"}))
        assert out["details"]["risk_level"] == "risky"

    def test_rpc_failure(self, connected, fake_rpc):
        from clawmes.services.rpc import RpcError

        fake_rpc.eth_call.side_effect = RpcError(-32000, "no node", method="eth_call")
        out = json.loads(defi_lend({"action": "health_factor"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "rpc_error"


class TestSupply:
    def test_basic(self, connected, fake_decimals, fake_mode):
        out = json.loads(defi_lend({"action": "supply", "asset": USDC, "amount": "100"}))
        assert "isError" not in out
        kwargs = fake_mode.send_transaction.call_args.kwargs
        # Pool address for Base
        assert kwargs["to"].lower() == "0xa238dd80c259a72e81d7e4664a9801593f98d1c5"
        assert kwargs["data"].startswith("0x617ba037")  # supply selector

    def test_supply_all_rejected(self, connected, fake_decimals, fake_mode):
        # 'all' is only valid for withdraw / repay
        out = json.loads(defi_lend({"action": "supply", "asset": USDC, "amount": "all"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "param_error"

    def test_invalid_asset(self, connected, fake_decimals, fake_mode):
        out = json.loads(defi_lend({"action": "supply", "asset": "0xshort", "amount": "100"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "param_error"

    def test_decimals_lookup_failure(self, connected, fake_decimals, fake_mode):
        from clawmes.services.rpc import RpcError
        from clawmes.services.token_decimals import TokenDecimalsError

        fake_decimals.get_strict.side_effect = TokenDecimalsError(
            USDC, 8453, RpcError(-32000, "no node", method="eth_call")
        )
        out = json.loads(defi_lend({"action": "supply", "asset": USDC, "amount": "100"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "decimals_lookup_failed"

    def test_no_active_mode(self, connected, fake_decimals, monkeypatch):
        from clawmes.services import wallet as wallet_mod

        svc = MagicMock()
        svc.active_mode = None
        monkeypatch.setattr(wallet_mod, "get_wallet_service", lambda: svc)
        out = json.loads(defi_lend({"action": "supply", "asset": USDC, "amount": "100"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "wallet_not_connected"

    def test_send_failure(self, connected, fake_decimals, fake_mode):
        fake_mode.send_transaction.side_effect = RuntimeError("rejected")
        out = json.loads(defi_lend({"action": "supply", "asset": USDC, "amount": "100"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "send_failed"

    def test_bad_amount(self, connected, fake_decimals, fake_mode):
        out = json.loads(defi_lend({"action": "supply", "asset": USDC, "amount": "-1"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "param_error"


class TestWithdraw:
    def test_specific_amount(self, connected, fake_decimals, fake_mode):
        out = json.loads(defi_lend({"action": "withdraw", "asset": USDC, "amount": "50"}))
        assert "isError" not in out
        assert fake_mode.send_transaction.call_args.kwargs["data"].startswith("0x69328dec")

    def test_withdraw_all(self, connected, fake_decimals, fake_mode):
        out = json.loads(defi_lend({"action": "withdraw", "asset": USDC, "amount": "all"}))
        assert "isError" not in out
        details = out["details"]
        # amount=type(uint256).max signals "withdraw entire balance"
        assert details["is_full_balance"] is True


class TestBorrow:
    def test_basic(self, connected, fake_decimals, fake_mode):
        out = json.loads(defi_lend({"action": "borrow", "asset": USDC, "amount": "200"}))
        assert "isError" not in out
        kwargs = fake_mode.send_transaction.call_args.kwargs
        assert kwargs["data"].startswith("0xa415bcad")  # borrow selector

    def test_borrow_all_rejected(self, connected, fake_decimals, fake_mode):
        out = json.loads(defi_lend({"action": "borrow", "asset": USDC, "amount": "all"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "param_error"


class TestRepay:
    def test_basic(self, connected, fake_decimals, fake_mode):
        out = json.loads(defi_lend({"action": "repay", "asset": USDC, "amount": "300"}))
        assert "isError" not in out
        kwargs = fake_mode.send_transaction.call_args.kwargs
        assert kwargs["data"].startswith("0x573ade81")  # repay selector

    def test_repay_all(self, connected, fake_decimals, fake_mode):
        out = json.loads(defi_lend({"action": "repay", "asset": USDC, "amount": "all"}))
        assert "isError" not in out
        assert out["details"]["is_full_balance"] is True


class TestErrorPropagation:
    def test_withdraw_bad_amount(self, connected, fake_decimals, fake_mode):
        out = json.loads(defi_lend({"action": "withdraw", "asset": USDC, "amount": "-5"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "param_error"

    def test_repay_decimals_failure(self, connected, fake_decimals, fake_mode):
        from clawmes.services.rpc import RpcError
        from clawmes.services.token_decimals import TokenDecimalsError

        fake_decimals.get_strict.side_effect = TokenDecimalsError(
            USDC, 8453, RpcError(-32000, "no node", method="eth_call")
        )
        out = json.loads(defi_lend({"action": "repay", "asset": USDC, "amount": "100"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "decimals_lookup_failed"

    def test_borrow_decimals_failure(self, connected, fake_decimals, fake_mode):
        from clawmes.services.rpc import RpcError
        from clawmes.services.token_decimals import TokenDecimalsError

        fake_decimals.get_strict.side_effect = TokenDecimalsError(
            USDC, 8453, RpcError(-32000, "no node", method="eth_call")
        )
        out = json.loads(defi_lend({"action": "borrow", "asset": USDC, "amount": "200"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "decimals_lookup_failed"


class TestChainId:
    def test_explicit_chain_id(self, connected, fake_decimals, fake_mode):
        defi_lend(
            {
                "action": "supply",
                "asset": USDC,
                "amount": "100",
                "chain_id": 1,  # mainnet
            }
        )
        kwargs = fake_mode.send_transaction.call_args.kwargs
        # Mainnet pool address
        assert kwargs["to"].lower() == "0x87870bca3f3fd6335c3f4ce8392d69350b4fa4e2"

    def test_chain_id_falls_back(self, monkeypatch, fake_decimals, fake_mode):
        state = WalletState(connected=True, mode="local", address=OWNER, chain_id=None)
        monkeypatch.setattr("clawmes.tools.defi_lend.get_wallet_state", lambda: state)
        defi_lend({"action": "supply", "asset": USDC, "amount": "100"})
        kwargs = fake_mode.send_transaction.call_args.kwargs
        # Falls back to Base
        assert kwargs["to"].lower() == "0xa238dd80c259a72e81d7e4664a9801593f98d1c5"


class TestRegister:
    def test_register_calls_ctx(self):
        from clawmes.tools import defi_lend as defi_lend_mod

        recorded = []

        class FakeCtx:
            def register_tool(self, **kw):
                recorded.append(kw)

        defi_lend_mod.register(FakeCtx())
        assert len(recorded) == 1
        assert recorded[0]["name"] == "defi_lend"
