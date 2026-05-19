"""``eas_attestation`` — read EAS attestations on Base.

Ethereum Attestation Service (https://attest.org) is a generic
on-chain primitive for cryptographic attestations: agent identity
claims, KYC results, trust-score certificates, signal predictions
(BV-7X uses it), and more. EAS is deployed on Base mainnet at
``0x4200000000000000000000000000000000000021`` (Identity Registry
predeploy slot) — the same singleton everyone reads from.

This tool reads attestations by UID. It returns the decoded
:class:`Attestation` struct (uid, schema, time, expirationTime,
revocationTime, refUID, recipient, attester, revocable, raw data).
It does NOT verify signatures yet — the data is committed on-chain
under ``attester``, which is what matters for most read use cases.
Signature verification + revocation-status interpretation are
sketched as follow-up work.

Why this tool: generic on-chain primitive. BV-7X publishes daily
signal attestations users can verify with this tool. Other use cases
include reading agent reputation attestations, KYC results, and
trust-score certificates from any EAS-using protocol.
"""

from __future__ import annotations

from typing import Any

from clawmes.lib.params import ParamError, read_enum, read_int, read_str
from clawmes.lib.tool_result import error_result, json_result
from clawmes.services.rpc import RpcError, get_rpc_service
from clawmes.tools.registry import read_tool, register_with_ctx

# EAS singleton on Base mainnet. See https://docs.attest.org/docs/quick--start/contracts
_EAS_BASE_MAINNET = "0x4200000000000000000000000000000000000021"
_DEFAULT_CHAIN_ID = 8453

# keccak256("getAttestation(bytes32)")[:4]
_SELECTOR_GET_ATTESTATION = bytes.fromhex("a3112a64")

# Attestation struct return type, per the EAS interface:
#   struct Attestation {
#       bytes32 uid;
#       bytes32 schema;
#       uint64 time;
#       uint64 expirationTime;
#       uint64 revocationTime;
#       bytes32 refUID;
#       address recipient;
#       address attester;
#       bool revocable;
#       bytes data;
#   }
_ATTESTATION_TYPE = "(bytes32,bytes32,uint64,uint64,uint64,bytes32,address,address,bool,bytes)"

_VALID_ACTIONS = ["get", "decode_data"]


_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": _VALID_ACTIONS,
            "description": (
                "get = read the full attestation struct by UID. "
                "decode_data = decode the raw bytes payload of a "
                "previously-fetched attestation against a typed schema "
                "(callers supply the type string)."
            ),
        },
        "uid": {
            "type": "string",
            "description": (
                "EAS attestation UID (32-byte hex, 0x-prefixed). Required for action=get."
            ),
        },
        "chain_id": {
            "type": "integer",
            "description": (
                "Chain ID. Default 8453 (Base mainnet). EAS is deployed "
                "on most major L2s — pass the appropriate chain id."
            ),
        },
        "eas_address": {
            "type": "string",
            "description": (
                "Override the EAS contract address. Default is Base's "
                "canonical 0x4200...0021. Pass a different address for "
                "chains where the canonical address differs."
            ),
        },
        "data_hex": {
            "type": "string",
            "description": (
                "Hex-encoded raw attestation data (action=decode_data). Returned by action=get."
            ),
        },
        "schema_types": {
            "type": "string",
            "description": (
                "ABI type string for the data payload (action=decode_data). "
                'e.g. "uint8,string,bool" or "(address,uint256)" for a tuple.'
            ),
        },
    },
    "required": ["action"],
}


@read_tool(
    name="eas_attestation",
    toolset="clawmes-intelligence",
    description=(
        "Read EAS attestations on Base (and other L2s). "
        "action=get fetches an attestation by 32-byte UID, decoded into "
        "the canonical Attestation struct (uid, schema, time, "
        "recipient, attester, data, etc.). action=decode_data parses "
        "the raw bytes payload against a caller-supplied schema. "
        "Generic on-chain primitive \u2014 useful for BV-7X signal "
        "attestations, trust-score certificates, KYC results, and any "
        "EAS-using protocol."
    ),
    schema=_SCHEMA,
    emoji="\U0001f4dc",
)
def eas_attestation(args: dict[str, Any], **kwargs: Any) -> str:
    try:
        action = read_enum(args, "action", _VALID_ACTIONS, required=True)
    except ParamError as exc:
        return error_result(str(exc), code="param_error")

    if action == "get":
        return _handle_get(args)
    # action == "decode_data" — the only remaining option.
    return _handle_decode_data(args)


def _handle_get(args: dict[str, Any]) -> str:
    try:
        uid_str = read_str(args, "uid", required=True)
        chain_id = read_int(args, "chain_id") or _DEFAULT_CHAIN_ID
    except ParamError as exc:
        return error_result(str(exc), code="param_error")
    assert uid_str is not None

    uid_hex = uid_str.strip()
    if uid_hex.startswith("0x") or uid_hex.startswith("0X"):
        uid_hex = uid_hex[2:]
    try:
        uid_bytes = bytes.fromhex(uid_hex)
    except ValueError:
        return error_result(f"uid is not valid hex: {uid_str!r}", code="param_error")
    if len(uid_bytes) != 32:
        return error_result(
            f"uid must decode to 32 bytes, got {len(uid_bytes)}",
            code="param_error",
        )

    eas_address = read_str(args, "eas_address") or _EAS_BASE_MAINNET

    try:
        from eth_abi import decode as abi_decode
        from eth_abi import encode as abi_encode
    except ImportError:  # pragma: no cover — eth-abi is a transitive web3 dep
        return error_result(
            "eth-abi is not installed (transitive web3 dep). Install "
            "web3>=7.0 or eth-abi directly.",
            code="dependency_error",
        )

    encoded_uid = abi_encode(["bytes32"], [uid_bytes])
    call_data = "0x" + (_SELECTOR_GET_ATTESTATION + encoded_uid).hex()

    rpc = get_rpc_service()
    try:
        result_hex = rpc.eth_call(to=eas_address, data=call_data, chain_id=chain_id)
    except RpcError as exc:
        return error_result(f"RPC error: {exc.message}", code="rpc_error")

    raw = result_hex
    if isinstance(raw, str) and raw.startswith("0x"):
        raw = raw[2:]
    if not raw:
        return error_result(
            f"EAS returned empty result for uid {uid_str!r} \u2014 the "
            "attestation may not exist on this chain.",
            code="not_found",
        )

    try:
        decoded = abi_decode([_ATTESTATION_TYPE], bytes.fromhex(raw))
    except Exception as exc:  # noqa: BLE001 — eth-abi can raise various
        return error_result(
            f"failed to decode EAS response: {exc}",
            code="api_error",
        )
    attestation_tuple = decoded[0]

    # If the returned uid is all zeros, the attestation doesn't exist.
    returned_uid = attestation_tuple[0]
    if returned_uid == b"\x00" * 32:
        return error_result(
            f"No attestation found for uid {uid_str!r} on chain {chain_id}.",
            code="not_found",
        )

    payload = _attestation_to_dict(attestation_tuple)
    return json_result(
        payload,
        summary=_format_attestation_summary(payload),
    )


def _handle_decode_data(args: dict[str, Any]) -> str:
    try:
        data_hex = read_str(args, "data_hex", required=True)
        schema_types = read_str(args, "schema_types", required=True)
    except ParamError as exc:
        return error_result(str(exc), code="param_error")
    assert data_hex is not None and schema_types is not None

    raw = data_hex.strip()
    if raw.startswith("0x") or raw.startswith("0X"):
        raw = raw[2:]
    try:
        data_bytes = bytes.fromhex(raw)
    except ValueError:
        return error_result("data_hex is not valid hex", code="param_error")

    try:
        from eth_abi import decode as abi_decode
    except ImportError:  # pragma: no cover
        return error_result("eth-abi is not installed", code="dependency_error")

    # The schema may be a single type or comma-separated types.
    # Commas INSIDE parentheses (tuple types like "(uint8,bool)") must
    # not split — track paren depth.
    types = _split_types(schema_types)
    if not types:
        return error_result("schema_types must list at least one ABI type", code="param_error")
    try:
        values = abi_decode(types, data_bytes)
    except Exception as exc:  # noqa: BLE001
        return error_result(
            f"data decode failed against schema {schema_types!r}: {exc}",
            code="api_error",
        )

    return json_result(
        {
            "schema_types": schema_types,
            "values": _stringify_for_json(values),
        },
        summary=f"decoded {len(types)} field(s) from {len(data_bytes)} bytes",
    )


def _attestation_to_dict(t: tuple) -> dict[str, Any]:
    return {
        "uid": "0x" + t[0].hex(),
        "schema": "0x" + t[1].hex(),
        "time": int(t[2]),
        "expirationTime": int(t[3]),
        "revocationTime": int(t[4]),
        "refUID": "0x" + t[5].hex(),
        "recipient": t[6],
        "attester": t[7],
        "revocable": bool(t[8]),
        "data_hex": "0x" + t[9].hex(),
        "is_revoked": int(t[4]) > 0,
        "is_expired": int(t[3]) > 0 and int(t[3]) < _now_seconds(),
    }


def _format_attestation_summary(p: dict[str, Any]) -> str:
    lines = [
        f"EAS attestation {p['uid'][:18]}...",
        f"  Schema:    {p['schema'][:18]}...",
        f"  Attester:  {p['attester']}",
        f"  Recipient: {p['recipient']}",
        f"  Created:   epoch {p['time']}",
    ]
    if p["is_revoked"]:
        lines.append(f"  REVOKED at epoch {p['revocationTime']}")
    if p["is_expired"]:
        lines.append(f"  EXPIRED at epoch {p['expirationTime']}")
    data_hex = p["data_hex"]
    if data_hex and data_hex != "0x":
        lines.append(f"  Data:      {data_hex[:42]}{'...' if len(data_hex) > 42 else ''}")
    return "\n".join(lines)


def _split_types(s: str) -> list[str]:
    """Comma-split an ABI type list, respecting parenthesized tuples.

    ``"uint8,string"`` → ``["uint8", "string"]``.
    ``"(uint8,bool),bytes32"`` → ``["(uint8,bool)", "bytes32"]``.
    """
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for char in s:
        if char == "(":
            depth += 1
            current.append(char)
        elif char == ")":
            depth -= 1
            current.append(char)
        elif char == "," and depth == 0:
            stripped = "".join(current).strip()
            if stripped:
                parts.append(stripped)
            current = []
        else:
            current.append(char)
    final = "".join(current).strip()
    if final:
        parts.append(final)
    return parts


def _stringify_for_json(values: tuple) -> list[Any]:
    """Convert decoded ABI values into JSON-safe Python types."""
    out: list[Any] = []
    for v in values:
        if isinstance(v, bytes):
            out.append("0x" + v.hex())
        elif isinstance(v, tuple):
            out.append(_stringify_for_json(v))
        elif isinstance(v, int):
            out.append(v)
        else:
            out.append(v)
    return out


def _now_seconds() -> int:
    import time

    return int(time.time())


def register(ctx) -> None:
    register_with_ctx(ctx, eas_attestation)
