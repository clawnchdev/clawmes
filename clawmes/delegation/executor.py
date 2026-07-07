"""Delegation executor — bridge the write-tool gate to on-chain redemption.

When a write tool is about to run and the app-layer policy said ``allow``,
:func:`try_delegation_execution` checks whether a signed delegation covers the
action and, if so, redeems it on-chain (agent-signed) instead of running the
tool's own send path. This is stage 3 of the ``@write_tool`` gate.

Extraction is deliberately conservative: only tools with a registered
extractor and unambiguous ``(target, value, callData)`` are eligible. Anything
else returns a *skip* and the gate falls through to the normal handler.

Outcome contract (consumed by :mod:`clawmes.tools.registry`):
  * ``executed=True``            → handler is skipped; return the tx result.
  * ``error`` set                → redemption was attempted and refused
    (caveat violation / revoked / RPC). The gate FAILS CLOSED — it does not
    silently run the tool's own send path, which would bypass the on-chain
    limit the user signed.
  * ``skip_reason`` set          → delegation not applicable; fall through.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from clawmes.delegation import encoding as E
from clawmes.delegation.store import get_delegation_store
from clawmes.delegation.types import (
    ZERO_ADDRESS,
    DelegationRecord,
    ExecutionAction,
)
from clawmes.lib.logger import logger_for
from clawmes.policy.types import ActionContext

_log = logger_for("delegation.executor")

_KILL_SWITCH = "CLAWMES_DELEGATION_DISABLED"

# Redemption rate limiter — prevent gas-burning loops when redemption keeps
# reverting. Max attempts per (user, tool) per window; cleared on success.
_RATE_WINDOW_S = 60.0
_RATE_MAX = 3
_attempts: dict[str, list[float]] = {}


@dataclass
class DelegationExecutionResult:
    executed: bool
    tx_hash: str | None = None
    chain_id: int | None = None
    skip_reason: str | None = None
    error: str | None = None


# ─── action extractors ──────────────────────────────────────────────────
#
# Each extractor maps a tool's args → an ExecutionAction, or None if it can't
# (missing/invalid args, unknown ERC-20 decimals, unsupported sub-action).
# ERC-20 amounts use the non-blocking decimals cache: an unknown token yields
# None → the gate falls through to the handler (which does the strict fetch).

ActionExtractor = Callable[[dict[str, Any], "ExtractorContext"], "ExecutionAction | None"]


@dataclass
class ExtractorContext:
    chain_id: int
    delegator: str | None = None


def _is_addr(value: Any) -> bool:
    return (
        isinstance(value, str)
        and value.startswith("0x")
        and len(value) == 42
        and _is_hex(value[2:])
    )


def _is_hex(s: str) -> bool:
    try:
        int(s, 16)
        return True
    except (ValueError, TypeError):
        return False


def _parse_amount(amount: Any, decimals: int) -> int | None:
    from clawmes.lib.decimals import to_base_units

    try:
        value = to_base_units(str(amount), decimals)
    except (ValueError, ArithmeticError, TypeError):
        return None
    return value if value > 0 else None


def _peek_decimals(token: str, chain_id: int) -> int | None:
    from clawmes.services.token_decimals import get_token_decimals_service

    return get_token_decimals_service().peek(token, chain_id)


def _extract_transfer(args: dict[str, Any], ctx: ExtractorContext) -> ExecutionAction | None:
    if args.get("action") != "send":
        return None
    to = args.get("to")
    amount = args.get("amount")
    token = args.get("token")
    if not _is_addr(to) or amount is None:
        return None
    to_addr = str(to)
    if token and _is_addr(token):
        token_addr = str(token)
        decimals = _peek_decimals(token_addr, ctx.chain_id)
        if decimals is None:
            return None
        base = _parse_amount(amount, decimals)
        if base is None:
            return None
        return ExecutionAction(
            target=token_addr, value=0, call_data=E.encode_erc20_transfer(to_addr, base)
        )
    if not token or str(token).lower() in ("eth", "native"):
        wei = _parse_amount(amount, 18)
        if wei is None:
            return None
        return ExecutionAction(target=to_addr, value=wei, call_data="0x")
    return None


def _extract_approvals(args: dict[str, Any], ctx: ExtractorContext) -> ExecutionAction | None:
    # Only the revoke path (approve(spender, 0)) is safe to redeem — granting
    # a fresh allowance through delegation is intentionally not supported.
    if args.get("action") != "revoke":
        return None
    token = args.get("token")
    spender = args.get("spender")
    if not _is_addr(token) or not _is_addr(spender):
        return None
    return ExecutionAction(
        target=str(token), value=0, call_data=E.encode_erc20_approve(str(spender), 0)
    )


def _extract_nft(args: dict[str, Any], ctx: ExtractorContext) -> ExecutionAction | None:
    if args.get("action") != "transfer":
        return None
    contract = args.get("contract")
    to = args.get("to")
    token_id = args.get("token_id")
    if not _is_addr(contract) or not _is_addr(to) or token_id is None:
        return None
    try:
        tid = int(str(token_id))
    except (ValueError, TypeError):
        return None
    # `from` is the delegator; filled from a zero placeholder at redemption.
    call_data = E.encode_erc721_transfer_from(ZERO_ADDRESS, str(to), tid)
    return ExecutionAction(target=str(contract), value=0, call_data=call_data)


_EXTRACTORS: dict[str, ActionExtractor] = {
    "transfer": _extract_transfer,
    "approvals": _extract_approvals,
    "nft": _extract_nft,
}


def register_extractor(tool_name: str, extractor: ActionExtractor) -> None:
    """Register an action extractor for ``tool_name`` (extension seam)."""
    _EXTRACTORS[tool_name] = extractor


def supported_tools() -> list[str]:
    return sorted(_EXTRACTORS)


# ─── rate limiter ───────────────────────────────────────────────────────


def _rate_key(user_id: str, tool: str) -> str:
    return f"{user_id}:{tool}"


def _rate_limited(user_id: str, tool: str) -> bool:
    key = _rate_key(user_id, tool)
    now = time.monotonic()
    recent = [t for t in _attempts.get(key, []) if now - t < _RATE_WINDOW_S]
    _attempts[key] = recent
    return len(recent) >= _RATE_MAX


def _record_attempt(user_id: str, tool: str) -> None:
    key = _rate_key(user_id, tool)
    now = time.monotonic()
    recent = [t for t in _attempts.get(key, []) if now - t < _RATE_WINDOW_S]
    recent.append(now)
    _attempts[key] = recent


def _clear_attempts(user_id: str, tool: str) -> None:
    _attempts.pop(_rate_key(user_id, tool), None)


def reset_rate_limiter() -> None:
    _attempts.clear()


# ─── matching ───────────────────────────────────────────────────────────


def _scope_covers(record: DelegationRecord, tool_name: str) -> bool:
    return not record.tools or tool_name in record.tools


def find_matching_delegation(ctx: ActionContext) -> DelegationRecord | None:
    """First redeemable delegation whose scope + chain cover ``ctx``."""
    for record in get_delegation_store().list_records():
        if not record.is_redeemable():
            continue
        if not _scope_covers(record, ctx.tool_name):
            continue
        if ctx.chain_id is not None and record.chain_id != ctx.chain_id:
            continue
        return record
    return None


def _fill_delegator(call_data: str, delegator: str) -> str:
    """Replace a 32-byte zero placeholder with the delegator address."""
    zero_word = "0" * 64
    padded = delegator[2:].lower().rjust(64, "0")
    if zero_word in call_data:
        return call_data.replace(zero_word, padded)
    return call_data


def _rpc():
    from clawmes.services.rpc import get_rpc_service

    return get_rpc_service()


# ─── entry point ────────────────────────────────────────────────────────


def try_delegation_execution(
    action_ctx: ActionContext, tool_args: dict[str, Any]
) -> DelegationExecutionResult:
    """Attempt to satisfy a write action via on-chain delegation redemption.

    See the module docstring for the outcome contract.
    """
    if os.environ.get(_KILL_SWITCH):
        return DelegationExecutionResult(False, skip_reason="delegation disabled via env")

    extractor = _EXTRACTORS.get(action_ctx.tool_name)
    if extractor is None:
        return DelegationExecutionResult(
            False, skip_reason=f"{action_ctx.tool_name} has no delegation extractor"
        )

    record = find_matching_delegation(action_ctx)
    if record is None:
        return DelegationExecutionResult(
            False, skip_reason="no matching delegation for this action"
        )

    chain_id = action_ctx.chain_id or record.chain_id
    ctx = ExtractorContext(chain_id=chain_id, delegator=record.delegation.delegator)
    action = extractor(tool_args, ctx)
    if action is None:
        return DelegationExecutionResult(
            False, skip_reason="could not extract an on-chain action from args"
        )

    # Fill delegator placeholder (e.g. NFT `from`).
    if len(action.call_data) > 10:
        action = ExecutionAction(
            target=action.target,
            value=action.value,
            call_data=_fill_delegator(action.call_data, record.delegation.delegator),
        )

    # Client-side expiry check.
    if record.expires_at:
        try:
            expiry = datetime.fromisoformat(record.expires_at).timestamp()
            if time.time() > expiry:
                record.status = "expired"
                get_delegation_store().save(record)
                return DelegationExecutionResult(
                    False, error="delegation has expired — create a new one"
                )
        except ValueError:
            pass

    # Delegator must be a smart account (redeemDelegations calls
    # executeFromExecutor on it). Plain EOAs revert; skip so the gate falls
    # through to the normal handler rather than failing closed.
    try:
        code = _rpc().get_code(record.delegation.delegator, chain_id)
        if code in ("", "0x", "0x0"):
            return DelegationExecutionResult(
                False,
                skip_reason=(
                    f"delegator {record.delegation.delegator} is a plain EOA — "
                    "run /delegate upgrade to enable on-chain enforcement"
                ),
            )
    except Exception as exc:  # noqa: BLE001 — non-fatal; simulation catches it
        _log.debug("getCode check failed: %s", exc)

    if _rate_limited(action_ctx.user_id, action_ctx.tool_name):
        return DelegationExecutionResult(
            False, error="delegation redemption rate-limited; wait before retrying"
        )

    from clawmes.delegation.service import DelegationError, redeem

    _record_attempt(action_ctx.user_id, action_ctx.tool_name)
    try:
        result = redeem(record, action)
    except DelegationError as exc:
        return DelegationExecutionResult(False, error=str(exc))
    except Exception as exc:  # noqa: BLE001
        return DelegationExecutionResult(False, error=f"delegation error: {exc}")

    _clear_attempts(action_ctx.user_id, action_ctx.tool_name)
    return DelegationExecutionResult(True, tx_hash=result.tx_hash, chain_id=result.chain_id)
