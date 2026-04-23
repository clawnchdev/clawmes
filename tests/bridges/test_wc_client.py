"""Tests for clawmes.bridges.wc_client (WalletConnect Python client)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from clawmes.bridges.wc_client import WalletConnectClient


@pytest.fixture
def client():
    """Construct a client and replace its BridgeProcess with a Mock."""
    c = WalletConnectClient(Path("/fake/wc.mjs"))
    c._proc = MagicMock()
    return c


class TestLifecycle:
    def test_start(self, client):
        client.start()
        client._proc.start.assert_called_once()

    def test_stop(self, client):
        client.stop()
        client._proc.stop.assert_called_once()


class TestMethods:
    def test_pair(self, client):
        client._proc.call.return_value = {"uri": "wc:abc...", "session_topic": "topic"}
        result = client.pair()
        client._proc.call.assert_called_once_with("pair", {})
        assert result["uri"] == "wc:abc..."

    def test_session_status(self, client):
        client._proc.call.return_value = {"connected": True}
        client.session_status()
        client._proc.call.assert_called_once_with("session_status", {})

    def test_disconnect(self, client):
        client._proc.call.return_value = {}
        client.disconnect()
        client._proc.call.assert_called_once_with("disconnect", {})

    def test_request_signature(self, client):
        client._proc.call.return_value = {"signature_or_hash": "0xdeadbeef"}
        result = client.request_signature(
            method="eth_sendTransaction",
            params=[{"to": "0xabc"}],
            metadata={"label": "test"},
        )
        assert result == "0xdeadbeef"
        # Call args
        call = client._proc.call.call_args
        assert call.args[0] == "request_signature"
        assert call.args[1]["method"] == "eth_sendTransaction"
        assert call.args[1]["metadata"] == {"label": "test"}
        # Generous timeout for human-in-the-loop
        assert call.kwargs["timeout"] == 180.0

    def test_request_signature_default_metadata(self, client):
        client._proc.call.return_value = {"signature_or_hash": "0x123"}
        client.request_signature(
            method="personal_sign",
            params=["msg", "0xabc"],
        )
        call = client._proc.call.call_args
        assert call.args[1]["metadata"] == {}

    def test_switch_chain_ok(self, client):
        client._proc.call.return_value = {"ok": True}
        assert client.switch_chain(8453) is True

    def test_switch_chain_rejected(self, client):
        client._proc.call.return_value = {}  # no "ok" key
        assert client.switch_chain(1) is False

    def test_health(self, client):
        client._proc.call.return_value = {"version": "0.1.0"}
        result = client.health()
        client._proc.call.assert_called_once_with("health", {})
        assert result["version"] == "0.1.0"

    def test_notifications_proxies_through(self, client):
        client._proc.notifications.return_value = "queue-handle"
        assert client.notifications() == "queue-handle"
