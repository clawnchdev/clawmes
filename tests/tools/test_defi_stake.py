"""Tests for the ``defi_stake`` tool."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from clawmes.tools.defi_stake import defi_stake
from clawmes.wallet.state import WalletState

OWNER = "0x" + "a" * 40


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    from clawmes.policy import storage as policy_storage
    from clawmes.services import rpc as rpc_mod
    from clawmes.services import wallet as wallet_mod

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(wallet_mod, "_instance", None)
    monkeypatch.setattr(rpc_mod, "_instance", None)
    policy_storage.save_policies([])


@pytest.fixture
def connected(monkeypatch):
    state = WalletState.for_chain(mode="local", address=OWNER, chain_id=1)
    monkeypatch.setattr("clawmes.tools.defi_stake.get_wallet_state", lambda: state)
    return state


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
    # Default: 5 stETH balance
    svc.eth_call.return_value = "0x" + format(5 * 10**18, "064x")
    monkeypatch.setattr(rpc_mod, "_instance", svc)
    return svc


class TestNoWallet:
    def test_no_wallet(self, monkeypatch):
        monkeypatch.setattr(
            "clawmes.tools.defi_stake.get_wallet_state",
            lambda: WalletState.disconnected(),
        )
        out = json.loads(defi_stake({"action": "info", "protocol": "lido"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "wallet_not_connected"


class TestUnknownProtocol:
    def test_rejects(self, connected):
        out = json.loads(defi_stake({"action": "stake", "protocol": "ankr", "amount": "1"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "param_error"


class TestStake:
    def test_lido_stake(self, connected, fake_mode):
        out = json.loads(defi_stake({"action": "stake", "protocol": "lido", "amount": "0.5"}))
        assert "isError" not in out
        details = out["details"]
        assert details["tx_hash"] == "0x" + "f" * 64
        assert details["amount_wei"] == str(5 * 10**17)
        kwargs = fake_mode.send_transaction.call_args.kwargs
        # Lido stETH contract address (case-insensitive — eth-utils
        # may normalize to checksummed form)
        assert kwargs["to"].lower() == "0xae7ab96520de3a18e5e111b5eaab095312d7fe84"
        # Lido submit selector
        assert kwargs["data"].startswith("0xa1903eab")
        # ETH value sent with the call
        assert kwargs["value"] == 5 * 10**17

    def test_rocketpool_stake(self, connected, fake_mode):
        out = json.loads(
            defi_stake(
                {
                    "action": "stake",
                    "protocol": "rocketpool",
                    "amount": "1",
                }
            )
        )
        assert "isError" not in out
        kwargs = fake_mode.send_transaction.call_args.kwargs
        # Rocket Pool deposit() = no args = 4-byte selector only
        assert kwargs["data"] == "0xd0e30db0"

    def test_stake_l2_unsupported(self, connected, fake_mode):
        out = json.loads(
            defi_stake(
                {
                    "action": "stake",
                    "protocol": "lido",
                    "amount": "0.5",
                    "chain_id": 8453,
                }
            )
        )
        assert out["isError"] is True
        assert out["details"]["error_code"] == "unsupported_chain"

    def test_stake_no_active_mode(self, connected, monkeypatch):
        from clawmes.services import wallet as wallet_mod

        svc = MagicMock()
        svc.active_mode = None
        monkeypatch.setattr(wallet_mod, "get_wallet_service", lambda: svc)
        out = json.loads(defi_stake({"action": "stake", "protocol": "lido", "amount": "1"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "wallet_not_connected"

    def test_stake_send_failure(self, connected, fake_mode):
        fake_mode.send_transaction.side_effect = RuntimeError("rejected")
        out = json.loads(defi_stake({"action": "stake", "protocol": "lido", "amount": "1"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "send_failed"

    def test_stake_bad_amount(self, connected, fake_mode):
        out = json.loads(defi_stake({"action": "stake", "protocol": "lido", "amount": "-1"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "param_error"


class TestInfo:
    def test_lido(self, connected, fake_rpc):
        out = json.loads(defi_stake({"action": "info", "protocol": "lido"}))
        assert "isError" not in out
        details = out["details"]
        assert details["protocol"] == "lido"
        assert details["balance"] == str(5 * 10**18)
        assert details["balance_eth_estimate"] == 5.0

    def test_info_l2_unsupported(self, connected, fake_rpc):
        out = json.loads(defi_stake({"action": "info", "protocol": "lido", "chain_id": 137}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "unsupported_chain"

    def test_info_rpc_failure(self, connected, fake_rpc):
        from clawmes.services.rpc import RpcError

        fake_rpc.eth_call.side_effect = RpcError(-32000, "no node", method="eth_call")
        out = json.loads(defi_stake({"action": "info", "protocol": "lido"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "rpc_error"


class TestUnstake:
    def test_returns_not_implemented(self, connected):
        out = json.loads(defi_stake({"action": "unstake", "protocol": "lido"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "not_implemented"


class TestChainResolution:
    def test_chain_id_falls_back_to_mainnet_when_none(self, monkeypatch, fake_mode):
        state = WalletState(connected=True, mode="local", address=OWNER, chain_id=None)
        monkeypatch.setattr("clawmes.tools.defi_stake.get_wallet_state", lambda: state)
        out = json.loads(defi_stake({"action": "stake", "protocol": "lido", "amount": "1"}))
        assert "isError" not in out
        kwargs = fake_mode.send_transaction.call_args.kwargs
        assert kwargs["chain_id"] == 1


class TestRegister:
    def test_register_calls_ctx(self):
        from clawmes.tools import defi_stake as defi_stake_mod

        recorded = []

        class FakeCtx:
            def register_tool(self, **kw):
                recorded.append(kw)

        defi_stake_mod.register(FakeCtx())
        assert len(recorded) == 1
        assert recorded[0]["name"] == "defi_stake"
