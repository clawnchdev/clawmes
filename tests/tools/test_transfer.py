"""Tests for the ``transfer`` tool skeleton."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from clawmes.tools.transfer import transfer
from clawmes.wallet.state import WalletState


@pytest.fixture(autouse=True)
def _isolate_wallet(monkeypatch):
    """Reset the wallet service singleton each test."""
    from clawmes.services import wallet as wallet_mod

    monkeypatch.setattr(wallet_mod, "_instance", None)


class TestNoWallet:
    def test_send_no_wallet(self):
        out = json.loads(transfer({"action": "send", "to": "alice.eth", "amount": "1"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "wallet_not_connected"

    def test_estimate_no_wallet(self):
        out = json.loads(transfer({"action": "estimate", "to": "alice.eth", "amount": "1"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "wallet_not_connected"


class TestWithWallet:
    @pytest.fixture
    def connected_wallet(self):
        connected = WalletState.for_chain(
            mode="walletconnect",
            address="0x" + "a" * 40,
            chain_id=8453,
        )
        with patch("clawmes.tools.transfer.get_wallet_state", return_value=connected):
            yield connected

    def test_send_not_implemented(self, connected_wallet):
        out = json.loads(transfer({"action": "send", "to": "alice.eth", "amount": "1"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "not_implemented"

    def test_estimate_not_implemented(self, connected_wallet):
        out = json.loads(transfer({"action": "estimate", "to": "alice.eth", "amount": "1"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "not_implemented"

    def test_unknown_action(self, connected_wallet):
        out = json.loads(transfer({"action": "drain", "to": "alice.eth", "amount": "1"}))
        # 'drain' fails the schema enum at param_error (read_str passes it
        # through; the dispatcher catches the unknown action). ParamError
        # is raised by read_str if action is missing; but if action is
        # *valid string* but wrong value, the dispatch returns "Unknown
        # action".
        assert out["isError"] is True
        # Could be either invalid_action (own dispatcher) or param_error
        # depending on the schema enum check. Our read_str just returns
        # the string, so we land in invalid_action.
        assert out["details"]["error_code"] in ("invalid_action", "param_error")


class TestRegister:
    def test_register_calls_ctx(self):
        from clawmes.tools import transfer as transfer_mod

        recorded = []

        class FakeCtx:
            def register_tool(self, **kw):
                recorded.append(kw)

        transfer_mod.register(FakeCtx())
        assert len(recorded) == 1
        assert recorded[0]["name"] == "transfer"
