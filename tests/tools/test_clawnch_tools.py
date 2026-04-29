"""Tests for clawnch_launch + clawnch_fees tools."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from clawmes.tools.clawnch_fees import clawnch_fees
from clawmes.tools.clawnch_launch import clawnch_launch
from clawmes.wallet.state import WalletState

OWNER = "0x" + "a" * 40


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    from clawmes.policy import storage as policy_storage
    from clawmes.services import wallet as wallet_mod

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(wallet_mod, "_instance", None)
    policy_storage.save_policies([])


@pytest.fixture
def connected(monkeypatch):
    state = WalletState.for_chain(mode="local", address=OWNER, chain_id=8453)
    monkeypatch.setattr("clawmes.tools.clawnch_launch.get_wallet_state", lambda: state)
    monkeypatch.setattr("clawmes.tools.clawnch_fees.get_wallet_state", lambda: state)
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


class TestClawnchLaunchNoWallet:
    def test_no_wallet(self, monkeypatch):
        monkeypatch.setattr(
            "clawmes.tools.clawnch_launch.get_wallet_state",
            lambda: WalletState.disconnected(),
        )
        out = json.loads(clawnch_launch({"action": "deploy"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "wallet_not_connected"


class TestClawnchLaunchActions:
    def test_deploy_requires_calldata(self, connected, fake_mode):
        out = json.loads(
            clawnch_launch(
                {
                    "action": "deploy",
                    "name": "Test",
                    "symbol": "TST",
                    "supply": "1000000",
                }
            )
        )
        assert out["isError"] is True
        assert out["details"]["error_code"] == "not_implemented"

    def test_deploy_with_calldata(self, connected, fake_mode):
        out = json.loads(clawnch_launch({"action": "deploy", "calldata": "0xdeadbeef"}))
        assert "isError" not in out
        kwargs = fake_mode.send_transaction.call_args.kwargs
        assert kwargs["data"] == "0xdeadbeef"
        assert kwargs["chain_id"] == 8453

    def test_pair_with_calldata(self, connected, fake_mode):
        out = json.loads(clawnch_launch({"action": "pair", "calldata": "0xfeed"}))
        assert "isError" not in out

    def test_seed_lp_with_calldata(self, connected, fake_mode):
        out = json.loads(clawnch_launch({"action": "seed_lp", "calldata": "0xbabe"}))
        assert "isError" not in out

    def test_info_not_implemented(self, connected, fake_mode):
        out = json.loads(clawnch_launch({"action": "info"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "not_implemented"

    def test_no_active_mode(self, connected, monkeypatch):
        from clawmes.services import wallet as wallet_mod

        svc = MagicMock()
        svc.active_mode = None
        monkeypatch.setattr(wallet_mod, "get_wallet_service", lambda: svc)
        out = json.loads(clawnch_launch({"action": "deploy", "calldata": "0xabc"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "wallet_not_connected"

    def test_send_failure(self, connected, fake_mode):
        fake_mode.send_transaction.side_effect = RuntimeError("rejected")
        out = json.loads(clawnch_launch({"action": "deploy", "calldata": "0xabc"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "send_failed"

    def test_env_override(self, monkeypatch, connected, fake_mode):
        monkeypatch.setenv("CLAWNCH_LAUNCHPAD_ADDRESS", "0x" + "9" * 40)
        clawnch_launch({"action": "deploy", "calldata": "0xabc"})
        kwargs = fake_mode.send_transaction.call_args.kwargs
        assert kwargs["to"] == "0x" + "9" * 40


class TestClawnchFees:
    def test_no_wallet(self, monkeypatch):
        monkeypatch.setattr(
            "clawmes.tools.clawnch_fees.get_wallet_state",
            lambda: WalletState.disconnected(),
        )
        out = json.loads(clawnch_fees({"action": "claim"}))
        assert out["isError"] is True

    def test_summary_not_implemented(self, connected):
        out = json.loads(clawnch_fees({"action": "summary"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "not_implemented"

    def test_history_not_implemented(self, connected):
        out = json.loads(clawnch_fees({"action": "history"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "not_implemented"

    def test_claim_requires_calldata(self, connected, fake_mode):
        out = json.loads(clawnch_fees({"action": "claim"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "not_implemented"

    def test_claim_with_calldata(self, connected, fake_mode):
        out = json.loads(clawnch_fees({"action": "claim", "calldata": "0xfeefeefee"}))
        assert "isError" not in out

    def test_claim_no_active_mode(self, connected, monkeypatch):
        from clawmes.services import wallet as wallet_mod

        svc = MagicMock()
        svc.active_mode = None
        monkeypatch.setattr(wallet_mod, "get_wallet_service", lambda: svc)
        out = json.loads(clawnch_fees({"action": "claim", "calldata": "0xabc"}))
        assert out["isError"] is True

    def test_claim_send_failure(self, connected, fake_mode):
        fake_mode.send_transaction.side_effect = RuntimeError("rejected")
        out = json.loads(clawnch_fees({"action": "claim", "calldata": "0xabc"}))
        assert out["isError"] is True


class TestRegister:
    def test_clawnch_launch(self):
        from clawmes.tools import clawnch_launch as cl

        recorded = []

        class FakeCtx:
            def register_tool(self, **kw):
                recorded.append(kw)

        cl.register(FakeCtx())
        assert recorded[0]["name"] == "clawnch_launch"

    def test_clawnch_fees(self):
        from clawmes.tools import clawnch_fees as cf

        recorded = []

        class FakeCtx:
            def register_tool(self, **kw):
                recorded.append(kw)

        cf.register(FakeCtx())
        assert recorded[0]["name"] == "clawnch_fees"
