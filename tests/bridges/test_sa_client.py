"""Tests for clawmes.bridges.sa_client (MetaMask Smart Accounts Python client)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from clawmes.bridges.sa_client import SmartAccountsClient


@pytest.fixture
def client():
    c = SmartAccountsClient(Path("/fake/sa.mjs"))
    c._proc = MagicMock()
    return c


class TestLifecycle:
    def test_start(self, client):
        client.start()
        client._proc.start.assert_called_once()

    def test_stop(self, client):
        client.stop()
        client._proc.stop.assert_called_once()


class TestDelegation:
    def test_delegation_create(self, client):
        client._proc.call.return_value = {
            "delegation_id": "0xabc",
            "tx_hash": "0xdef",
        }
        result = client.delegation_create(
            delegate="0x" + "a" * 40,
            permissions=[{"type": "swap"}],
            expiry=1234567890,
        )
        client._proc.call.assert_called_once_with(
            "delegation_create",
            {
                "delegate": "0x" + "a" * 40,
                "permissions": [{"type": "swap"}],
                "expiry": 1234567890,
            },
        )
        assert result["delegation_id"] == "0xabc"

    def test_delegation_list_empty(self, client):
        client._proc.call.return_value = {"delegations": []}
        assert client.delegation_list() == []

    def test_delegation_list_populated(self, client):
        client._proc.call.return_value = {"delegations": [{"id": "1"}, {"id": "2"}]}
        assert client.delegation_list() == [{"id": "1"}, {"id": "2"}]

    def test_delegation_list_missing_key(self, client):
        client._proc.call.return_value = {}
        assert client.delegation_list() == []

    def test_delegation_revoke(self, client):
        client._proc.call.return_value = {"tx_hash": "0xrevoke"}
        result = client.delegation_revoke("0xabc")
        assert result == "0xrevoke"

    def test_delegation_execute(self, client):
        client._proc.call.return_value = {"tx_hash": "0xexec"}
        result = client.delegation_execute(
            delegation_id="0xabc",
            calldata="0x123",
            to="0x" + "1" * 40,
            chain_id=8453,
        )
        assert result == "0xexec"
        # default value passes through
        call = client._proc.call.call_args
        assert call.args[1]["value"] == "0x0"

    def test_delegation_execute_with_value(self, client):
        client._proc.call.return_value = {"tx_hash": "0x"}
        client.delegation_execute(
            delegation_id="0xabc",
            calldata="0x",
            to="0x" + "1" * 40,
            value="0xde0b6b3a7640000",
            chain_id=8453,
        )
        call = client._proc.call.call_args
        assert call.args[1]["value"] == "0xde0b6b3a7640000"


class TestAccount:
    def test_account_deploy(self, client):
        client._proc.call.return_value = {"address": "0xdeployed", "tx_hash": "0xtx"}
        result = client.account_deploy(8453)
        assert result["address"] == "0xdeployed"
        client._proc.call.assert_called_once_with("account_deploy", {"chain_id": 8453})

    def test_permit2_sign(self, client):
        client._proc.call.return_value = {"signature": "0xsig", "permit": {}}
        result = client.permit2_sign(
            token="0xtoken",
            spender="0xspender",
            amount="1000",
            deadline=12345,
        )
        assert result["signature"] == "0xsig"
        call = client._proc.call.call_args
        assert call.args[0] == "permit2_sign"
        assert call.args[1]["amount"] == "1000"

    def test_health(self, client):
        client._proc.call.return_value = {"version": "0.1.0"}
        client.health()
        client._proc.call.assert_called_once_with("health", {})
