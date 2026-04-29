"""Tests for the ``approvals`` tool."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from clawmes.tools.approvals import approvals
from clawmes.wallet.state import WalletState

OWNER = "0x" + "a" * 40
TOKEN = "0x" + "b" * 40
SPENDER = "0x" + "c" * 40


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    from clawmes.policy import storage as policy_storage
    from clawmes.services import explorer as explorer_mod
    from clawmes.services import rpc as rpc_mod
    from clawmes.services import token_decimals as td_mod
    from clawmes.services import wallet as wallet_mod

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(wallet_mod, "_instance", None)
    monkeypatch.setattr(rpc_mod, "_instance", None)
    monkeypatch.setattr(explorer_mod, "_instance", None)
    monkeypatch.setattr(td_mod, "_instance", None)
    policy_storage.save_policies([])


@pytest.fixture
def connected(monkeypatch):
    state = WalletState.for_chain(mode="local", address=OWNER, chain_id=8453)
    monkeypatch.setattr("clawmes.tools.approvals.get_wallet_state", lambda: state)
    return state


@pytest.fixture
def fake_explorer(monkeypatch):
    from clawmes.services import explorer as explorer_mod

    svc = MagicMock()
    svc.get_logs.return_value = []
    monkeypatch.setattr(explorer_mod, "_instance", svc)
    return svc


@pytest.fixture
def fake_rpc(monkeypatch):
    from clawmes.services import rpc as rpc_mod

    svc = MagicMock()
    svc.eth_call.return_value = "0x" + "0" * 64  # zero allowance default
    monkeypatch.setattr(rpc_mod, "_instance", svc)
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
def fake_decimals(monkeypatch):
    from clawmes.services import token_decimals as td_mod

    svc = MagicMock()
    svc.get_strict.return_value = 6  # USDC
    monkeypatch.setattr(td_mod, "_instance", svc)
    return svc


class TestNoWallet:
    def test_no_wallet(self, monkeypatch):
        monkeypatch.setattr(
            "clawmes.tools.approvals.get_wallet_state",
            lambda: WalletState.disconnected(),
        )
        out = json.loads(approvals({"action": "list"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "wallet_not_connected"


class TestList:
    def test_empty(self, connected, fake_explorer, fake_rpc):
        out = json.loads(approvals({"action": "list"}))
        assert "isError" not in out
        assert out["details"]["count"] == 0
        body = out["content"][0]["text"]
        assert "No active approvals" in body

    def test_explorer_failure(self, connected, fake_explorer, fake_rpc):
        fake_explorer.get_logs.side_effect = RuntimeError("rate limit")
        out = json.loads(approvals({"action": "list"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "explorer_error"

    def test_active_approval_shown(self, connected, fake_explorer, fake_rpc):
        # Topic encoding: indexed address args are left-padded to 32 bytes
        spender_topic = "0x" + "0" * 24 + "c" * 40
        fake_explorer.get_logs.return_value = [
            {
                "address": TOKEN,
                "topics": [
                    "0x" + "0" * 64,  # event sig (don't care for parser)
                    "0x" + "0" * 24 + "a" * 40,  # owner
                    spender_topic,
                ],
                "blockNumber": "0x12345",
            }
        ]
        # eth_call returns a non-zero allowance: 100 USDC base units
        fake_rpc.eth_call.return_value = "0x" + format(100 * 10**6, "064x")

        out = json.loads(approvals({"action": "list"}))
        assert "isError" not in out
        assert out["details"]["count"] == 1
        approval = out["details"]["approvals"][0]
        assert approval["token"] == TOKEN
        assert approval["spender"] == SPENDER
        assert approval["current_allowance"] == str(100 * 10**6)
        assert approval["is_unlimited"] is False
        assert approval["risk_level"] == "ok"

    def test_unlimited_approval_flagged(self, connected, fake_explorer, fake_rpc):
        fake_explorer.get_logs.return_value = [
            {
                "address": TOKEN,
                "topics": [
                    "0x" + "0" * 64,
                    "0x" + "0" * 24 + "a" * 40,
                    "0x" + "0" * 24 + "c" * 40,
                ],
                "blockNumber": "0x100",
            }
        ]
        # uint256 max = unlimited
        fake_rpc.eth_call.return_value = "0x" + "f" * 64
        out = json.loads(approvals({"action": "list"}))
        assert out["details"]["approvals"][0]["is_unlimited"] is True
        assert out["details"]["approvals"][0]["risk_level"] == "high"

    def test_zeroed_allowance_skipped(self, connected, fake_explorer, fake_rpc):
        fake_explorer.get_logs.return_value = [
            {
                "address": TOKEN,
                "topics": [
                    "0x" + "0" * 64,
                    "0x" + "0" * 24 + "a" * 40,
                    "0x" + "0" * 24 + "c" * 40,
                ],
                "blockNumber": "0x100",
            }
        ]
        fake_rpc.eth_call.return_value = "0x" + "0" * 64  # revoked / used up
        out = json.loads(approvals({"action": "list"}))
        # Revoked approvals don't appear in the output
        assert out["details"]["count"] == 0

    def test_dedupes_repeated_approvals(self, connected, fake_explorer, fake_rpc):
        fake_explorer.get_logs.return_value = [
            {
                "address": TOKEN,
                "topics": [
                    "0x" + "0" * 64,
                    "0x" + "0" * 24 + "a" * 40,
                    "0x" + "0" * 24 + "c" * 40,
                ],
                "blockNumber": "0x100",
            },
            {
                "address": TOKEN,
                "topics": [
                    "0x" + "0" * 64,
                    "0x" + "0" * 24 + "a" * 40,
                    "0x" + "0" * 24 + "c" * 40,
                ],
                "blockNumber": "0x200",
            },
        ]
        fake_rpc.eth_call.return_value = "0x" + format(50 * 10**6, "064x")
        out = json.loads(approvals({"action": "list"}))
        # Two logs for same (token, spender) → one entry; latest block wins
        assert out["details"]["count"] == 1
        assert out["details"]["approvals"][0]["last_set_block"] == 0x200

    def test_skips_malformed_logs(self, connected, fake_explorer, fake_rpc):
        fake_explorer.get_logs.return_value = [
            {"address": "", "topics": []},  # missing fields
            {"address": TOKEN, "topics": ["only-one-topic"]},
            {  # this one is valid
                "address": TOKEN,
                "topics": [
                    "0x" + "0" * 64,
                    "0x" + "0" * 24 + "a" * 40,
                    "0x" + "0" * 24 + "c" * 40,
                ],
                "blockNumber": "0x100",
            },
        ]
        fake_rpc.eth_call.return_value = "0x" + format(1, "064x")
        out = json.loads(approvals({"action": "list"}))
        assert out["details"]["count"] == 1

    def test_handles_non_hex_block_number(self, connected, fake_explorer, fake_rpc):
        fake_explorer.get_logs.return_value = [
            {
                "address": TOKEN,
                "topics": [
                    "0x" + "0" * 64,
                    "0x" + "0" * 24 + "a" * 40,
                    "0x" + "0" * 24 + "c" * 40,
                ],
                "blockNumber": "garbage",  # parse failure → 0
            }
        ]
        fake_rpc.eth_call.return_value = "0x" + format(1, "064x")
        out = json.loads(approvals({"action": "list"}))
        assert out["details"]["approvals"][0]["last_set_block"] == 0

    def test_allowance_lookup_failure_drops_entry(self, connected, fake_explorer, fake_rpc):
        from clawmes.services.rpc import RpcError

        fake_explorer.get_logs.return_value = [
            {
                "address": TOKEN,
                "topics": [
                    "0x" + "0" * 64,
                    "0x" + "0" * 24 + "a" * 40,
                    "0x" + "0" * 24 + "c" * 40,
                ],
                "blockNumber": "0x100",
            }
        ]
        fake_rpc.eth_call.side_effect = RpcError(-32000, "boom", method="eth_call")
        out = json.loads(approvals({"action": "list"}))
        # Lookup failure → treat as zero → entry dropped
        assert out["details"]["count"] == 0


class TestAudit:
    def test_audit_flags_unlimited(self, connected, fake_explorer, fake_rpc):
        fake_explorer.get_logs.return_value = [
            {
                "address": TOKEN,
                "topics": [
                    "0x" + "0" * 64,
                    "0x" + "0" * 24 + "a" * 40,
                    "0x" + "0" * 24 + "c" * 40,
                ],
                "blockNumber": "0x100",
            }
        ]
        fake_rpc.eth_call.return_value = "0x" + "f" * 64
        out = json.loads(approvals({"action": "audit"}))
        assert "isError" not in out
        assert out["details"]["flagged"] == 1
        assert "UNLIMITED" in out["content"][0]["text"]

    def test_audit_clean_state(self, connected, fake_explorer, fake_rpc):
        out = json.loads(approvals({"action": "audit"}))
        assert "isError" not in out
        assert out["details"]["flagged"] == 0

    def test_audit_explorer_failure(self, connected, fake_explorer, fake_rpc):
        fake_explorer.get_logs.side_effect = RuntimeError("network")
        out = json.loads(approvals({"action": "audit"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "explorer_error"


class TestApprove:
    def test_approve_success(self, connected, fake_explorer, fake_rpc, fake_mode, fake_decimals):
        out = json.loads(
            approvals(
                {
                    "action": "approve",
                    "token": TOKEN,
                    "spender": SPENDER,
                    "amount": "100",
                }
            )
        )
        assert "isError" not in out
        details = out["details"]
        assert details["tx_hash"] == "0x" + "f" * 64
        assert details["is_revoke"] is False
        # Mode received the right calldata
        kwargs = fake_mode.send_transaction.call_args.kwargs
        assert kwargs["to"] == TOKEN
        assert kwargs["value"] == 0
        assert kwargs["gas"] == 80_000
        assert kwargs["data"].startswith("0x095ea7b3")

    def test_approve_unlimited(self, connected, fake_explorer, fake_rpc, fake_mode, fake_decimals):
        out = json.loads(
            approvals(
                {
                    "action": "approve",
                    "token": TOKEN,
                    "spender": SPENDER,
                    "amount": "unlimited",
                }
            )
        )
        assert "isError" not in out
        # Encoded amount is uint256 max
        kwargs = fake_mode.send_transaction.call_args.kwargs
        assert "f" * 64 in kwargs["data"].lower()

    def test_approve_invalid_token(self, connected, fake_explorer, fake_rpc, fake_mode):
        out = json.loads(
            approvals(
                {
                    "action": "approve",
                    "token": "0xshort",
                    "spender": SPENDER,
                    "amount": "1",
                }
            )
        )
        assert out["isError"] is True
        assert out["details"]["error_code"] == "param_error"

    def test_approve_invalid_spender(self, connected, fake_explorer, fake_rpc, fake_mode):
        out = json.loads(
            approvals(
                {
                    "action": "approve",
                    "token": TOKEN,
                    "spender": "not-an-addr",
                    "amount": "1",
                }
            )
        )
        assert out["isError"] is True
        assert out["details"]["error_code"] == "param_error"

    def test_approve_decimals_lookup_failed(
        self, connected, fake_explorer, fake_rpc, fake_mode, monkeypatch
    ):
        from clawmes.services import token_decimals as td_mod
        from clawmes.services.rpc import RpcError
        from clawmes.services.token_decimals import TokenDecimalsError

        td = MagicMock()
        td.get_strict.side_effect = TokenDecimalsError(
            TOKEN, 8453, RpcError(-32000, "no node", method="eth_call")
        )
        monkeypatch.setattr(td_mod, "_instance", td)
        out = json.loads(
            approvals(
                {
                    "action": "approve",
                    "token": TOKEN,
                    "spender": SPENDER,
                    "amount": "100",
                }
            )
        )
        assert out["isError"] is True
        assert out["details"]["error_code"] == "param_error"

    def test_approve_no_active_mode(
        self, connected, fake_explorer, fake_rpc, fake_decimals, monkeypatch
    ):
        from clawmes.services import wallet as wallet_mod

        svc = MagicMock()
        svc.active_mode = None
        monkeypatch.setattr(wallet_mod, "get_wallet_service", lambda: svc)
        out = json.loads(
            approvals(
                {
                    "action": "approve",
                    "token": TOKEN,
                    "spender": SPENDER,
                    "amount": "100",
                }
            )
        )
        assert out["isError"] is True
        assert out["details"]["error_code"] == "wallet_not_connected"

    def test_approve_send_failure(
        self, connected, fake_explorer, fake_rpc, fake_mode, fake_decimals
    ):
        fake_mode.send_transaction.side_effect = RuntimeError("user rejected")
        out = json.loads(
            approvals(
                {
                    "action": "approve",
                    "token": TOKEN,
                    "spender": SPENDER,
                    "amount": "1",
                }
            )
        )
        assert out["isError"] is True
        assert out["details"]["error_code"] == "send_failed"


class TestRevoke:
    def test_revoke_success(self, connected, fake_explorer, fake_rpc, fake_mode):
        out = json.loads(approvals({"action": "revoke", "token": TOKEN, "spender": SPENDER}))
        assert "isError" not in out
        details = out["details"]
        assert details["is_revoke"] is True
        assert details["amount"] == "0"
        # Encoded calldata sets allowance to 0
        kwargs = fake_mode.send_transaction.call_args.kwargs
        # The amount field in calldata is at the end — last 64 chars all zero
        assert kwargs["data"].endswith("0" * 64)

    def test_revoke_invalid_token(self, connected, fake_explorer, fake_rpc):
        out = json.loads(approvals({"action": "revoke", "token": "0xshort", "spender": SPENDER}))
        assert out["isError"] is True

    def test_revoke_invalid_spender(self, connected, fake_explorer, fake_rpc):
        out = json.loads(approvals({"action": "revoke", "token": TOKEN, "spender": "bogus"}))
        assert out["isError"] is True


class TestChainResolution:
    def test_explicit_chain_id(self, connected, fake_explorer, fake_rpc, fake_mode, fake_decimals):
        out = json.loads(
            approvals(
                {
                    "action": "approve",
                    "token": TOKEN,
                    "spender": SPENDER,
                    "amount": "1",
                    "chain_id": 1,
                }
            )
        )
        assert "isError" not in out
        kwargs = fake_mode.send_transaction.call_args.kwargs
        assert kwargs["chain_id"] == 1

    def test_chain_id_falls_back_to_8453_when_none(
        self, monkeypatch, fake_explorer, fake_rpc, fake_mode, fake_decimals
    ):
        # State is connected but chain_id is None
        state = WalletState(connected=True, mode="local", address=OWNER, chain_id=None)
        monkeypatch.setattr("clawmes.tools.approvals.get_wallet_state", lambda: state)
        out = json.loads(
            approvals(
                {
                    "action": "approve",
                    "token": TOKEN,
                    "spender": SPENDER,
                    "amount": "1",
                }
            )
        )
        assert "isError" not in out
        kwargs = fake_mode.send_transaction.call_args.kwargs
        assert kwargs["chain_id"] == 8453

    def test_from_block_passed_through(self, connected, fake_explorer, fake_rpc):
        approvals({"action": "list", "from_block": 1000})
        kwargs = fake_explorer.get_logs.call_args.kwargs
        assert kwargs["from_block"] == 1000

    def test_from_block_default_is_zero(self, connected, fake_explorer, fake_rpc):
        approvals({"action": "list"})
        kwargs = fake_explorer.get_logs.call_args.kwargs
        assert kwargs["from_block"] == 0


class TestRegister:
    def test_register_calls_ctx(self):
        from clawmes.tools import approvals as approvals_mod

        recorded = []

        class FakeCtx:
            def register_tool(self, **kw):
                recorded.append(kw)

        approvals_mod.register(FakeCtx())
        assert len(recorded) == 1
        assert recorded[0]["name"] == "approvals"
