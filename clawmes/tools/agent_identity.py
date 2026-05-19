"""``agent_identity`` — LLM-callable surface for the IdentityService.

Five actions:

  * ``show``   — return the active DID identity (did:key + public key
    hex + creation timestamp). Empty result when no identity is set.
  * ``create`` — generate a fresh ed25519 keypair. Refuses to
    overwrite an existing identity unless ``overwrite=true`` is
    passed (parallels ``/create_wallet`` 's posture for the local
    keystore).
  * ``sign``   — sign a UTF-8 string ``message`` (or hex-encoded
    ``message_hex``) with the agent's private key. Returns the
    64-byte signature as hex.
  * ``verify`` — verify a signature against an arbitrary public key.
    No identity required — static crypto primitive.
  * ``did_encode`` — convert a raw public-key hex string to a
    ``did:key`` identifier. Useful when an LLM has a peer's pubkey
    and wants to render their DID.

Read-only by design: the tool reads + computes, but the actual
keypair state lives in :class:`IdentityService`. The decorator is
``@read_tool`` because the tool doesn't mutate on-chain state — the
in-memory keypair mutation is fine to leave unguarded (mirrors the
posture of ``policy_manage`` for managed config state).
"""

from __future__ import annotations

from typing import Any

from clawmes.lib.params import ParamError, read_bool, read_enum, read_str
from clawmes.lib.tool_result import error_result, json_result
from clawmes.services.identity import encode_did_key, get_identity_service
from clawmes.tools.registry import read_tool, register_with_ctx

_VALID_ACTIONS = ["show", "create", "sign", "verify", "did_encode"]


_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": _VALID_ACTIONS,
        },
        "overwrite": {
            "type": "boolean",
            "description": (
                "Force-replace an existing identity (action=create only). "
                "Default false — preserves the existing identity if one "
                "is set."
            ),
        },
        "message": {
            "type": "string",
            "description": (
                "UTF-8 string to sign or verify. Mutually exclusive with "
                "message_hex; if both are set, message_hex wins."
            ),
        },
        "message_hex": {
            "type": "string",
            "description": "Hex-encoded message bytes to sign or verify.",
        },
        "signature_hex": {
            "type": "string",
            "description": "Hex-encoded 64-byte signature (action=verify).",
        },
        "public_key_hex": {
            "type": "string",
            "description": (
                "Hex-encoded 32-byte ed25519 public key. Required for "
                "action=verify and action=did_encode."
            ),
        },
    },
    "required": ["action"],
}


@read_tool(
    name="agent_identity",
    toolset="clawmes-identity",
    description=(
        "Manage the agent's ed25519 cryptographic identity (did:key). "
        "show / create / sign / verify / did_encode. Separate from the "
        "wallet identity — the DID signs protocol messages (MCP, PRs, "
        "capability delegations) while the wallet signs on-chain "
        "transactions. v1 is session-only — restart loses the keypair."
    ),
    schema=_SCHEMA,
    emoji="\U0001f194",
)
def agent_identity(args: dict[str, Any], **kwargs: Any) -> str:
    try:
        action = read_enum(args, "action", _VALID_ACTIONS, required=True)
    except ParamError as exc:
        return error_result(str(exc), code="param_error")

    if action == "show":
        return _handle_show()
    if action == "create":
        return _handle_create(args)
    if action == "sign":
        return _handle_sign(args)
    if action == "verify":
        return _handle_verify(args)
    # action == "did_encode" — the only remaining valid option.
    return _handle_did_encode(args)


# --- handlers -----------------------------------------------------------


def _handle_show() -> str:
    summary = get_identity_service().show()
    if not summary:
        return json_result(
            {"status": "no_identity"},
            summary=(
                "No agent identity set. Run agent_identity action=create "
                "(or /identity create) to generate one."
            ),
        )
    return json_result(
        {"status": "active", **summary},
        summary=(
            f"Agent identity:\n"
            f"  DID:        {summary['did']}\n"
            f"  Public key: {summary['public_key_hex']}\n"
            f"  Created:    epoch {int(summary['created_at'])}"
        ),
    )


def _handle_create(args: dict[str, Any]) -> str:
    svc = get_identity_service()
    overwrite = read_bool(args, "overwrite", default=False)
    if svc.has_identity() and not overwrite:
        existing = svc.show()
        return error_result(
            f"Agent identity already exists ({existing['did']}). Pass "
            "overwrite=true to replace it, or use action=show to see "
            "the current one.",
            code="conflict",
        )
    summary = svc.generate()
    return json_result(
        {"status": "created", **summary},
        summary=(
            f"Generated agent identity:\n"
            f"  DID:        {summary['did']}\n"
            f"  Public key: {summary['public_key_hex']}\n"
            "(In-memory only — restart loses the keypair. Persistence "
            "lands in a follow-up.)"
        ),
    )


def _handle_sign(args: dict[str, Any]) -> str:
    try:
        message = _read_message(args)
    except ParamError as exc:
        return error_result(str(exc), code="param_error")
    try:
        signature = get_identity_service().sign(message)
    except RuntimeError as exc:
        return error_result(str(exc), code="no_identity")
    svc = get_identity_service()
    pubkey_hex = svc.public_key_hex()
    return json_result(
        {
            "signature_hex": signature.hex(),
            "public_key_hex": pubkey_hex,
            "did": svc.show().get("did"),
        },
        summary=f"signed {len(message)} byte(s); signature = {signature.hex()[:32]}...",
    )


def _handle_verify(args: dict[str, Any]) -> str:
    try:
        message = _read_message(args)
        public_key_hex = read_str(args, "public_key_hex", required=True)
        signature_hex = read_str(args, "signature_hex", required=True)
    except ParamError as exc:
        return error_result(str(exc), code="param_error")
    assert public_key_hex is not None and signature_hex is not None
    try:
        signature = bytes.fromhex(signature_hex.strip())
    except ValueError:
        return error_result(
            "signature_hex is not valid hex",
            code="param_error",
        )
    valid = get_identity_service().verify(public_key_hex, message, signature)
    return json_result(
        {
            "valid": valid,
            "public_key_hex": public_key_hex.strip(),
            "message_bytes": len(message),
        },
        summary=("signature VALID" if valid else "signature INVALID"),
    )


def _handle_did_encode(args: dict[str, Any]) -> str:
    try:
        public_key_hex = read_str(args, "public_key_hex", required=True)
    except ParamError as exc:
        return error_result(str(exc), code="param_error")
    assert public_key_hex is not None
    try:
        pubkey_bytes = bytes.fromhex(public_key_hex.strip())
    except ValueError:
        return error_result(
            "public_key_hex is not valid hex",
            code="param_error",
        )
    try:
        did = encode_did_key(pubkey_bytes)
    except ValueError as exc:
        return error_result(str(exc), code="param_error")
    return json_result(
        {"did": did, "public_key_hex": public_key_hex.strip()},
        summary=did,
    )


def _read_message(args: dict[str, Any]) -> bytes:
    """Read the message-to-sign-or-verify from either ``message_hex``
    (preferred) or ``message`` (UTF-8). Raises :class:`ParamError`
    when neither is provided.
    """
    hex_str = read_str(args, "message_hex")
    if hex_str:
        try:
            return bytes.fromhex(hex_str.strip())
        except ValueError as exc:
            raise ParamError(f"message_hex is not valid hex: {exc}") from exc
    text = read_str(args, "message")
    if text is not None:
        return text.encode("utf-8")
    raise ParamError("provide either message (UTF-8) or message_hex")


def register(ctx) -> None:
    register_with_ctx(ctx, agent_identity)
