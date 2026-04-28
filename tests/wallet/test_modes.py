"""Tests for wallet mode implementations.

WalletConnect and local-key modes are wired up; Bankr mode remains a
stub.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from clawmes.bridges.process import BridgeError
from clawmes.wallet._base import WalletMode
from clawmes.wallet.bankr import BankrMode
from clawmes.wallet.local_key import KEYRING_SERVICE, LocalKeyMode
from clawmes.wallet.state import WalletState
from clawmes.wallet.walletconnect import WalletConnectMode, _to_hex

# Bankr mode invariants ---------------------------------------------------


class TestBankrShape:
    """Bankr mode is now wired through bankr_service. These tests cover
    the no-credentials / not-connected paths; full happy-path tests
    live in TestBankrModeWithService below."""

    def test_subclass_of_base(self):
        assert issubclass(BankrMode, WalletMode)

    def test_name_set(self):
        m = BankrMode()
        assert m.name == "bankr"

    def test_state_returns_wallet_state(self):
        m = BankrMode()
        s = m.state()
        assert isinstance(s, WalletState)
        assert s.connected is False

    def test_disconnect_idempotent(self):
        m = BankrMode()
        m.disconnect()
        m.disconnect()
        assert m.state().connected is False

    def test_send_transaction_when_disconnected(self):
        from clawmes.services.bankr_service import BankrError

        m = BankrMode()
        with pytest.raises(BankrError, match="Bankr wallet not connected"):
            m.send_transaction(to="0x" + "a" * 40, value=10**18)

    def test_sign_typed_data_when_disconnected(self):
        from clawmes.services.bankr_service import BankrError

        m = BankrMode()
        with pytest.raises(BankrError, match="Bankr wallet not connected"):
            m.sign_typed_data_v4({"types": {}})

    def test_sign_personal_when_disconnected(self):
        from clawmes.services.bankr_service import BankrError

        m = BankrMode()
        with pytest.raises(BankrError, match="Bankr wallet not connected"):
            m.sign_personal_message("hello")


class TestBankrModeWithService:
    """Wire BankrMode against a fake bankr_service for the happy path."""

    @pytest.fixture(autouse=True)
    def _isolate(self, monkeypatch):
        from clawmes.services import bankr_service as bs_mod

        fake = MagicMock()
        fake.has_credentials = True
        fake.get_account.return_value = {
            "user_id": "u1",
            "tier": "pro",
            "addresses": {"1": "0x" + "a" * 40, "8453": "0x" + "b" * 40},
        }
        fake.send_transaction.return_value = "0xfeed"
        fake.sign_typed_data_v4.return_value = "0xtypedsig"
        fake.sign_personal_message.return_value = "0xpersonalsig"
        monkeypatch.setattr(bs_mod, "_instance", fake)
        return fake

    def test_connect_picks_default_chain(self, _isolate):
        m = BankrMode()
        state = m.connect()
        assert state.connected is True
        assert state.chain_id == 8453
        assert state.address == "0x" + "b" * 40
        assert state.chain_name == "Base"

    def test_connect_explicit_chain(self, _isolate):
        m = BankrMode()
        state = m.connect(chain_id=1)
        assert state.chain_id == 1
        assert state.address == "0x" + "a" * 40

    def test_connect_unknown_chain_falls_through(self, _isolate, monkeypatch):
        # Inject an addresses map with an unknown chain id
        from clawmes.services import bankr_service as bs_mod

        bs_mod._instance.get_account.return_value = {
            "addresses": {"999999": "0x" + "c" * 40},
        }
        m = BankrMode()
        state = m.connect(chain_id=999999)
        assert state.chain_name == "chain 999999"

    def test_connect_no_address_for_chain(self, _isolate, monkeypatch):
        from clawmes.services import bankr_service as bs_mod
        from clawmes.services.bankr_service import BankrError

        bs_mod._instance.get_account.return_value = {"addresses": {}}
        m = BankrMode()
        with pytest.raises(BankrError, match="no address for chain"):
            m.connect(chain_id=8453)

    def test_connect_propagates_bankr_error(self, _isolate, monkeypatch):
        from clawmes.services import bankr_service as bs_mod
        from clawmes.services.bankr_service import BankrError

        bs_mod._instance.get_account.side_effect = BankrError("no_credentials", "no key")
        m = BankrMode()
        with pytest.raises(BankrError) as exc_info:
            m.connect()
        assert exc_info.value.code == "no_credentials"

    def test_send_transaction(self, _isolate):
        m = BankrMode()
        m.connect()
        result = m.send_transaction(
            to="0x" + "1" * 40,
            value=10**18,
            data=b"\xab",
            gas=21000,
            max_fee_per_gas=10**10,
            max_priority_fee_per_gas=10**9,
        )
        assert result == "0xfeed"
        # Service was called with the right shape
        call = _isolate.send_transaction.call_args
        assert call.kwargs["chain_id"] == 8453
        assert call.kwargs["to"] == "0x" + "1" * 40
        assert call.kwargs["value"] == 10**18
        assert call.kwargs["gas"] == 21000

    def test_send_transaction_explicit_chain(self, _isolate):
        m = BankrMode()
        m.connect()
        m.send_transaction(to="0x" + "1" * 40, value=0, chain_id=1)
        call = _isolate.send_transaction.call_args
        assert call.kwargs["chain_id"] == 1

    def test_sign_typed_data(self, _isolate):
        m = BankrMode()
        m.connect()
        sig = m.sign_typed_data_v4({"types": {}})
        assert sig == "0xtypedsig"
        _isolate.sign_typed_data_v4.assert_called_once()

    def test_sign_personal_message(self, _isolate):
        m = BankrMode()
        m.connect()
        sig = m.sign_personal_message("hello")
        assert sig == "0xpersonalsig"
        _isolate.sign_personal_message.assert_called_once()


# WalletConnect tests -----------------------------------------------------


class TestWalletConnectStub:
    """No-client construction + introspection."""

    def test_subclass_of_base(self):
        assert issubclass(WalletConnectMode, WalletMode)

    def test_name(self):
        m = WalletConnectMode()
        assert m.name == "walletconnect"

    def test_init_stores_project_id(self):
        m = WalletConnectMode(project_id="abc-123")
        assert m._project_id == "abc-123"

    def test_init_no_project_id(self):
        m = WalletConnectMode()
        assert m._project_id is None

    def test_default_state_disconnected(self):
        assert WalletConnectMode().state() == WalletState.disconnected()


class TestWalletConnectWithoutClient:
    """Without a client, every mutating operation raises RuntimeError."""

    def test_connect_raises(self):
        m = WalletConnectMode()
        with pytest.raises(RuntimeError, match="WalletConnectClient"):
            m.connect()

    def test_send_transaction_raises(self):
        m = WalletConnectMode()
        with pytest.raises(RuntimeError, match="no active.*session"):
            m.send_transaction(to="0x" + "a" * 40, value=10**18)

    def test_sign_typed_data_raises(self):
        m = WalletConnectMode()
        with pytest.raises(RuntimeError, match="no active.*session"):
            m.sign_typed_data_v4({"types": {}})

    def test_sign_personal_message_raises(self):
        m = WalletConnectMode()
        with pytest.raises(RuntimeError, match="no active.*session"):
            m.sign_personal_message("hi")

    def test_disconnect_clears_state_no_client(self):
        m = WalletConnectMode()
        m.disconnect()  # safe — clears local state
        assert m.state().connected is False


class TestWalletConnectWithClient:
    """All wired-up behavior with a mock WalletConnectClient."""

    @pytest.fixture
    def fake_client(self):
        c = MagicMock()
        c.pair.return_value = {"uri": "wc:abc@2", "topic": "abc-topic"}
        c.disconnect.return_value = None
        c.request_signature.return_value = "0xdeadbeef"
        return c

    @pytest.fixture
    def mode(self, fake_client):
        return WalletConnectMode(client=fake_client, project_id="test")

    def test_connect_calls_pair(self, mode, fake_client):
        state = mode.connect()
        assert state.mode == "walletconnect"
        # State NOT yet connected — that flips when pairing_approved fires
        assert state.connected is False
        # The pair URI is stashed for the caller to surface
        assert state.balances.get("_pair_uri") == "wc:abc@2"
        fake_client.pair.assert_called_once_with()

    def test_connect_propagates_bridge_error(self, mode, fake_client):
        fake_client.pair.side_effect = BridgeError("config_error", "missing key")
        with pytest.raises(BridgeError):
            mode.connect()

    def test_disconnect_calls_client(self, mode, fake_client):
        mode.connect()
        mode.disconnect()
        fake_client.disconnect.assert_called_once_with()
        assert mode.state().connected is False

    def test_disconnect_swallows_bridge_error(self, mode, fake_client):
        mode.connect()
        fake_client.disconnect.side_effect = BridgeError("network", "oops")
        # Must not raise — disconnect is best-effort
        mode.disconnect()
        assert mode.state().connected is False


class TestWalletConnectSigning:
    """After a session is applied, sign methods route through the client."""

    @pytest.fixture
    def mode(self):
        c = MagicMock()
        c.pair.return_value = {"uri": "wc:abc@2", "topic": "abc-topic"}
        c.request_signature.return_value = "0xfeedface"
        m = WalletConnectMode(client=c, project_id="test")
        m.connect()
        # Simulate the bridge's pairing_approved notification
        m._apply_session(address="0x" + "1" * 40, chain_id=8453)
        return m

    def test_send_transaction(self, mode):
        result = mode.send_transaction(
            to="0x" + "2" * 40,
            value=10**18,
            data=b"\xab\xcd",
            chain_id=8453,
            gas=21000,
            max_fee_per_gas=10**10,
            max_priority_fee_per_gas=10**9,
        )
        assert result == "0xfeedface"
        call = mode._client.request_signature.call_args
        assert call.kwargs["method"] == "eth_sendTransaction"
        tx = call.kwargs["params"][0]
        assert tx["to"] == "0x" + "2" * 40
        assert tx["value"] == hex(10**18)
        assert tx["data"] == "abcd"
        assert tx["gas"] == hex(21000)
        assert tx["maxFeePerGas"] == hex(10**10)
        assert tx["maxPriorityFeePerGas"] == hex(10**9)
        assert tx["from"] == "0x" + "1" * 40
        assert call.kwargs["metadata"]["chain_id"] == 8453

    def test_send_transaction_minimal(self, mode):
        # No optional fields — minimal tx body
        mode.send_transaction(to="0x" + "2" * 40, value=0)
        tx = mode._client.request_signature.call_args.kwargs["params"][0]
        assert "gas" not in tx
        assert "maxFeePerGas" not in tx
        assert "maxPriorityFeePerGas" not in tx
        # data was empty → field omitted
        assert "data" not in tx

    def test_send_transaction_string_data(self, mode):
        mode.send_transaction(to="0x" + "2" * 40, value=0, data="0xabcd")
        tx = mode._client.request_signature.call_args.kwargs["params"][0]
        assert tx["data"] == "0xabcd"

    def test_sign_typed_data_v4(self, mode):
        result = mode.sign_typed_data_v4({"types": {}, "domain": {}})
        assert result == "0xfeedface"
        call = mode._client.request_signature.call_args
        assert call.kwargs["method"] == "eth_signTypedData_v4"
        assert call.kwargs["params"][0] == "0x" + "1" * 40
        # Second param is the JSON-stringified typed data
        assert json.loads(call.kwargs["params"][1]) == {"types": {}, "domain": {}}

    def test_sign_personal_message_string(self, mode):
        result = mode.sign_personal_message("hello")
        assert result == "0xfeedface"
        call = mode._client.request_signature.call_args
        assert call.kwargs["method"] == "personal_sign"
        # First arg is hex of utf-8 bytes; second is address
        assert call.kwargs["params"][0] == "0x" + b"hello".hex()
        assert call.kwargs["params"][1] == "0x" + "1" * 40

    def test_sign_personal_message_bytes(self, mode):
        result = mode.sign_personal_message(b"\xab\xcd")
        assert result == "0xfeedface"
        params = mode._client.request_signature.call_args.kwargs["params"]
        assert params[0] == "abcd"


class TestApplySession:
    """The pairing_approved notification handler."""

    def test_applies_session(self):
        m = WalletConnectMode(client=MagicMock())
        m._apply_session(address="0x" + "a" * 40, chain_id=8453)
        s = m.state()
        assert s.connected is True
        assert s.address == "0x" + "a" * 40
        assert s.chain_id == 8453
        assert s.chain_name == "Base"

    def test_unknown_chain_falls_through(self):
        m = WalletConnectMode(client=MagicMock())
        # 999999 isn't in the chain registry
        m._apply_session(address="0xabc", chain_id=999999)
        s = m.state()
        assert s.connected is True
        assert s.chain_name == "chain 999999"


class TestPersonalSignHelpers:
    def test_to_hex(self):
        assert _to_hex("hello") == "0x" + b"hello".hex()


# Local key / Bankr mode-specific tests ----------------------------------


class TestLocalKeyMode:
    """LocalKeyMode is wired now; tests use a deterministic test mnemonic."""

    TEST_MNEMONIC = (
        "abandon abandon abandon abandon abandon abandon "
        "abandon abandon abandon abandon abandon about"
    )
    TEST_ADDRESS = "0x9858EfFD232B4033E47d90003D41EC34EcaEda94"

    @pytest.fixture(autouse=True)
    def _isolate(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        # Replace keyring backend with an in-memory dict for the test

        store: dict = {}

        class FakeKeyring:
            @staticmethod
            def set_password(service, account, value):
                store[(service, account)] = value

            @staticmethod
            def get_password(service, account):
                return store.get((service, account))

            @staticmethod
            def delete_password(service, account):
                store.pop((service, account), None)

        # Inject the fake by patching every reference path
        import sys
        import types

        fake_module = types.ModuleType("keyring")
        fake_module.set_password = FakeKeyring.set_password
        fake_module.get_password = FakeKeyring.get_password
        fake_module.delete_password = FakeKeyring.delete_password
        monkeypatch.setitem(sys.modules, "keyring", fake_module)

    def test_keyring_service_constant(self):
        assert KEYRING_SERVICE == "clawmes"

    def test_keystore_path_under_hermes_home(self, tmp_path):
        m = LocalKeyMode()
        path = m.keystore_path
        assert path.parent.parent.parent == tmp_path
        assert path.name == "keystore.bin"

    def test_password_cache_default_zero(self):
        m = LocalKeyMode()
        assert m._password_cache_seconds == 0

    def test_password_cache_seconds_stored(self):
        m = LocalKeyMode(password_cache_seconds=300)
        assert m._password_cache_seconds == 300

    def test_connect_requires_password(self):
        from clawmes.wallet.keystore import KeystoreError

        m = LocalKeyMode()
        with pytest.raises(KeystoreError, match="password is required"):
            m.connect()

    def test_connect_empty_password(self):
        from clawmes.wallet.keystore import KeystoreError

        m = LocalKeyMode()
        with pytest.raises(KeystoreError, match="password is required"):
            m.connect(password="")

    def test_connect_no_keystore_raises(self):
        from clawmes.wallet.keystore import KeystoreError

        m = LocalKeyMode()
        with pytest.raises(KeystoreError, match="no local keystore"):
            m.connect(password="hunter2")

    def test_connect_with_explicit_mnemonic(self):
        m = LocalKeyMode()
        state = m.connect(password="hunter2", mnemonic=self.TEST_MNEMONIC)
        assert state.connected is True
        assert state.mode == "local"
        assert state.address == self.TEST_ADDRESS
        assert state.balances["_mnemonic"] == self.TEST_MNEMONIC

    def test_connect_generate_creates_random_mnemonic(self):
        m = LocalKeyMode()
        state = m.connect(password="hunter2", generate=True)
        words = state.balances["_mnemonic"].split()
        assert len(words) == 24  # 256-bit entropy
        assert state.connected is True
        assert state.address.startswith("0x")

    def test_connect_then_load_with_password(self):
        m = LocalKeyMode()
        m.connect(password="hunter2", mnemonic=self.TEST_MNEMONIC)
        # New mode instance loads the persisted keystore
        m2 = LocalKeyMode()
        state = m2.connect(password="hunter2")
        assert state.address == self.TEST_ADDRESS

    def test_load_wrong_password_raises(self):
        from clawmes.wallet.keystore import KeystoreError

        m = LocalKeyMode()
        m.connect(password="hunter2", mnemonic=self.TEST_MNEMONIC)
        m2 = LocalKeyMode()
        with pytest.raises(KeystoreError, match="wrong password"):
            m2.connect(password="wrong-password")

    def test_invalid_mnemonic_import(self):
        from clawmes.wallet.keystore import KeystoreError

        m = LocalKeyMode()
        with pytest.raises(KeystoreError, match="non-empty"):
            m.connect(password="hunter2", mnemonic="")

    def test_disconnect_clears(self):
        m = LocalKeyMode(password_cache_seconds=60)
        m.connect(password="hunter2", mnemonic=self.TEST_MNEMONIC)
        m.disconnect()
        assert m.state().connected is False
        assert m._cached_mnemonic is None

    # --- signing (with cache enabled) ---

    @pytest.fixture
    def hot_mode(self):
        m = LocalKeyMode(password_cache_seconds=60)
        m.connect(password="hunter2", mnemonic=self.TEST_MNEMONIC)
        return m

    def test_sign_personal_message_string(self, hot_mode):
        sig = hot_mode.sign_personal_message("hello")
        assert sig.startswith("0x") or len(sig) == 130
        assert len(sig) >= 130  # 65 bytes hex

    def test_sign_personal_message_bytes(self, hot_mode):
        sig = hot_mode.sign_personal_message(b"hello")
        assert len(sig) >= 130

    def test_sign_typed_data_v4(self, hot_mode):
        typed = {
            "types": {
                "EIP712Domain": [{"name": "name", "type": "string"}],
                "Mail": [{"name": "from", "type": "string"}],
            },
            "primaryType": "Mail",
            "domain": {"name": "Test"},
            "message": {"from": "alice"},
        }
        sig = hot_mode.sign_typed_data_v4(typed)
        assert len(sig) >= 130

    def test_send_transaction_returns_signed_hex(self, hot_mode, monkeypatch):
        # Mock RPC since send_transaction touches eth_call as a placeholder
        from clawmes.services import rpc as rpc_module

        class FakeRpc:
            def eth_call(self, **kw):
                return "0x0"

        monkeypatch.setattr(rpc_module, "_instance", FakeRpc())
        raw = hot_mode.send_transaction(
            to="0x" + "a" * 40,
            value=10**17,
            chain_id=8453,
        )
        # eth-account returns raw signed tx as hex
        assert isinstance(raw, str)
        assert len(raw) > 50  # non-trivial RLP bytes

    def test_send_transaction_with_data_string(self, hot_mode, monkeypatch):
        from clawmes.services import rpc as rpc_module

        class FakeRpc:
            def eth_call(self, **kw):
                return "0x0"

        monkeypatch.setattr(rpc_module, "_instance", FakeRpc())
        raw = hot_mode.send_transaction(
            to="0x" + "a" * 40,
            value=0,
            data="0xdeadbeef",
            chain_id=8453,
            gas=50000,
            max_fee_per_gas=2 * 10**10,
            max_priority_fee_per_gas=2 * 10**9,
        )
        assert isinstance(raw, str)

    def test_send_transaction_with_data_bytes(self, hot_mode, monkeypatch):
        from clawmes.services import rpc as rpc_module

        class FakeRpc:
            def eth_call(self, **kw):
                return "0x0"

        monkeypatch.setattr(rpc_module, "_instance", FakeRpc())
        raw = hot_mode.send_transaction(
            to="0x" + "a" * 40,
            value=0,
            data=b"\xab\xcd",
            chain_id=8453,
        )
        assert isinstance(raw, str)

    def test_send_transaction_disconnected_raises(self):
        from clawmes.wallet.keystore import KeystoreError

        m = LocalKeyMode()
        with pytest.raises(KeystoreError, match="not connected"):
            m.send_transaction(to="0x" + "a" * 40, value=0)

    def test_signing_without_cache_raises(self):
        from clawmes.wallet.keystore import KeystoreError

        # password_cache_seconds=0 (default) → cache empty after connect
        m = LocalKeyMode()
        m.connect(password="hunter2", mnemonic=self.TEST_MNEMONIC)
        with pytest.raises(KeystoreError, match="cache expired or empty"):
            m.sign_personal_message("hello")

    def test_signing_after_cache_expiry(self, monkeypatch):
        import time as _time

        from clawmes.wallet.keystore import KeystoreError

        m = LocalKeyMode(password_cache_seconds=1)
        m.connect(password="hunter2", mnemonic=self.TEST_MNEMONIC)

        # Fast-forward past cache TTL
        real_monotonic = _time.monotonic
        offset = [0.0]
        monkeypatch.setattr(
            "clawmes.wallet.local_key.time.monotonic",
            lambda: real_monotonic() + offset[0],
        )
        offset[0] = 100.0
        with pytest.raises(KeystoreError, match="cache expired"):
            m.sign_personal_message("hello")


class TestBankrMode:
    def test_init_stores_api_key(self):
        m = BankrMode(api_key="bankr-xyz")
        assert m._api_key == "bankr-xyz"

    def test_no_api_key(self):
        m = BankrMode()
        assert m._api_key is None
