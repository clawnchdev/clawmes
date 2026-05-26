"""Tests for clawmes.wallet.base_account."""

from __future__ import annotations

import pytest

from clawmes.services.base_account import BaseAccountError
from clawmes.wallet.base_account import BaseAccountMode


class _FakeSvc:
    def __init__(self):
        self.calls: list = []
        self.auth_url = "https://auth.test/?client_id=x"
        self.address = "0x" + "1" * 40
        self.requests: list[dict] = []
        self.poll_responses: list[dict] = []
        self.submit_response = {
            "request_id": "req-1",
            "approval_url": "https://base.app/approve",
        }
        self.exchange_response = {"access_token": "acc", "address": "0x" + "1" * 40}
        self.stop_called = False

    def get_auth_url(self, **kwargs):
        return self.auth_url

    def exchange_code(self, code):
        self.calls.append(("exchange_code", code))
        return self.exchange_response

    def get_user_address(self):
        return self.address

    def submit_request(self, *, method, params):
        self.requests.append({"method": method, "params": params})
        return self.submit_response

    def poll_request(self, request_id, **_kw):
        if self.poll_responses:
            return self.poll_responses.pop(0)
        return {"status": "confirmed", "result": "0xtx"}

    def stop(self):
        self.stop_called = True


@pytest.fixture
def svc(monkeypatch):
    s = _FakeSvc()
    import clawmes.services.base_account as ba_mod

    monkeypatch.setattr(ba_mod, "_instance", s)
    return s


# ── lifecycle ─────────────────────────────────────────────────────


class TestConnect:
    def test_returns_disconnected_state_with_auth_url(self, svc):
        mode = BaseAccountMode()
        state = mode.connect()
        assert not state.connected
        assert mode.get_pending_auth_url() == svc.auth_url

    def test_connect_with_code(self, svc):
        mode = BaseAccountMode()
        mode.connect()
        state = mode.connect_with_code("abc123")
        assert state.connected
        assert state.address == "0x" + "1" * 40
        assert state.chain_id == 8453
        assert state.mode == "base_account"
        assert mode.get_pending_auth_url() is None

    def test_connect_with_code_custom_chain(self, svc):
        mode = BaseAccountMode()
        mode.connect()
        state = mode.connect_with_code("abc", chain_id=42161)
        assert state.chain_id == 42161

    def test_connect_with_code_unknown_chain(self, svc):
        mode = BaseAccountMode()
        mode.connect()
        state = mode.connect_with_code("abc", chain_id=99999999)
        assert state.chain_id == 99999999
        assert "99999999" in state.chain_name

    def test_disconnect(self, svc):
        mode = BaseAccountMode()
        mode.connect()
        mode.connect_with_code("abc")
        mode.disconnect()
        assert not mode.state().connected
        assert mode.get_pending_auth_url() is None
        assert svc.stop_called


# ── send_transaction ──────────────────────────────────────────────


class TestSendTransaction:
    def test_not_connected(self, svc):
        mode = BaseAccountMode()
        with pytest.raises(BaseAccountError) as exc_info:
            mode.send_transaction(to="0xabc", value=0)
        assert exc_info.value.code == "not_connected"

    def test_happy_path(self, svc):
        mode = BaseAccountMode()
        mode.connect()
        mode.connect_with_code("abc")
        tx_hash = mode.send_transaction(to="0xtarget", value=1000, data=b"\x12\x34")
        assert tx_hash == "0xtx"
        # Request shape
        req = svc.requests[0]
        assert req["method"] == "eth_sendTransaction"
        assert req["params"][0]["to"] == "0xtarget"
        assert req["params"][0]["value"] == "0x3e8"
        assert req["params"][0]["data"] == "0x1234"

    def test_bytes_data_encodes(self, svc):
        mode = BaseAccountMode()
        mode.connect()
        mode.connect_with_code("abc")
        mode.send_transaction(to="0xt", value=0, data=b"\xab\xcd")
        assert svc.requests[0]["params"][0]["data"] == "0xabcd"

    def test_string_data_normalized(self, svc):
        mode = BaseAccountMode()
        mode.connect()
        mode.connect_with_code("abc")
        # Without 0x prefix
        mode.send_transaction(to="0xt", value=0, data="deadbeef")
        assert svc.requests[0]["params"][0]["data"] == "0xdeadbeef"

    def test_chain_override(self, svc):
        mode = BaseAccountMode()
        mode.connect()
        mode.connect_with_code("abc")
        mode.send_transaction(to="0xt", value=0, chain_id=42161)
        assert svc.requests[0]["params"][0]["chainId"] == hex(42161)

    def test_gas_passed_through(self, svc):
        mode = BaseAccountMode()
        mode.connect()
        mode.connect_with_code("abc")
        mode.send_transaction(to="0xt", value=0, gas=200000)
        assert svc.requests[0]["params"][0]["gas"] == hex(200000)

    def test_no_tx_hash_in_confirmed_response(self, svc):
        mode = BaseAccountMode()
        mode.connect()
        mode.connect_with_code("abc")
        svc.poll_responses = [{"status": "confirmed"}]  # No result/tx_hash
        with pytest.raises(BaseAccountError) as exc_info:
            mode.send_transaction(to="0xt", value=0)
        assert exc_info.value.code == "request_failed"


# ── signing ──────────────────────────────────────────────────────


class TestSignTypedData:
    def test_not_connected(self, svc):
        mode = BaseAccountMode()
        with pytest.raises(BaseAccountError):
            mode.sign_typed_data_v4({})

    def test_happy_path(self, svc):
        mode = BaseAccountMode()
        mode.connect()
        mode.connect_with_code("abc")
        svc.poll_responses = [{"status": "confirmed", "result": "0xsig"}]
        sig = mode.sign_typed_data_v4({"types": {}, "primaryType": "X"})
        assert sig == "0xsig"
        assert svc.requests[0]["method"] == "eth_signTypedData_v4"

    def test_no_signature(self, svc):
        mode = BaseAccountMode()
        mode.connect()
        mode.connect_with_code("abc")
        svc.poll_responses = [{"status": "confirmed"}]
        with pytest.raises(BaseAccountError):
            mode.sign_typed_data_v4({})


class TestSignPersonalMessage:
    def test_not_connected(self, svc):
        mode = BaseAccountMode()
        with pytest.raises(BaseAccountError):
            mode.sign_personal_message("hi")

    def test_bytes_message(self, svc):
        mode = BaseAccountMode()
        mode.connect()
        mode.connect_with_code("abc")
        svc.poll_responses = [{"status": "confirmed", "signature": "0xsig"}]
        sig = mode.sign_personal_message(b"hello")
        assert sig == "0xsig"
        # First param is the message hex
        assert svc.requests[0]["params"][0] == "0x" + b"hello".hex()

    def test_hex_string_message(self, svc):
        mode = BaseAccountMode()
        mode.connect()
        mode.connect_with_code("abc")
        svc.poll_responses = [{"status": "confirmed", "result": "0xsig"}]
        mode.sign_personal_message("0xabcd")
        assert svc.requests[0]["params"][0] == "0xabcd"

    def test_plain_string_message(self, svc):
        mode = BaseAccountMode()
        mode.connect()
        mode.connect_with_code("abc")
        svc.poll_responses = [{"status": "confirmed", "result": "0xsig"}]
        mode.sign_personal_message("hello")
        # Plain string gets hex-encoded
        assert svc.requests[0]["params"][0].startswith("0x")

    def test_no_signature_in_response(self, svc):
        mode = BaseAccountMode()
        mode.connect()
        mode.connect_with_code("abc")
        svc.poll_responses = [{"status": "confirmed"}]
        with pytest.raises(BaseAccountError):
            mode.sign_personal_message("hi")


# ── switch_chain ─────────────────────────────────────────────────


class TestSwitchChain:
    def test_not_connected(self, svc):
        mode = BaseAccountMode()
        with pytest.raises(BaseAccountError):
            mode.switch_chain(1)

    def test_switches(self, svc):
        mode = BaseAccountMode()
        mode.connect()
        mode.connect_with_code("abc")
        new_state = mode.switch_chain(1)
        assert new_state.chain_id == 1

    def test_unknown_chain_gracefully(self, svc):
        mode = BaseAccountMode()
        mode.connect()
        mode.connect_with_code("abc")
        new_state = mode.switch_chain(99999999)
        assert new_state.chain_id == 99999999
        assert "99999999" in new_state.chain_name
