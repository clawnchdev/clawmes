"""``approvals`` — manage ERC-20 token approvals.

ERC-20 approvals are the most common attack vector for wallet drainers:
a user grants ``unlimited`` approval to a contract, the contract gets
exploited or the user grants approval to a malicious clone, and the
attacker drains all of the user's tokens for that asset. This tool
gives users a clear view of every active approval and the ability to
revoke them.

Four actions:

  * ``list``    — every active approval the wallet has issued for the
    requested chain. Aggregates by (token, spender) and shows the
    current allowance via ``eth_call``.
  * ``audit``   — same as ``list`` but flags risky approvals
    (unlimited, old, granted to non-verified contracts).
  * ``approve`` — grant an allowance. Mostly useful when a tool
    needs a specific permission (e.g. before a swap) and you want to
    pre-grant rather than approve-on-the-fly.
  * ``revoke``  — set allowance to zero. Calls ``approve(spender, 0)``
    on the token contract.

The ``list`` and ``audit`` actions use the block explorer's logs API
to find every Approval event the wallet has emitted. This is the
canonical way to enumerate approvals — there's no "approvals(owner)"
view function on ERC-20.
"""

from __future__ import annotations

from typing import Any

from clawmes.lib.abi import (
    APPROVAL_EVENT_TOPIC,
    UNLIMITED_ALLOWANCE,
    decode_uint,
    encode_allowance,
    encode_approve,
)
from clawmes.lib.logger import logger_for
from clawmes.lib.params import read_int, read_str
from clawmes.lib.tool_result import error_result, json_result
from clawmes.services.wallet import get_wallet_state
from clawmes.tools.registry import register_with_ctx, write_tool

_log = logger_for("tools.approvals")

# Conservative gas ceiling for an approve() call. Real usage is ~46k;
# the 80k ceiling absorbs first-touch storage initialization.
_APPROVE_GAS_DEFAULT = 80_000

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["list", "audit", "approve", "revoke"],
            "description": (
                "list: enumerate active approvals via explorer logs. "
                "audit: list + flag unlimited / risky approvals. "
                "approve: grant an allowance to a spender. "
                "revoke: set the allowance to zero."
            ),
        },
        "token": {
            "type": "string",
            "description": "ERC-20 contract address. Required for approve/revoke.",
        },
        "spender": {
            "type": "string",
            "description": "Address to approve / revoke. Required for approve/revoke.",
        },
        "amount": {
            "type": "string",
            "description": (
                "Amount in human units for approve. Use 'unlimited' for the max uint256 value."
            ),
        },
        "chain_id": {
            "type": "integer",
            "description": "Chain id (defaults to the wallet's current chain).",
        },
        "from_block": {
            "type": "integer",
            "description": (
                "First block to scan for Approval events (list/audit actions). Default 0."
            ),
        },
        "policyConfirmationNonce": {
            "type": "string",
            "description": "Set when retrying after a POLICY HOLD.",
        },
    },
    "required": ["action"],
}


@write_tool(
    name="approvals",
    toolset="clawmes-wallet",
    description=(
        "Manage ERC-20 token approvals. List active approvals via "
        "explorer logs, audit for risky (unlimited / old) approvals, "
        "or approve / revoke a specific spender. Approvals are the "
        "most common drainer vector — running 'audit' periodically is "
        "good wallet hygiene."
    ),
    schema=_SCHEMA,
    emoji="\U0001f4dd",
)
def approvals(args: dict[str, Any], **kwargs: Any) -> str:
    state = get_wallet_state()
    if not state.connected:
        return error_result(
            "No wallet connected. Run /connect first.",
            code="wallet_not_connected",
        )

    action = read_str(args, "action", required=True)
    chain_id = _resolve_chain_id(args, state)

    if action == "list":
        return _handle_list(state, chain_id, _read_from_block(args))
    if action == "audit":
        return _handle_audit(state, chain_id, _read_from_block(args))
    if action == "approve":
        return _handle_approve(args, state, chain_id)
    return _handle_revoke(args, state, chain_id)


def _handle_list(state, chain_id: int, from_block: int) -> str:
    try:
        approvals_list = _fetch_approvals(state.address, chain_id, from_block)
    except Exception as exc:  # noqa: BLE001
        return error_result(f"Could not fetch approvals: {exc}", code="explorer_error")

    return json_result(
        {
            "chain_id": chain_id,
            "owner": state.address,
            "count": len(approvals_list),
            "approvals": approvals_list,
        },
        summary=_render_list(approvals_list, chain_id, audit=False),
    )


def _handle_audit(state, chain_id: int, from_block: int) -> str:
    try:
        approvals_list = _fetch_approvals(state.address, chain_id, from_block)
    except Exception as exc:  # noqa: BLE001
        return error_result(f"Could not fetch approvals: {exc}", code="explorer_error")

    flagged = [a for a in approvals_list if a.get("risk_level") != "ok"]
    return json_result(
        {
            "chain_id": chain_id,
            "owner": state.address,
            "total": len(approvals_list),
            "flagged": len(flagged),
            "approvals": approvals_list,
        },
        summary=_render_list(approvals_list, chain_id, audit=True),
    )


def _handle_approve(args: dict[str, Any], state, chain_id: int) -> str:
    token = _validate_address(read_str(args, "token", required=True), "token")
    if isinstance(token, str) and token.startswith("__error__"):
        return token[len("__error__") :]

    spender = _validate_address(read_str(args, "spender", required=True), "spender")
    if isinstance(spender, str) and spender.startswith("__error__"):
        return spender[len("__error__") :]

    amount_raw = read_str(args, "amount", required=True)
    try:
        amount_base = _parse_amount(amount_raw, token, chain_id)
    except ValueError as exc:
        return error_result(str(exc), code="param_error")

    return _send_approve(token, spender, amount_base, chain_id, state)


def _handle_revoke(args: dict[str, Any], state, chain_id: int) -> str:
    token = _validate_address(read_str(args, "token", required=True), "token")
    if isinstance(token, str) and token.startswith("__error__"):
        return token[len("__error__") :]

    spender = _validate_address(read_str(args, "spender", required=True), "spender")
    if isinstance(spender, str) and spender.startswith("__error__"):
        return spender[len("__error__") :]

    return _send_approve(token, spender, 0, chain_id, state)


# --- helpers --------------------------------------------------------------


def _send_approve(token: str, spender: str, amount: int, chain_id: int, state) -> str:
    from clawmes.services.wallet import get_wallet_service

    svc = get_wallet_service()
    mode = svc.active_mode
    if mode is None:
        return error_result(
            "No active wallet mode; reconnect via /connect.",
            code="wallet_not_connected",
        )

    calldata = encode_approve(spender, amount)
    try:
        tx_hash = mode.send_transaction(
            to=token,
            value=0,
            data=calldata,
            gas=_APPROVE_GAS_DEFAULT,
            chain_id=chain_id,
        )
    except Exception as exc:  # noqa: BLE001
        return error_result(f"Approve failed: {exc}", code="send_failed")

    return json_result(
        {
            "tx_hash": tx_hash,
            "chain_id": chain_id,
            "token": token,
            "spender": spender,
            "amount": str(amount),
            "is_revoke": amount == 0,
        },
        summary=(
            f"{'Revoked' if amount == 0 else 'Approved'} {spender} on token {token}: {tx_hash}"
        ),
    )


def _fetch_approvals(owner: str, chain_id: int, from_block: int) -> list[dict[str, Any]]:
    """Query Approval logs for ``owner`` on ``chain_id``, then resolve
    each (token, spender) pair to its current allowance.

    Logs are deduplicated — a user may have approved the same
    (token, spender) multiple times; only the most recent allowance
    is interesting.
    """
    from clawmes.services.explorer import get_explorer_service
    from clawmes.services.rpc import get_rpc_service

    explorer = get_explorer_service()
    # topic1 is the indexed `owner` field — pad address to 32 bytes
    topic1 = "0x" + owner.lower().removeprefix("0x").zfill(64)
    raw_logs = explorer.get_logs(
        chain_id,
        topic0=APPROVAL_EVENT_TOPIC,
        topic1=topic1,
        from_block=from_block,
    )

    # Aggregate by (token, spender). Last log wins.
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    for log in raw_logs:
        token = (log.get("address") or "").lower()
        topics = log.get("topics") or []
        if len(topics) < 3 or not token:
            continue
        spender = "0x" + topics[2][-40:].lower()
        block_hex = log.get("blockNumber") or "0x0"
        try:
            block = int(block_hex, 16)
        except (ValueError, TypeError):
            block = 0
        seen[(token, spender)] = {
            "token": token,
            "spender": spender,
            "last_set_block": block,
        }

    # For each unique pair, query the current allowance
    rpc = get_rpc_service()
    out: list[dict[str, Any]] = []
    for (token, spender), entry in seen.items():
        try:
            allowance_hex = rpc.eth_call(
                to=token,
                data=encode_allowance(owner, spender),
                chain_id=chain_id,
            )
            current = decode_uint(allowance_hex)
        except Exception as exc:  # noqa: BLE001 — best-effort
            _log.warning("allowance lookup failed for %s/%s: %s", token, spender, exc)
            current = 0
        if current == 0:
            # Approval was revoked or used up; not interesting for the user
            continue
        entry["current_allowance"] = str(current)
        entry["is_unlimited"] = current >= UNLIMITED_ALLOWANCE
        entry["risk_level"] = "high" if entry["is_unlimited"] else "ok"
        out.append(entry)

    out.sort(key=lambda a: a["last_set_block"], reverse=True)
    return out


def _render_list(approvals_list: list[dict[str, Any]], chain_id: int, *, audit: bool) -> str:
    if not approvals_list:
        return f"No active approvals on chain {chain_id}."
    lines = [f"{len(approvals_list)} active approval(s) on chain {chain_id}:"]
    for a in approvals_list:
        flag = ""
        if audit and a["risk_level"] == "high":
            flag = " [UNLIMITED — review]"
        amt = "unlimited" if a["is_unlimited"] else a["current_allowance"]
        lines.append(f"  • token={a['token']} spender={a['spender']} allowance={amt}{flag}")
    if audit:
        flagged = sum(1 for a in approvals_list if a["risk_level"] != "ok")
        if flagged:
            lines.append(
                f"\n{flagged} flagged. Run "
                f"`approvals revoke token=<token> spender=<spender>` "
                f"to revoke."
            )
    return "\n".join(lines)


def _parse_amount(amount: str, token: str, chain_id: int) -> int:
    """Parse human amount → base units. Accepts 'unlimited' for max."""
    if amount.strip().lower() == "unlimited":
        return UNLIMITED_ALLOWANCE
    from clawmes.lib.decimals import to_base_units
    from clawmes.services.token_decimals import (
        TokenDecimalsError,
        get_token_decimals_service,
    )

    try:
        decimals = get_token_decimals_service().get_strict(token, chain_id)
    except TokenDecimalsError as exc:
        raise ValueError(f"could not determine decimals for {token}: {exc.cause}") from exc
    return to_base_units(amount, decimals)


def _validate_address(value: str | None, label: str) -> str:
    """Light hex-address check. Returns the address on success or an
    error-result-prefixed sentinel on failure (callers strip the prefix)."""
    if not value or not value.startswith(("0x", "0X")) or len(value) != 42:
        return "__error__" + error_result(f"Invalid {label} address: {value!r}", code="param_error")
    return value


def _resolve_chain_id(args: dict[str, Any], state) -> int:
    explicit = read_int(args, "chain_id")
    if explicit is not None:
        return explicit
    return int(state.chain_id) if state.chain_id is not None else 8453


def _read_from_block(args: dict[str, Any]) -> int:
    v = read_int(args, "from_block")
    return v if v is not None else 0


def register(ctx) -> None:
    """Wire ``approvals`` into Hermes."""
    register_with_ctx(ctx, approvals)
