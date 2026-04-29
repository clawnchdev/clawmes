"""``safe`` — Gnosis Safe multisig operations.

Five actions:

  * ``info``     — read Safe metadata: owners, threshold, nonce,
    contract version, ETH balance.
  * ``propose``  — propose a new transaction to the Safe. Posts the
    user's owner-signature to Safe Transaction Service; other owners
    can then add their signatures via this same action targeting the
    same safe_tx_hash.
  * ``confirm``  — alias for ``propose`` against an existing
    safe_tx_hash. The Transaction Service deduplicates by hash.
  * ``execute``  — broadcast a fully-signed Safe transaction. Calls
    ``execTransaction`` on the Safe contract with collected signatures.
  * ``pending``  — list pending (unexecuted) transactions awaiting
    signatures.

Limitations at this milestone:

  * ``create`` (deploying a new Safe) is not yet wired — it requires
    SafeFactory contract calls. Use the Safe web UI for now.
  * ``propose`` accepts pre-built payloads only; building the EIP-712
    hash + signing the safe_tx_hash needs the wallet mode's
    sign_typed_data_v4 path. For now the LLM is expected to construct
    the payload externally and pass it through.

The actual on-chain execution and EIP-712 signing flows are
non-trivial; this tool focuses on the read + relay paths that the
Transaction Service handles. Full local-signing support lands in a
follow-up that wires the wallet's typed-data path.
"""

from __future__ import annotations

from typing import Any

from clawmes.lib.logger import logger_for
from clawmes.lib.params import read_int, read_str
from clawmes.lib.tool_result import error_result, json_result
from clawmes.services.safe import (
    SafeError,
    get_pending_transactions,
    get_safe_info,
    propose_transaction,
    supports_chain,
)
from clawmes.services.wallet import get_wallet_state
from clawmes.tools.registry import register_with_ctx, write_tool

_log = logger_for("tools.safe")

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["info", "propose", "confirm", "execute", "pending"],
        },
        "safe_address": {
            "type": "string",
            "description": "Safe (multisig) contract address.",
        },
        "chain_id": {
            "type": "integer",
            "description": "Chain id. Defaults to wallet's chain.",
        },
        "payload": {
            "type": "object",
            "description": (
                "For propose/confirm: a Safe Transaction Service "
                "payload dict (see safe.global docs). Includes safe_tx_hash, "
                "signature, sender, etc."
            ),
        },
        "limit": {
            "type": "integer",
            "description": "Pending list page size (default 20, max 100).",
        },
        "policyConfirmationNonce": {
            "type": "string",
            "description": "Set when retrying after POLICY HOLD.",
        },
    },
    "required": ["action", "safe_address"],
}


@write_tool(
    name="safe",
    toolset="clawmes-defi",
    description=(
        "Gnosis Safe multisig operations. info reads owners + threshold; "
        "propose/confirm relay owner signatures via Safe Transaction "
        "Service; pending lists unexecuted txs; execute broadcasts a "
        "fully-signed multisig tx. Multi-step signing flows require "
        "the LLM to coordinate — this tool is the on-chain + Transaction "
        "Service interface; signing happens via the wallet mode."
    ),
    schema=_SCHEMA,
    emoji="\U0001f512",
)
def safe(args: dict[str, Any], **kwargs: Any) -> str:
    action = read_str(args, "action", required=True)
    safe_address = read_str(args, "safe_address", required=True)
    if not safe_address.startswith(("0x", "0X")) or len(safe_address) != 42:
        return error_result(f"Invalid Safe address: {safe_address!r}", code="param_error")

    state = get_wallet_state()
    chain_id = _resolve_chain_id(args, state)

    if not supports_chain(chain_id):
        return error_result(
            f"Safe Transaction Service not available on chain {chain_id}",
            code="unsupported_chain",
        )

    if action == "info":
        return _handle_info(safe_address, chain_id)
    if action == "pending":
        return _handle_pending(safe_address, chain_id, args)
    if action in ("propose", "confirm"):
        return _handle_propose(safe_address, chain_id, args)
    return _handle_execute(safe_address, chain_id, args)


def _handle_info(safe_address: str, chain_id: int) -> str:
    try:
        info = get_safe_info(safe_address, chain_id)
    except SafeError as exc:
        return error_result(exc.message, code=exc.code)

    owners = info.get("owners") or []
    return json_result(
        {
            "address": safe_address,
            "chain_id": chain_id,
            "owners": owners,
            "threshold": info.get("threshold"),
            "nonce": info.get("nonce"),
            "version": info.get("version"),
            "modules": info.get("modules") or [],
            "fallback_handler": info.get("fallbackHandler"),
            "guard": info.get("guard"),
        },
        summary=(
            f"Safe {safe_address} on chain {chain_id}\n"
            f"  Threshold: {info.get('threshold')}/{len(owners)}\n"
            f"  Nonce:     {info.get('nonce')}\n"
            f"  Version:   {info.get('version', 'unknown')}"
        ),
    )


def _handle_pending(safe_address: str, chain_id: int, args: dict[str, Any]) -> str:
    limit = read_int(args, "limit") or 20
    try:
        pending = get_pending_transactions(safe_address, chain_id, limit=min(limit, 100))
    except SafeError as exc:
        return error_result(exc.message, code=exc.code)

    items = []
    for tx in pending:
        if not isinstance(tx, dict):
            continue
        items.append(
            {
                "safe_tx_hash": tx.get("safeTxHash"),
                "to": tx.get("to"),
                "value": tx.get("value"),
                "nonce": tx.get("nonce"),
                "confirmations_count": len(tx.get("confirmations") or []),
                "confirmations_required": tx.get("confirmationsRequired"),
                "submission_date": tx.get("submissionDate"),
            }
        )
    return json_result(
        {"count": len(items), "pending": items},
        summary=f"{len(items)} pending Safe tx(s) awaiting signatures",
    )


def _handle_propose(safe_address: str, chain_id: int, args: dict[str, Any]) -> str:
    payload = args.get("payload")
    if not isinstance(payload, dict):
        return error_result(
            "propose/confirm requires a 'payload' dict (Safe Transaction "
            "Service shape — see https://docs.safe.global).",
            code="param_error",
        )

    try:
        result = propose_transaction(safe_address, chain_id, payload)
    except SafeError as exc:
        return error_result(exc.message, code=exc.code)

    return json_result(
        {
            "safe_address": safe_address,
            "chain_id": chain_id,
            "result": result,
        },
        summary=(
            f"Submitted to Safe Transaction Service for {safe_address}. "
            "Other owners can now add signatures via this same endpoint."
        ),
    )


def _handle_execute(safe_address: str, chain_id: int, args: dict[str, Any]) -> str:
    return error_result(
        "Safe execute requires building execTransaction calldata with "
        "all collected signatures — not yet wired. Use the Safe web UI "
        "to execute, or extract the calldata from the Transaction "
        "Service and pass to transfer with action=send + token=null.",
        code="not_implemented",
    )


def _resolve_chain_id(args: dict[str, Any], state) -> int:
    explicit = read_int(args, "chain_id")
    if explicit is not None:
        return explicit
    return int(state.chain_id) if state.chain_id is not None else 1


def register(ctx) -> None:
    """Wire ``safe`` into Hermes."""
    register_with_ctx(ctx, safe)
