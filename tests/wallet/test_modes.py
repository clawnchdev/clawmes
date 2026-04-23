"""Tests for wallet mode stubs (WalletConnect, local key, Bankr).

These modes are skeletons at this milestone — the real implementations
ship in subsequent commits as bridges + key management land. The tests
pin the public surface (connect/disconnect/state/send/sign signatures)
so the contract doesn't drift while the implementations are being
written.
"""

from __future__ import annotations

import pytest

from clawmes.wallet._base import WalletMode
from clawmes.wallet.bankr import BankrMode
from clawmes.wallet.local_key import KEYRING_SERVICE, LocalKeyMode
from clawmes.wallet.state import WalletState
from clawmes.wallet.walletconnect import WalletConnectMode

# Shared invariants ---------------------------------------------------------


@pytest.mark.parametrize(
    "mode_cls,kwargs",
    [
        (WalletConnectMode, {"project_id": "test-project"}),
        (LocalKeyMode, {"password_cache_seconds": 0}),
        (BankrMode, {"api_key": "test-key"}),
    ],
)
class TestEveryMode:
    """Invariants every WalletMode subclass must satisfy."""

    def test_subclass_of_base(self, mode_cls, kwargs):
        assert issubclass(mode_cls, WalletMode)

    def test_name_set(self, mode_cls, kwargs):
        m = mode_cls(**kwargs)
        assert isinstance(m.name, str)
        assert m.name

    def test_state_returns_wallet_state(self, mode_cls, kwargs):
        m = mode_cls(**kwargs)
        s = m.state()
        assert isinstance(s, WalletState)
        assert s.connected is False

    def test_connect_returns_state(self, mode_cls, kwargs):
        m = mode_cls(**kwargs)
        s = m.connect()
        assert isinstance(s, WalletState)

    def test_disconnect_idempotent(self, mode_cls, kwargs):
        m = mode_cls(**kwargs)
        m.disconnect()  # never connected
        m.disconnect()  # again — must not raise
        assert m.state().connected is False

    def test_send_transaction_raises_not_implemented(self, mode_cls, kwargs):
        m = mode_cls(**kwargs)
        with pytest.raises(NotImplementedError):
            m.send_transaction(
                to="0x" + "a" * 40,
                value=10**18,
            )

    def test_sign_typed_data_raises_not_implemented(self, mode_cls, kwargs):
        m = mode_cls(**kwargs)
        with pytest.raises(NotImplementedError):
            m.sign_typed_data_v4({"types": {}})


# Mode-specific tests -------------------------------------------------------


class TestWalletConnectMode:
    def test_init_stores_project_id(self):
        m = WalletConnectMode(project_id="abc-123")
        assert m._project_id == "abc-123"

    def test_init_no_project_id(self):
        m = WalletConnectMode()
        assert m._project_id is None

    def test_disconnect_clears_state(self):
        m = WalletConnectMode()
        m.disconnect()
        assert m.state() == WalletState.disconnected()


class TestLocalKeyMode:
    def test_keyring_service_constant(self):
        # Pin the constant so a rename trips a test
        assert KEYRING_SERVICE == "clawmes"

    def test_keystore_path_under_hermes_home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        m = LocalKeyMode()
        path = m.keystore_path
        # ${HERMES_HOME}/clawmes/wallet/keystore.bin → walk up three levels
        assert path.parent.parent.parent == tmp_path
        assert path.name == "keystore.bin"

    def test_password_cache_default_zero(self):
        m = LocalKeyMode()
        assert m._password_cache_seconds == 0

    def test_password_cache_seconds_stored(self):
        m = LocalKeyMode(password_cache_seconds=300)
        assert m._password_cache_seconds == 300

    def test_personal_sign_raises_not_implemented(self):
        m = LocalKeyMode()
        with pytest.raises(NotImplementedError):
            m.sign_personal_message("hello")


class TestBankrMode:
    def test_init_stores_api_key(self):
        m = BankrMode(api_key="bankr-xyz")
        assert m._api_key == "bankr-xyz"

    def test_no_api_key(self):
        m = BankrMode()
        assert m._api_key is None


# Base class -----------------------------------------------------------------


class TestWalletModeBase:
    def test_default_personal_sign_raises(self):
        # WalletConnect doesn't override personal_sign; default raises.
        m = WalletConnectMode()
        with pytest.raises(NotImplementedError, match="does not support personal_sign"):
            m.sign_personal_message("hi")
