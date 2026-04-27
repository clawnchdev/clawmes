"""Tests for clawmes.services.wallet.

The wallet service holds the active :class:`WalletMode` and exposes
the rich :class:`clawmes.wallet.state.WalletState` to the rest of
clawmes.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from clawmes.services import wallet as wallet_mod
from clawmes.services.wallet import (
    WalletService,
    get_wallet_service,
    get_wallet_state,
)
from clawmes.wallet.state import WalletState


@pytest.fixture(autouse=True)
def _reset_singleton(monkeypatch):
    monkeypatch.setattr(wallet_mod, "_instance", None)


class TestWalletServiceDefaults:
    def test_start_stop_lifecycle(self):
        svc = WalletService()
        svc.start()
        svc.stop()  # no mode → no-op

    def test_initial_state_disconnected(self):
        svc = WalletService()
        assert svc.state.connected is False
        assert svc.state.address is None
        assert svc.active_mode is None


class TestSetMode:
    def test_set_mode_assigns(self):
        svc = WalletService()
        mode = MagicMock()
        svc.set_mode(mode)
        assert svc.active_mode is mode

    def test_set_mode_disconnects_previous(self):
        svc = WalletService()
        first = MagicMock()
        second = MagicMock()
        svc.set_mode(first)
        svc.set_mode(second)
        first.disconnect.assert_called_once_with()

    def test_set_mode_swallows_disconnect_failure(self):
        svc = WalletService()
        first = MagicMock()
        first.disconnect.side_effect = RuntimeError("simulated")
        svc.set_mode(first)
        # Replacing must not propagate the previous mode's failure
        svc.set_mode(MagicMock())

    def test_set_mode_none_clears(self):
        svc = WalletService()
        mode = MagicMock()
        svc.set_mode(mode)
        svc.set_mode(None)
        assert svc.active_mode is None
        mode.disconnect.assert_called_once_with()


class TestStateRouting:
    def test_state_returns_active_mode_state(self):
        svc = WalletService()
        connected = WalletState.for_chain(
            mode="walletconnect",
            address="0x" + "a" * 40,
            chain_id=8453,
        )
        mode = MagicMock()
        mode.state.return_value = connected
        svc.set_mode(mode)
        assert svc.state == connected

    def test_state_failure_returns_disconnected(self):
        svc = WalletService()
        mode = MagicMock()
        mode.state.side_effect = RuntimeError("oops")
        svc.set_mode(mode)
        # Must never propagate — the prompt builder reads this on every turn
        assert svc.state.connected is False


class TestStop:
    def test_stop_disconnects_active_mode(self):
        svc = WalletService()
        mode = MagicMock()
        svc.set_mode(mode)
        svc.stop()
        mode.disconnect.assert_called_once_with()

    def test_stop_swallows_disconnect_failure(self):
        svc = WalletService()
        mode = MagicMock()
        mode.disconnect.side_effect = RuntimeError("oops")
        svc.set_mode(mode)
        svc.stop()  # must not raise


class TestSingleton:
    def test_returns_same_instance(self):
        a = get_wallet_service()
        b = get_wallet_service()
        assert a is b

    def test_get_wallet_state_via_accessor(self):
        # Default-disconnected via the singleton
        state = get_wallet_state()
        assert isinstance(state, WalletState)
        assert state.connected is False


class TestConnectWalletConnect:
    def test_no_bridge_raises_config_error(self, monkeypatch):
        from clawmes.bridges import installer
        from clawmes.services.wallet import WalletConfigError

        # Force ensure_node_bridges to return None paths
        class FakeBridgePaths:
            wc_entry = None
            sa_entry = None

        monkeypatch.setattr(installer, "ensure_node_bridges", lambda: FakeBridgePaths)

        svc = WalletService()
        with pytest.raises(WalletConfigError, match="WalletConnect bridge"):
            svc.connect_walletconnect()

    def test_creates_mode_and_returns_state(self, monkeypatch, tmp_path):
        from clawmes.bridges import installer
        from clawmes.bridges import wc_client as wc_client_module

        class FakeBridgePaths:
            wc_entry = tmp_path / "fake.mjs"
            sa_entry = None

        monkeypatch.setattr(installer, "ensure_node_bridges", lambda: FakeBridgePaths)

        # Substitute a fake WalletConnectClient that doesn't actually spawn
        fake_client = MagicMock()
        fake_client.pair.return_value = {"uri": "wc:abc@2", "topic": "abc"}
        monkeypatch.setattr(wc_client_module, "WalletConnectClient", lambda *a, **kw: fake_client)

        svc = WalletService()
        state = svc.connect_walletconnect()
        assert state.mode == "walletconnect"
        assert state.balances.get("_pair_uri") == "wc:abc@2"
        # Mode is set on the service
        assert svc.active_mode is not None
        # client.start was called (lifecycle handoff)
        fake_client.start.assert_called_once_with()
