"""Tests for clawmes.tools.block_explorer."""

from __future__ import annotations

import json

import pytest

from clawmes.services import explorer as ex_module
from clawmes.services.explorer import ExplorerError, ExplorerService
from clawmes.tools.block_explorer import block_explorer


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(ex_module, "_instance", None)


@pytest.fixture
def fake_explorer(monkeypatch):
    """Replace the explorer service with a recorder."""

    class FakeExplorer(ExplorerService):
        def __init__(self):
            super().__init__()
            self.tx_status_responses: dict = {}
            self.tx_receipt_responses: dict = {}
            self.balance_responses: dict = {}
            self.tx_count_responses: dict = {}
            self.failure_targets: set = set()

        def supports_chain(self, chain_id):
            return chain_id in (1, 8453, 42161, 10, 137)

        def get_tx_status(self, tx_hash, chain_id):
            if (tx_hash, "status") in self.failure_targets:
                raise ExplorerError("simulated tx_status failure")
            return self.tx_status_responses.get((tx_hash, chain_id), {"errDescription": ""})

        def get_tx_receipt_status(self, tx_hash, chain_id):
            if (tx_hash, "receipt") in self.failure_targets:
                raise ExplorerError("simulated receipt failure")
            return self.tx_receipt_responses.get((tx_hash, chain_id), {"status": "1"})

        def get_address_balance(self, address, chain_id):
            if (address, "balance") in self.failure_targets:
                raise ExplorerError("simulated balance failure")
            return self.balance_responses.get((address.lower(), chain_id), 0)

        def get_address_tx_count(self, address, chain_id):
            return self.tx_count_responses.get((address.lower(), chain_id), 0)

    fake = FakeExplorer()
    monkeypatch.setattr(ex_module, "_instance", fake)
    return fake


HOLDER = "0x" + "a" * 40
TX_HASH = "0x" + "1" * 64


class TestTxAction:
    def test_happy_path(self, fake_explorer):
        out = json.loads(block_explorer({"action": "tx", "value": TX_HASH, "chain": "base"}))
        assert "isError" not in out
        assert out["details"]["receipt_status"] == "success"
        assert "basescan.org" in out["details"]["explorer_url"]

    def test_failed_tx(self, fake_explorer):
        fake_explorer.tx_receipt_responses[(TX_HASH, 8453)] = {"status": "0"}
        fake_explorer.tx_status_responses[(TX_HASH, 8453)] = {"errDescription": "out of gas"}
        out = json.loads(block_explorer({"action": "tx", "value": TX_HASH, "chain": "base"}))
        assert out["details"]["receipt_status"] == "failed"
        assert out["details"]["error_description"] == "out of gas"

    def test_invalid_tx_hash_too_short(self, fake_explorer):
        out = json.loads(block_explorer({"action": "tx", "value": "0xabc", "chain": "base"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "invalid_tx_hash"

    def test_invalid_tx_hash_no_prefix(self, fake_explorer):
        out = json.loads(block_explorer({"action": "tx", "value": "1" * 64, "chain": "base"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "invalid_tx_hash"

    def test_explorer_error_returns_envelope(self, fake_explorer):
        fake_explorer.failure_targets.add((TX_HASH, "receipt"))
        out = json.loads(block_explorer({"action": "tx", "value": TX_HASH, "chain": "base"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "explorer_error"


class TestAddressAction:
    def test_happy_path(self, fake_explorer):
        fake_explorer.balance_responses[(HOLDER.lower(), 8453)] = 5 * 10**17
        fake_explorer.tx_count_responses[(HOLDER.lower(), 8453)] = 42

        out = json.loads(block_explorer({"action": "address", "value": HOLDER, "chain": "base"}))
        assert "isError" not in out
        assert out["details"]["tx_count"] == 42
        assert "0.5" in out["details"]["native_balance"]
        assert "basescan.org" in out["details"]["explorer_url"]

    def test_invalid_address(self, fake_explorer):
        out = json.loads(
            block_explorer({"action": "address", "value": "not-an-address", "chain": "base"})
        )
        assert out["isError"] is True
        assert out["details"]["error_code"] == "invalid_address"

    def test_explorer_error_returns_envelope(self, fake_explorer):
        fake_explorer.failure_targets.add((HOLDER, "balance"))
        out = json.loads(block_explorer({"action": "address", "value": HOLDER, "chain": "base"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "explorer_error"


class TestChainHandling:
    def test_default_chain(self, fake_explorer):
        out = json.loads(block_explorer({"action": "tx", "value": TX_HASH}))
        assert out["details"]["chain_id"] == 8453

    def test_chain_by_id(self, fake_explorer):
        out = json.loads(block_explorer({"action": "tx", "value": TX_HASH, "chain": "1"}))
        assert out["details"]["chain_id"] == 1
        assert "etherscan.io" in out["details"]["explorer_url"]

    def test_unknown_chain(self, fake_explorer):
        out = json.loads(block_explorer({"action": "tx", "value": TX_HASH, "chain": "lunarchain"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "invalid_chain"

    def test_unsupported_chain(self, fake_explorer, monkeypatch):
        # zksync (324) is in lib/chains but not in the explorer service
        out = json.loads(block_explorer({"action": "tx", "value": TX_HASH, "chain": "324"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "explorer_unconfigured"


class TestRegister:
    def test_registers(self):
        from clawmes.tools import block_explorer as be_mod

        recorded = []

        class FakeCtx:
            def register_tool(self, **kw):
                recorded.append(kw)

        be_mod.register(FakeCtx())
        assert recorded[0]["name"] == "block_explorer"
