"""Tests for the ``eas_attestation`` tool."""

from __future__ import annotations

import json

import pytest

from clawmes.services import rpc as rpc_mod
from clawmes.tools import eas_attestation as eas_mod
from clawmes.tools.eas_attestation import eas_attestation, register


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    from clawmes.policy import storage as policy_storage

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(rpc_mod, "_instance", None)
    policy_storage.save_policies([])


@pytest.fixture
def fake_rpc(monkeypatch):
    class FakeRpc:
        calls: list = []
        responses: list = []

        def eth_call(self, *, to, data, chain_id, block="latest"):
            self.calls.append({"to": to, "data": data, "chain_id": chain_id})
            if not self.responses:
                raise AssertionError("no fake rpc response queued")
            r = self.responses.pop(0)
            if isinstance(r, Exception):
                raise r
            return r

    fake = FakeRpc()
    # Patch the alias inside the tool module, not the source. The tool
    # does `from clawmes.services.rpc import ... get_rpc_service` at
    # module load time, so the binding lives in eas_attestation's
    # namespace.
    monkeypatch.setattr(eas_mod, "get_rpc_service", lambda: fake)
    return fake


def _encode_attestation(
    *,
    uid: bytes,
    schema: bytes = b"\xbb" * 32,
    time_: int = 1779000000,
    expiration: int = 0,
    revocation: int = 0,
    ref_uid: bytes = b"\x00" * 32,
    recipient: str = "0x" + "cc" * 20,
    attester: str = "0x" + "dd" * 20,
    revocable: bool = True,
    data: bytes = b"hello",
) -> str:
    """Build a hex-encoded EAS getAttestation return value."""
    from eth_abi import encode

    encoded = encode(
        [
            "(bytes32,bytes32,uint64,uint64,uint64,bytes32,address,address,bool,bytes)",
        ],
        [
            (
                uid,
                schema,
                time_,
                expiration,
                revocation,
                ref_uid,
                recipient,
                attester,
                revocable,
                data,
            )
        ],
    )
    return "0x" + encoded.hex()


# --- get action --------------------------------------------------------


class TestGet:
    def test_basic(self, fake_rpc):
        uid = b"\xaa" * 32
        fake_rpc.responses.append(_encode_attestation(uid=uid))
        out = json.loads(eas_attestation({"action": "get", "uid": "0x" + uid.hex()}))
        details = out["details"]
        assert details["uid"] == "0x" + uid.hex()
        assert details["attester"] == "0x" + "dd" * 20
        assert details["recipient"] == "0x" + "cc" * 20
        assert details["revocable"] is True
        assert details["is_revoked"] is False
        # Check the RPC call was to the canonical EAS address on Base.
        assert fake_rpc.calls[0]["to"] == ("0x4200000000000000000000000000000000000021")
        assert fake_rpc.calls[0]["chain_id"] == 8453

    def test_revoked_attestation(self, fake_rpc):
        uid = b"\xaa" * 32
        fake_rpc.responses.append(_encode_attestation(uid=uid, revocation=1779100000))
        out = json.loads(eas_attestation({"action": "get", "uid": "0x" + uid.hex()}))
        assert out["details"]["is_revoked"] is True
        assert "REVOKED" in out["content"][0]["text"]

    def test_expired_attestation(self, fake_rpc):
        uid = b"\xaa" * 32
        # Expiration set to a past time (epoch 100).
        fake_rpc.responses.append(_encode_attestation(uid=uid, expiration=100))
        out = json.loads(eas_attestation({"action": "get", "uid": "0x" + uid.hex()}))
        assert out["details"]["is_expired"] is True
        assert "EXPIRED" in out["content"][0]["text"]

    def test_no_expiration(self, fake_rpc):
        uid = b"\xaa" * 32
        fake_rpc.responses.append(_encode_attestation(uid=uid, expiration=0))
        out = json.loads(eas_attestation({"action": "get", "uid": "0x" + uid.hex()}))
        assert out["details"]["is_expired"] is False

    def test_uid_without_0x_prefix(self, fake_rpc):
        uid = b"\xaa" * 32
        fake_rpc.responses.append(_encode_attestation(uid=uid))
        # No 0x prefix.
        out = json.loads(eas_attestation({"action": "get", "uid": uid.hex()}))
        assert out["details"]["uid"] == "0x" + uid.hex()

    def test_custom_chain_id(self, fake_rpc):
        uid = b"\xaa" * 32
        fake_rpc.responses.append(_encode_attestation(uid=uid))
        eas_attestation({"action": "get", "uid": "0x" + uid.hex(), "chain_id": 1})
        assert fake_rpc.calls[0]["chain_id"] == 1

    def test_custom_eas_address(self, fake_rpc):
        uid = b"\xaa" * 32
        fake_rpc.responses.append(_encode_attestation(uid=uid))
        eas_attestation(
            {
                "action": "get",
                "uid": "0x" + uid.hex(),
                "eas_address": "0x" + "11" * 20,
            }
        )
        assert fake_rpc.calls[0]["to"] == "0x" + "11" * 20

    def test_all_zero_uid_returns_not_found(self, fake_rpc):
        # EAS returns an all-zero Attestation for nonexistent UIDs.
        fake_rpc.responses.append(_encode_attestation(uid=b"\x00" * 32))
        out = json.loads(eas_attestation({"action": "get", "uid": "0x" + "aa" * 32}))
        assert out["details"]["error_code"] == "not_found"

    def test_empty_rpc_result(self, fake_rpc):
        fake_rpc.responses.append("0x")
        out = json.loads(eas_attestation({"action": "get", "uid": "0x" + "aa" * 32}))
        assert out["details"]["error_code"] == "not_found"

    def test_rpc_error(self, fake_rpc):
        from clawmes.services.rpc import RpcError

        fake_rpc.responses.append(RpcError("rpc_unreachable", "endpoint down"))
        out = json.loads(eas_attestation({"action": "get", "uid": "0x" + "aa" * 32}))
        assert out["details"]["error_code"] == "rpc_error"

    def test_bad_uid_hex(self):
        out = json.loads(eas_attestation({"action": "get", "uid": "not-hex"}))
        assert out["details"]["error_code"] == "param_error"

    def test_wrong_length_uid(self):
        # 16 bytes instead of 32.
        out = json.loads(eas_attestation({"action": "get", "uid": "0x" + "aa" * 16}))
        assert out["details"]["error_code"] == "param_error"

    def test_missing_uid(self):
        out = json.loads(eas_attestation({"action": "get"}))
        assert out["details"]["error_code"] == "param_error"

    def test_decode_failure(self, fake_rpc):
        # Return malformed data that can't decode as the Attestation tuple.
        fake_rpc.responses.append("0xdeadbeef")
        out = json.loads(eas_attestation({"action": "get", "uid": "0x" + "aa" * 32}))
        assert out["details"]["error_code"] == "api_error"


# --- decode_data action ------------------------------------------------


class TestDecodeData:
    def test_basic(self):
        from eth_abi import encode

        encoded = encode(["uint8", "string"], [42, "hello"])
        out = json.loads(
            eas_attestation(
                {
                    "action": "decode_data",
                    "data_hex": "0x" + encoded.hex(),
                    "schema_types": "uint8,string",
                }
            )
        )
        assert out["details"]["values"][0] == 42
        assert out["details"]["values"][1] == "hello"

    def test_without_0x_prefix(self):
        from eth_abi import encode

        encoded = encode(["uint8"], [42])
        out = json.loads(
            eas_attestation(
                {
                    "action": "decode_data",
                    "data_hex": encoded.hex(),
                    "schema_types": "uint8",
                }
            )
        )
        assert out["details"]["values"][0] == 42

    def test_bytes_value_stringified(self):
        from eth_abi import encode

        encoded = encode(["bytes32"], [b"\xaa" * 32])
        out = json.loads(
            eas_attestation(
                {
                    "action": "decode_data",
                    "data_hex": "0x" + encoded.hex(),
                    "schema_types": "bytes32",
                }
            )
        )
        # bytes are surfaced as 0x-prefixed hex strings.
        assert out["details"]["values"][0] == "0x" + "aa" * 32

    def test_tuple_value(self):
        from eth_abi import encode

        encoded = encode(["(uint8,bool)"], [(7, True)])
        out = json.loads(
            eas_attestation(
                {
                    "action": "decode_data",
                    "data_hex": "0x" + encoded.hex(),
                    "schema_types": "(uint8,bool)",
                }
            )
        )
        # Tuple comes back as a list of [int, bool].
        assert out["details"]["values"][0] == [7, True]

    def test_missing_data(self):
        out = json.loads(eas_attestation({"action": "decode_data", "schema_types": "uint8"}))
        assert out["details"]["error_code"] == "param_error"

    def test_missing_schema(self):
        out = json.loads(eas_attestation({"action": "decode_data", "data_hex": "0x00"}))
        assert out["details"]["error_code"] == "param_error"

    def test_bad_data_hex(self):
        out = json.loads(
            eas_attestation(
                {
                    "action": "decode_data",
                    "data_hex": "not-hex",
                    "schema_types": "uint8",
                }
            )
        )
        assert out["details"]["error_code"] == "param_error"

    def test_empty_schema_types_string(self):
        out = json.loads(
            eas_attestation(
                {
                    "action": "decode_data",
                    "data_hex": "0x00",
                    "schema_types": ",  ,",
                }
            )
        )
        assert out["details"]["error_code"] == "param_error"

    def test_decode_failure_against_schema(self):
        out = json.loads(
            eas_attestation(
                {
                    "action": "decode_data",
                    "data_hex": "0x" + "00" * 10,
                    "schema_types": "uint256,string",  # not enough data
                }
            )
        )
        assert out["details"]["error_code"] == "api_error"


# --- guards ------------------------------------------------------------


class TestGuards:
    def test_unknown_action(self):
        out = json.loads(eas_attestation({"action": "explode"}))
        assert out["details"]["error_code"] == "param_error"

    def test_missing_action(self):
        out = json.loads(eas_attestation({}))
        assert out["details"]["error_code"] == "param_error"


# --- helpers -----------------------------------------------------------


class TestHelpers:
    def test_now_seconds(self):
        # Just verify it returns a sensible int.
        assert eas_mod._now_seconds() > 1_700_000_000

    def test_stringify_for_json_passes_through_ints_and_bools(self):
        out = eas_mod._stringify_for_json((42, True, "x"))
        assert out == [42, True, "x"]


# --- registration ------------------------------------------------------


class TestRegister:
    def test_register(self):
        captured = []

        class FakeCtx:
            def register_tool(self, **kw):
                captured.append(kw)

        register(FakeCtx())
        assert len(captured) == 1
        assert captured[0]["name"] == "eas_attestation"
