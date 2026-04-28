"""Tests for the ``clawnchconnect`` tool."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from clawmes.tools.clawnchconnect import clawnchconnect
from clawmes.wallet.state import WalletState


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    from clawmes.services import wallet as wallet_mod

    monkeypatch.setattr(wallet_mod, "_instance", None)


def _disconnected():
    return WalletState.disconnected()


def _connected_wc():
    return WalletState.for_chain(mode="walletconnect", address="0x" + "a" * 40, chain_id=8453)


def _connected_bankr():
    return WalletState.for_chain(mode="bankr", address="0x" + "b" * 40, chain_id=8453)


class TestStatus:
    def test_status_when_disconnected(self, monkeypatch):
        monkeypatch.setattr("clawmes.tools.clawnchconnect.get_wallet_state", _disconnected)
        out = json.loads(clawnchconnect({"mode": "status"}))
        assert "isError" not in out
        assert out["details"]["connected"] is False
        body = out["content"][0]["text"]
        assert "No wallet connected" in body
        # Surfaces the available modes
        assert "walletconnect" in body
        assert "bankr" in body

    def test_status_when_connected(self, monkeypatch):
        monkeypatch.setattr("clawmes.tools.clawnchconnect.get_wallet_state", _connected_wc)
        out = json.loads(clawnchconnect({"mode": "status"}))
        assert "isError" not in out
        details = out["details"]
        assert details["connected"] is True
        assert details["mode"] == "walletconnect"
        assert details["chain_id"] == 8453


class TestRejectsBadMode:
    def test_unknown_mode(self):
        out = json.loads(clawnchconnect({"mode": "drain"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "param_error"

    def test_missing_mode(self):
        out = json.loads(clawnchconnect({}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "param_error"


class TestSameModeIdempotent:
    def test_already_connected_walletconnect(self, monkeypatch):
        monkeypatch.setattr("clawmes.tools.clawnchconnect.get_wallet_state", _connected_wc)
        out = json.loads(clawnchconnect({"mode": "walletconnect"}))
        # Idempotent: should return status, not re-pair
        assert "isError" not in out
        assert out["details"]["connected"] is True
        assert out["details"]["mode"] == "walletconnect"


class TestWalletConnect:
    @pytest.fixture
    def fake_svc(self, monkeypatch):
        from clawmes.services import wallet as wallet_mod

        # Disconnected at the start so we don't hit the idempotent path
        monkeypatch.setattr("clawmes.tools.clawnchconnect.get_wallet_state", _disconnected)
        svc = MagicMock()
        monkeypatch.setattr(wallet_mod, "get_wallet_service", lambda: svc)
        return svc

    def test_walletconnect_returns_uri(self, fake_svc):
        fake_svc.connect_walletconnect.return_value = WalletState(
            connected=False,
            mode="walletconnect",
            balances={"_pair_uri": "wc:abc@2"},
        )
        out = json.loads(clawnchconnect({"mode": "walletconnect"}))
        assert "isError" not in out
        assert out["details"]["mode"] == "walletconnect"
        assert out["details"]["pair_uri"] == "wc:abc@2"
        body = out["content"][0]["text"]
        assert "wc:abc@2" in body

    def test_walletconnect_no_uri_returned(self, fake_svc):
        fake_svc.connect_walletconnect.return_value = WalletState(
            connected=False,
            mode="walletconnect",
            balances={},
        )
        out = json.loads(clawnchconnect({"mode": "walletconnect"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "bridge_error"

    def test_walletconnect_config_error(self, fake_svc):
        from clawmes.services.wallet import WalletConfigError

        fake_svc.connect_walletconnect.side_effect = WalletConfigError(
            "WalletConnect bridge unavailable"
        )
        out = json.loads(clawnchconnect({"mode": "walletconnect"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "wallet_config_error"

    def test_walletconnect_missing_project_id(self, fake_svc):
        from clawmes.bridges.process import BridgeError

        fake_svc.connect_walletconnect.side_effect = BridgeError(
            "config_error", "WALLETCONNECT_PROJECT_ID not set"
        )
        out = json.loads(clawnchconnect({"mode": "walletconnect"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "config_error"
        assert "cloud.walletconnect.com" in out["content"][0]["text"]

    def test_walletconnect_other_bridge_error(self, fake_svc):
        from clawmes.bridges.process import BridgeError

        fake_svc.connect_walletconnect.side_effect = BridgeError("network", "relay unreachable")
        out = json.loads(clawnchconnect({"mode": "walletconnect"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "bridge_error"


class TestBankr:
    @pytest.fixture
    def fake_svc(self, monkeypatch):
        from clawmes.services import wallet as wallet_mod

        monkeypatch.setattr("clawmes.tools.clawnchconnect.get_wallet_state", _disconnected)
        svc = MagicMock()
        monkeypatch.setattr(wallet_mod, "get_wallet_service", lambda: svc)
        return svc

    def test_bankr_success(self, fake_svc):
        fake_svc.connect_bankr.return_value = _connected_bankr()
        out = json.loads(clawnchconnect({"mode": "bankr"}))
        assert "isError" not in out
        assert out["details"]["mode"] == "bankr"
        assert out["details"]["address"] == "0x" + "b" * 40

    def test_bankr_no_credentials(self, fake_svc):
        from clawmes.services.bankr_service import BankrError

        fake_svc.connect_bankr.side_effect = BankrError("no_credentials", "BANKR_API_KEY not set")
        out = json.loads(clawnchconnect({"mode": "bankr"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "no_credentials"
        assert "bankr.bot" in out["content"][0]["text"]

    def test_bankr_other_error(self, fake_svc):
        from clawmes.services.bankr_service import BankrError

        fake_svc.connect_bankr.side_effect = BankrError("network", "relay down")
        out = json.loads(clawnchconnect({"mode": "bankr"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "network"


class TestLocal:
    def test_local_directs_to_slash_command(self, monkeypatch):
        monkeypatch.setattr("clawmes.tools.clawnchconnect.get_wallet_state", _disconnected)
        out = json.loads(clawnchconnect({"mode": "local"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "interactive_required"
        assert "/connect_local" in out["content"][0]["text"]


class TestRegister:
    def test_register_calls_ctx(self):
        from clawmes.tools import clawnchconnect as cc_mod

        recorded = []

        class FakeCtx:
            def register_tool(self, **kw):
                recorded.append(kw)

        cc_mod.register(FakeCtx())
        assert len(recorded) == 1
        assert recorded[0]["name"] == "clawnchconnect"
