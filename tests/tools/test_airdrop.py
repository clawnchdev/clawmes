"""Tests for the ``airdrop`` tool."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from clawmes.tools.airdrop import airdrop
from clawmes.wallet.state import WalletState

OWNER = "0x" + "a" * 40
DISTRIBUTOR = "0x" + "b" * 40


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    from clawmes.policy import storage as policy_storage
    from clawmes.services import wallet as wallet_mod

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(wallet_mod, "_instance", None)
    policy_storage.save_policies([])


@pytest.fixture
def connected(monkeypatch):
    state = WalletState.for_chain(mode="local", address=OWNER, chain_id=1)
    monkeypatch.setattr("clawmes.tools.airdrop.get_wallet_state", lambda: state)
    return state


@pytest.fixture
def fake_http(monkeypatch):
    class FakeHttp:
        def __init__(self):
            self.calls: list[dict] = []
            self.responses: list = []

        def __call__(self, url, *, params=None, headers=None, timeout=30.0, **kw):
            self.calls.append({"url": url})
            if not self.responses:
                raise AssertionError("no fake response queued")
            response = self.responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response

    fake = FakeHttp()
    monkeypatch.setattr("clawmes.tools.airdrop.http_get", fake)
    return fake


@pytest.fixture
def fake_mode(monkeypatch):
    from clawmes.services import wallet as wallet_mod

    mode = MagicMock()
    mode.send_transaction.return_value = "0x" + "f" * 64
    svc = MagicMock()
    svc.active_mode = mode
    monkeypatch.setattr(wallet_mod, "get_wallet_service", lambda: svc)
    return mode


class TestList:
    def test_not_implemented(self):
        out = json.loads(airdrop({"action": "list"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "not_implemented"


class TestEligibility:
    def test_no_wallet(self, monkeypatch):
        monkeypatch.setattr(
            "clawmes.tools.airdrop.get_wallet_state",
            lambda: WalletState.disconnected(),
        )
        out = json.loads(
            airdrop(
                {
                    "action": "eligibility",
                    "endpoint": "https://drop.example.com/api",
                }
            )
        )
        assert out["isError"] is True

    def test_basic(self, connected, fake_http):
        fake_http.responses.append({"eligible": True, "amount": "100"})
        out = json.loads(
            airdrop(
                {
                    "action": "eligibility",
                    "endpoint": "https://drop.example.com/api",
                }
            )
        )
        assert "isError" not in out
        # URL was augmented with ?address
        assert "address=" in fake_http.calls[0]["url"]

    def test_reject_http_endpoint(self, connected, fake_http):
        out = json.loads(
            airdrop(
                {
                    "action": "eligibility",
                    "endpoint": "http://insecure.example.com/api",
                }
            )
        )
        assert out["isError"] is True
        assert out["details"]["error_code"] == "param_error"

    def test_endpoint_with_existing_query(self, connected, fake_http):
        fake_http.responses.append({"eligible": False})
        airdrop(
            {
                "action": "eligibility",
                "endpoint": "https://drop.example.com/api?network=ethereum",
            }
        )
        # The address is NOT auto-appended when ? is already present;
        # the user is expected to format the URL fully
        assert "address=" not in fake_http.calls[0]["url"]

    def test_api_error(self, connected, fake_http):
        fake_http.responses.append(RuntimeError("network"))
        out = json.loads(
            airdrop(
                {
                    "action": "eligibility",
                    "endpoint": "https://drop.example.com/api",
                }
            )
        )
        assert out["isError"] is True
        assert out["details"]["error_code"] == "api_error"


class TestClaim:
    def test_basic(self, connected, fake_mode):
        out = json.loads(
            airdrop(
                {
                    "action": "claim",
                    "distributor": DISTRIBUTOR,
                    "index": 42,
                    "amount": "1000",
                    "proof": ["0x" + "1" * 64, "0x" + "2" * 64],
                }
            )
        )
        assert "isError" not in out
        kwargs = fake_mode.send_transaction.call_args.kwargs
        # claim() selector
        assert kwargs["data"].startswith("0x2e7ba6ef")

    def test_no_proof(self, connected, fake_mode):
        out = json.loads(
            airdrop(
                {
                    "action": "claim",
                    "distributor": DISTRIBUTOR,
                    "index": 1,
                    "amount": "100",
                }
            )
        )
        # proof defaults to [] so claim attempts with empty proof —
        # the calldata still encodes correctly
        assert "isError" not in out

    def test_invalid_distributor(self, connected, fake_mode):
        out = json.loads(
            airdrop(
                {
                    "action": "claim",
                    "distributor": "0xshort",
                    "index": 1,
                    "amount": "100",
                    "proof": [],
                }
            )
        )
        assert out["isError"] is True

    def test_missing_index(self, connected, fake_mode):
        out = json.loads(
            airdrop(
                {
                    "action": "claim",
                    "distributor": DISTRIBUTOR,
                    "amount": "100",
                    "proof": [],
                }
            )
        )
        assert out["isError"] is True
        assert out["details"]["error_code"] == "param_error"

    def test_bad_amount(self, connected, fake_mode):
        out = json.loads(
            airdrop(
                {
                    "action": "claim",
                    "distributor": DISTRIBUTOR,
                    "index": 1,
                    "amount": "garbage",
                    "proof": [],
                }
            )
        )
        assert out["isError"] is True

    def test_proof_non_string(self, connected, fake_mode):
        out = json.loads(
            airdrop(
                {
                    "action": "claim",
                    "distributor": DISTRIBUTOR,
                    "index": 1,
                    "amount": "100",
                    "proof": [123, "0x" + "1" * 64],  # int instead of hex str
                }
            )
        )
        assert out["isError"] is True

    def test_proof_non_hex(self, connected, fake_mode):
        out = json.loads(
            airdrop(
                {
                    "action": "claim",
                    "distributor": DISTRIBUTOR,
                    "index": 1,
                    "amount": "100",
                    "proof": ["not-hex"],
                }
            )
        )
        assert out["isError"] is True

    def test_custom_calldata_override(self, connected, fake_mode):
        out = json.loads(
            airdrop(
                {
                    "action": "claim",
                    "distributor": DISTRIBUTOR,
                    "calldata": "0xdeadbeef",
                }
            )
        )
        assert "isError" not in out
        kwargs = fake_mode.send_transaction.call_args.kwargs
        assert kwargs["data"] == "0xdeadbeef"

    def test_no_active_mode(self, connected, monkeypatch):
        from clawmes.services import wallet as wallet_mod

        svc = MagicMock()
        svc.active_mode = None
        monkeypatch.setattr(wallet_mod, "get_wallet_service", lambda: svc)
        out = json.loads(
            airdrop(
                {
                    "action": "claim",
                    "distributor": DISTRIBUTOR,
                    "index": 1,
                    "amount": "100",
                    "proof": [],
                }
            )
        )
        assert out["isError"] is True
        assert out["details"]["error_code"] == "wallet_not_connected"

    def test_send_failure(self, connected, fake_mode):
        fake_mode.send_transaction.side_effect = RuntimeError("rejected")
        out = json.loads(
            airdrop(
                {
                    "action": "claim",
                    "distributor": DISTRIBUTOR,
                    "index": 1,
                    "amount": "100",
                    "proof": [],
                }
            )
        )
        assert out["isError"] is True
        assert out["details"]["error_code"] == "send_failed"


class TestChainId:
    def test_explicit_chain_id(self, connected, fake_mode):
        airdrop(
            {
                "action": "claim",
                "distributor": DISTRIBUTOR,
                "calldata": "0xabc",
                "chain_id": 8453,
            }
        )
        kwargs = fake_mode.send_transaction.call_args.kwargs
        assert kwargs["chain_id"] == 8453


class TestRegister:
    def test_register_calls_ctx(self):
        from clawmes.tools import airdrop as ad_mod

        recorded = []

        class FakeCtx:
            def register_tool(self, **kw):
                recorded.append(kw)

        ad_mod.register(FakeCtx())
        assert len(recorded) == 1
        assert recorded[0]["name"] == "airdrop"
