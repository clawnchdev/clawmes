"""Tests for WalletConnectMode.request_execution_permissions (ERC-7715)."""

from __future__ import annotations

from typing import Any

import pytest

from clawmes.wallet.walletconnect import WalletConnectMode


class _FakeClient:
    def __init__(self):
        self.calls: list[dict[str, Any]] = []

    def request_signature(self, *, method, params, metadata=None):
        self.calls.append({"method": method, "params": params, "metadata": metadata})
        return [{"context": "0xabc", "signerMeta": {"delegationManager": "0xdm"}}]


def _connected_mode():
    mode = WalletConnectMode(client=_FakeClient())
    mode._active_topic = "topic-1"
    mode._apply_session(address="0x" + "55" * 20, chain_id=8453)
    return mode


class TestRequestExecutionPermissions:
    def test_forwards_to_bridge(self):
        mode = _connected_mode()
        params = [{"chainId": "0x2105", "permissions": []}]
        result = mode.request_execution_permissions(params)
        assert result[0]["context"] == "0xabc"
        call = mode._client.calls[0]
        assert call["method"] == "wallet_requestExecutionPermissions"
        assert call["params"] == params
        assert call["metadata"]["chain_id"] == 8453

    def test_requires_active_session(self):
        mode = WalletConnectMode(client=_FakeClient())
        with pytest.raises(RuntimeError, match="no active WalletConnect session"):
            mode.request_execution_permissions([{}])

    def test_requires_client(self):
        mode = WalletConnectMode(client=None)
        mode._active_topic = "topic-1"
        with pytest.raises(RuntimeError, match="no active WalletConnect session"):
            mode.request_execution_permissions([{}])
