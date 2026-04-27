"""Tests for wallet mode implementations.

WalletConnect mode is wired through the Node bridge and tested against
a mock ``WalletConnectClient``. Local key and Bankr modes remain stubs
at this milestone; their tests pin the NotImplementedError contract.
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

# Stub-mode invariants (local + bankr) ------------------------------------


@pytest.mark.parametrize(
    "mode_cls,kwargs",
    [
        (LocalKeyMode, {"password_cache_seconds": 0}),
        (BankrMode, {"api_key": "test-key"}),
    ],
)
class TestStubModes:
    """Invariants for stub modes — connect/disconnect/state work; sign raises."""

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
        m.disconnect()
        m.disconnect()
        assert m.state().connected is False

    def test_send_transaction_raises_not_implemented(self, mode_cls, kwargs):
        m = mode_cls(**kwargs)
        with pytest.raises(NotImplementedError):
            m.send_transaction(to="0x" + "a" * 40, value=10**18)

    def test_sign_typed_data_raises_not_implemented(self, mode_cls, kwargs):
        m = mode_cls(**kwargs)
        with pytest.raises(NotImplementedError):
            m.sign_typed_data_v4({"types": {}})


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
    def test_keyring_service_constant(self):
        assert KEYRING_SERVICE == "clawmes"

    def test_keystore_path_under_hermes_home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
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
