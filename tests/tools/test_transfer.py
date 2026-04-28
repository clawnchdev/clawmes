"""Tests for the ``transfer`` tool."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from clawmes.tools.transfer import transfer
from clawmes.wallet.state import WalletState


@pytest.fixture(autouse=True)
def _isolate_wallet(monkeypatch):
    """Reset wallet + RPC singletons each test."""
    from clawmes.services import rpc as rpc_mod
    from clawmes.services import wallet as wallet_mod

    monkeypatch.setattr(wallet_mod, "_instance", None)
    monkeypatch.setattr(rpc_mod, "_instance", None)


@pytest.fixture
def connected_wallet(monkeypatch):
    """Patch get_wallet_state to return a connected Base wallet."""
    connected = WalletState.for_chain(
        mode="walletconnect",
        address="0x" + "a" * 40,
        chain_id=8453,
    )
    monkeypatch.setattr("clawmes.tools.transfer.get_wallet_state", lambda: connected)
    return connected


@pytest.fixture
def fake_mode(monkeypatch):
    """Install a fake active wallet mode that returns a canned tx hash."""
    from clawmes.services import wallet as wallet_mod

    mode = MagicMock()
    mode.send_transaction.return_value = "0x" + "f" * 64
    svc = MagicMock()
    svc.active_mode = mode
    monkeypatch.setattr(wallet_mod, "get_wallet_service", lambda: svc)
    return mode


@pytest.fixture
def fake_rpc(monkeypatch):
    """Install a fake RPC service whose wait_for_receipt returns a success
    receipt by default."""
    from clawmes.services import rpc as rpc_mod

    rpc = MagicMock()
    rpc.wait_for_receipt.return_value = {
        "status": "0x1",
        "blockNumber": "0x123",
        "gasUsed": "0x5208",  # 21000
    }
    monkeypatch.setattr(rpc_mod, "get_rpc_service", lambda: rpc)
    return rpc


class TestNoWallet:
    def test_send_no_wallet(self):
        out = json.loads(transfer({"action": "send", "to": "0xdead", "amount": "1"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "wallet_not_connected"

    def test_estimate_no_wallet(self):
        out = json.loads(transfer({"action": "estimate", "to": "0xdead", "amount": "1"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "wallet_not_connected"


class TestEstimate:
    def test_native_estimate_basic(self, connected_wallet):
        out = json.loads(transfer({"action": "estimate", "to": "0x" + "1" * 40, "amount": "0.5"}))
        assert "isError" not in out
        details = out["details"]
        assert details["chain_id"] == 8453
        assert details["chain"] == "Base"
        assert details["estimated_gas"] == 21000
        assert details["value_wei"] == str(5 * 10**17)
        assert details["token"] == "native"
        # Summary is a multi-line string; first line names the asset+chain
        body = out["content"][0]["text"]
        assert "Base" in body
        assert "0.5" in body

    def test_estimate_rejects_token(self, connected_wallet):
        out = json.loads(
            transfer(
                {
                    "action": "estimate",
                    "to": "0x" + "1" * 40,
                    "amount": "1",
                    "token": "0x" + "2" * 40,
                }
            )
        )
        assert out["isError"] is True
        assert out["details"]["error_code"] == "not_implemented"

    def test_estimate_unknown_chain(self, monkeypatch, connected_wallet):
        out = json.loads(
            transfer(
                {
                    "action": "estimate",
                    "to": "0x" + "1" * 40,
                    "amount": "1",
                    "chain_id": 999_999,
                }
            )
        )
        assert out["isError"] is True
        assert out["details"]["error_code"] == "unsupported_chain"

    def test_estimate_rejects_negative_amount(self, connected_wallet):
        out = json.loads(transfer({"action": "estimate", "to": "0x" + "1" * 40, "amount": "-1"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "param_error"

    def test_estimate_chain_id_string(self, connected_wallet):
        # LLMs sometimes pass chain_id as a string; we coerce.
        out = json.loads(
            transfer(
                {
                    "action": "estimate",
                    "to": "0x" + "1" * 40,
                    "amount": "1",
                    "chain_id": "1",
                }
            )
        )
        assert "isError" not in out
        assert out["details"]["chain_id"] == 1
        assert out["details"]["chain"] == "Ethereum Mainnet"

    def test_estimate_bad_chain_id_falls_back(self, connected_wallet):
        # Garbage chain_id falls back to wallet's chain (8453).
        out = json.loads(
            transfer(
                {
                    "action": "estimate",
                    "to": "0x" + "1" * 40,
                    "amount": "1",
                    "chain_id": "not-a-number",
                }
            )
        )
        assert "isError" not in out
        assert out["details"]["chain_id"] == 8453


class TestSendNative:
    def test_send_success_with_receipt(self, connected_wallet, fake_mode, fake_rpc):
        out = json.loads(transfer({"action": "send", "to": "0x" + "1" * 40, "amount": "0.01"}))
        assert "isError" not in out
        details = out["details"]
        assert details["tx_hash"] == "0x" + "f" * 64
        assert details["status"] == "success"
        assert details["block_number"] == 0x123
        assert details["gas_used"] == 21000
        assert details["explorer_url"].endswith("/tx/0x" + "f" * 64)
        # The mode received the right args
        kwargs = fake_mode.send_transaction.call_args.kwargs
        assert kwargs["to"] == "0x" + "1" * 40
        assert kwargs["value"] == 10**16
        assert kwargs["chain_id"] == 8453

    def test_send_skip_receipt(self, connected_wallet, fake_mode, fake_rpc):
        out = json.loads(
            transfer(
                {
                    "action": "send",
                    "to": "0x" + "1" * 40,
                    "amount": "0.01",
                    "await_receipt": False,
                }
            )
        )
        assert "isError" not in out
        assert out["details"]["status"] == "pending"
        # wait_for_receipt was not called
        fake_rpc.wait_for_receipt.assert_not_called()
        body = out["content"][0]["text"]
        assert "receipt polling skipped" in body

    def test_send_receipt_timeout_returns_pending(self, connected_wallet, fake_mode, fake_rpc):
        from clawmes.services.rpc import RpcError

        fake_rpc.wait_for_receipt.side_effect = RpcError(
            -32000, "timed out after 120s", method="eth_getTransactionReceipt"
        )
        out = json.loads(transfer({"action": "send", "to": "0x" + "1" * 40, "amount": "0.01"}))
        # Timeout is NOT a tool error — the tx may still mine.
        assert "isError" not in out
        assert out["details"]["status"] == "pending"
        body = out["content"][0]["text"]
        assert "Receipt not seen" in body

    def test_send_reverted(self, connected_wallet, fake_mode, fake_rpc):
        fake_rpc.wait_for_receipt.return_value = {
            "status": "0x0",
            "blockNumber": 100,
            "gasUsed": 21000,
        }
        out = json.loads(transfer({"action": "send", "to": "0x" + "1" * 40, "amount": "0.01"}))
        assert "isError" not in out
        assert out["details"]["status"] == "reverted"
        body = out["content"][0]["text"]
        assert "Reverted" in body

    def test_send_pre_byzantium_root_treated_success(self, connected_wallet, fake_mode, fake_rpc):
        # No `status` field; legacy `root`-style receipt — success.
        fake_rpc.wait_for_receipt.return_value = {
            "root": "0x" + "a" * 64,
            "blockNumber": "0x10",
            "gasUsed": "0x5208",
        }
        out = json.loads(transfer({"action": "send", "to": "0x" + "1" * 40, "amount": "0.01"}))
        assert out["details"]["status"] == "success"

    def test_send_no_active_mode(self, connected_wallet, monkeypatch):
        from clawmes.services import wallet as wallet_mod

        svc = MagicMock()
        svc.active_mode = None
        monkeypatch.setattr(wallet_mod, "get_wallet_service", lambda: svc)
        out = json.loads(transfer({"action": "send", "to": "0x" + "1" * 40, "amount": "0.01"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "wallet_not_connected"

    def test_send_mode_raises(self, connected_wallet, fake_mode):
        fake_mode.send_transaction.side_effect = RuntimeError("nonce conflict")
        out = json.loads(transfer({"action": "send", "to": "0x" + "1" * 40, "amount": "0.01"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "send_failed"
        assert "nonce conflict" in out["content"][0]["text"]

    def test_send_rejects_token(self, connected_wallet, fake_mode):
        out = json.loads(
            transfer(
                {
                    "action": "send",
                    "to": "0x" + "1" * 40,
                    "amount": "1",
                    "token": "0x" + "2" * 40,
                }
            )
        )
        assert out["isError"] is True
        assert out["details"]["error_code"] == "not_implemented"

    def test_send_unknown_chain(self, connected_wallet, fake_mode):
        out = json.loads(
            transfer(
                {
                    "action": "send",
                    "to": "0x" + "1" * 40,
                    "amount": "1",
                    "chain_id": 999_999,
                }
            )
        )
        assert out["isError"] is True
        assert out["details"]["error_code"] == "unsupported_chain"

    def test_send_rejects_negative_amount(self, connected_wallet, fake_mode):
        out = json.loads(transfer({"action": "send", "to": "0x" + "1" * 40, "amount": "-1"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "param_error"


class TestUnknownAction:
    def test_unknown_action(self, connected_wallet):
        out = json.loads(transfer({"action": "drain", "to": "0x" + "1" * 40, "amount": "1"}))
        assert out["isError"] is True
        # `drain` is a valid string for read_str; fails our own dispatcher
        assert out["details"]["error_code"] == "invalid_action"


class TestEns:
    def test_invalid_address_length(self, connected_wallet):
        out = json.loads(transfer({"action": "estimate", "to": "0xabc", "amount": "1"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "param_error"

    def test_invalid_address_garbled(self, connected_wallet):
        out = json.loads(transfer({"action": "estimate", "to": "0x" + "z" * 40, "amount": "1"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "param_error"

    def test_no_dot_no_0x_rejected(self, connected_wallet):
        out = json.loads(transfer({"action": "estimate", "to": "vitalik", "amount": "1"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "param_error"

    def test_estimate_resolves_ens(self, connected_wallet, monkeypatch):
        from clawmes.tools import transfer as transfer_mod

        monkeypatch.setattr(transfer_mod, "resolve_ens", lambda name: "0x" + "a" * 40)
        out = json.loads(transfer({"action": "estimate", "to": "vitalik.eth", "amount": "0.1"}))
        assert "isError" not in out
        details = out["details"]
        assert details["to"] == "0x" + "a" * 40
        assert details["ens_name"] == "vitalik.eth"
        assert details["resolved_address"] == "0x" + "a" * 40
        body = out["content"][0]["text"]
        assert "vitalik.eth" in body
        assert "0x" + "a" * 40 in body

    def test_estimate_ens_not_registered(self, connected_wallet, monkeypatch):
        from clawmes.lib.ens import EnsError
        from clawmes.tools import transfer as transfer_mod

        def boom(name):
            raise EnsError("not_registered", "no resolver")

        monkeypatch.setattr(transfer_mod, "resolve_ens", boom)
        out = json.loads(transfer({"action": "estimate", "to": "ghost.eth", "amount": "1"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "ens_not_registered"

    def test_send_resolves_ens(self, connected_wallet, fake_mode, fake_rpc, monkeypatch):
        from clawmes.tools import transfer as transfer_mod

        monkeypatch.setattr(transfer_mod, "resolve_ens", lambda name: "0x" + "b" * 40)
        out = json.loads(transfer({"action": "send", "to": "alice.eth", "amount": "0.01"}))
        assert "isError" not in out
        details = out["details"]
        assert details["to"] == "0x" + "b" * 40
        assert details["ens_name"] == "alice.eth"
        assert details["resolved_address"] == "0x" + "b" * 40
        # The mode received the resolved address, not the ENS name
        kwargs = fake_mode.send_transaction.call_args.kwargs
        assert kwargs["to"] == "0x" + "b" * 40

    def test_send_ens_no_address(self, connected_wallet, fake_mode, fake_rpc, monkeypatch):
        from clawmes.lib.ens import EnsError
        from clawmes.tools import transfer as transfer_mod

        def boom(name):
            raise EnsError("no_address", "registered without addr")

        monkeypatch.setattr(transfer_mod, "resolve_ens", boom)
        out = json.loads(transfer({"action": "send", "to": "empty.eth", "amount": "0.01"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "ens_no_address"
        # Mode was never called — we bailed before signing
        fake_mode.send_transaction.assert_not_called()


class TestRecipientHelper:
    def test_empty_input(self):
        from clawmes.tools.transfer import _resolve_recipient

        result = _resolve_recipient("")
        assert isinstance(result, str)
        out = json.loads(result)
        assert out["details"]["error_code"] == "param_error"


class TestTargetChainFallback:
    def test_state_chain_id_none_defaults_8453(self, monkeypatch, fake_mode, fake_rpc):
        # Connected but no chain_id on the state — fall back to Base.
        connected = WalletState(
            connected=True,
            mode="walletconnect",
            address="0x" + "a" * 40,
            chain_id=None,
        )
        monkeypatch.setattr("clawmes.tools.transfer.get_wallet_state", lambda: connected)
        out = json.loads(transfer({"action": "send", "to": "0x" + "1" * 40, "amount": "0.01"}))
        assert "isError" not in out
        assert out["details"]["chain_id"] == 8453


class TestReceiptHelpers:
    def test_summarize_receipt_int_fields(self):
        from clawmes.tools.transfer import _summarize_receipt

        success, block, gas = _summarize_receipt(
            {"status": 1, "blockNumber": 100, "gasUsed": 21000}
        )
        assert success is True
        assert block == 100
        assert gas == 21000

    def test_hex_or_int_decimal_string(self):
        from clawmes.tools.transfer import _hex_or_int

        assert _hex_or_int("100") == 100
        assert _hex_or_int("0x10") == 16
        assert _hex_or_int(42) == 42
        assert _hex_or_int(None) == 0


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
