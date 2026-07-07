"""Tool registry — decorators that wrap tool handlers with the gating pipeline.

Two decorators:

  * :func:`write_tool` — for any tool that mutates on-chain state. The
    decorated handler runs through the pipeline:

      1. Readonly-mode check (``/safemode`` toggle) — block if active
      2. Policy evaluation (``allow | block | confirm``)
         - ``confirm`` returns a ``POLICY HOLD`` instruction to the LLM
           with a one-time nonce; the LLM relays to the user, gets approval,
           and retries with the nonce in ``policyConfirmationNonce``.
      3. Delegation execution (EIP-7710) — if a signed on-chain delegation
         covers the action, redeem it (agent-signed) and skip the handler.
         Fails CLOSED if redemption is refused (caveat violation / revoked);
         falls through only when delegation is not applicable.
      4. Original handler execution.
      5. Usage counter + ledger record on success.

  * :func:`read_tool` — for read-only tools. Skips the gate; only wraps in
    a generic try/except that converts unexpected exceptions to a clean
    error tool result.

The set of write tools is implicit — ``WRITE_TOOL_NAMES`` is populated as
``@write_tool`` decorators are evaluated at import time. There is no second
source of truth, so additions can never drift.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from typing import Any

from clawmes.lib.params import ParamError
from clawmes.lib.tool_result import error_result
from clawmes.policy.confirm_store import store as _confirm_store
from clawmes.policy.evaluator import evaluate as _evaluate
from clawmes.policy.evaluator import record_invocation as _record_invocation
from clawmes.policy.types import ActionContext
from clawmes.services.mode_service import is_readonly as _is_readonly

WRITE_TOOL_NAMES: set[str] = set()
"""Names of every tool decorated with ``@write_tool``. Populated at import
time; consumed by the policy evaluator and the delegation router."""


# Each module is required to expose ``_clawmes_meta`` so registration in
# ``clawmes.tools.__init__.register_all`` can read schema/description/etc.
# without re-introspecting the function. The shape is intentionally
# duck-typed (no dataclass) because Hermes' ``ctx.register_tool`` is a
# kwargs-based API and we want zero translation overhead.


def write_tool(
    *,
    name: str,
    toolset: str,
    description: str,
    schema: dict[str, Any],
    requires_env: list[str] | None = None,
    emoji: str = "",
) -> Callable[[Callable[..., str]], Callable[..., str]]:
    """Register a write tool wrapped with the full gating pipeline."""
    WRITE_TOOL_NAMES.add(name)

    def decorator(fn: Callable[..., str]) -> Callable[..., str]:
        @functools.wraps(fn)
        def gated(args: dict[str, Any], **kwargs: Any) -> str:
            user_id = str(kwargs.get("user_id") or "default")
            action_ctx = ActionContext(
                tool_name=name,
                args=args,
                user_id=user_id,
                chain_id=_extract_chain_id(args),
                value_wei=_extract_value_wei(args),
            )

            # Stage 1: readonly-mode check
            if _is_readonly(user_id):
                return error_result(
                    "Clawmes is in readonly mode. Use /dangermode to "
                    "enable writes (or /safemode off).",
                    code="readonly_mode",
                )

            # Stage 2: policy evaluation
            decision = _evaluate(action_ctx)
            if decision.kind == "block":
                return error_result(
                    f"Blocked by policy {decision.policy_name!r}: {decision.reason}",
                    code="policy_block",
                )
            if decision.kind == "confirm":
                supplied = (args or {}).get("policyConfirmationNonce")
                if supplied and _confirm_store.consume(action_ctx, str(supplied)):
                    pass  # Confirmed — fall through to stage 4
                else:
                    nonce = _confirm_store.issue(action_ctx)
                    return error_result(
                        f"POLICY HOLD: this {name} requires confirmation "
                        f"per policy {decision.policy_name!r} "
                        f"({decision.reason}). Show the user the action "
                        f"summary and ask for explicit confirmation. Once "
                        f"confirmed, retry this tool call with the parameter "
                        f'policyConfirmationNonce="{nonce}".',
                        code="policy_hold",
                    )

            # Stage 3: on-chain delegation (EIP-7710). If a signed
            # delegation covers this action, redeem it instead of running
            # the tool's own send path. Import lazily so the delegation
            # stack (and its eth-abi/eth-account deps) never touches tool
            # import time, and so tools stay usable if it's unavailable.
            delegation_outcome = _try_delegation(action_ctx, args)
            if delegation_outcome is not None:
                _record_invocation(action_ctx)
                return delegation_outcome

            # Stage 4: actual handler
            try:
                result = fn(args, **kwargs)
            except ParamError as exc:
                return error_result(str(exc), code="param_error")
            except Exception as exc:  # noqa: BLE001 — defensive; tools must never raise
                return error_result(f"Tool execution failed: {exc!s}", code="tool_error")

            # Stage 5: record successful invocation for rate-limit policies.
            # Ledger append is handled by the post_tool_call hook so we
            # don't double-record here.
            _record_invocation(action_ctx)
            return result

        gated._clawmes_meta = {  # type: ignore[attr-defined]
            "name": name,
            "toolset": toolset,
            "schema": schema,
            "description": description,
            "requires_env": requires_env,
            "emoji": emoji,
            "is_write": True,
        }
        return gated

    return decorator


def _try_delegation(action_ctx: ActionContext, args: dict[str, Any]) -> str | None:
    """Stage 3: attempt on-chain delegation redemption.

    Returns a tool-result JSON string when the outcome is terminal:

      * redemption succeeded → success result with the tx hash (handler is
        skipped);
      * redemption was attempted and refused (caveat violation, revoked,
        expired, rate-limited, RPC failure) → error result. We fail **closed**
        rather than silently running the tool's own send path, which would
        bypass the on-chain limit the user signed.

    Returns ``None`` when delegation is simply not applicable (no matching
    delegation, unsupported tool, EOA delegator, unparseable args), so the
    gate falls through to the normal handler.

    Any unexpected error inside the delegation stack is swallowed and treated
    as "not applicable" — delegation must never break a working tool.
    """
    try:
        from clawmes.delegation.executor import try_delegation_execution
    except Exception:  # noqa: BLE001 — delegation optional; never block tools
        return None

    try:
        outcome = try_delegation_execution(action_ctx, args or {})
    except Exception as exc:  # noqa: BLE001 — defensive: fall through to handler
        from clawmes.lib.logger import logger_for

        logger_for("tools.registry").warning("delegation stage errored: %s", exc)
        return None

    if outcome.executed:
        from clawmes.lib.tool_result import json_result

        return json_result(
            {
                "delegated": True,
                "tx_hash": outcome.tx_hash,
                "chain_id": outcome.chain_id,
            },
            summary=(
                f"Executed via on-chain delegation (EIP-7710).\n"
                f"  Tx:    {outcome.tx_hash}\n"
                f"  Chain: {outcome.chain_id}"
            ),
        )
    if outcome.error:
        return error_result(
            f"On-chain delegation refused this action: {outcome.error}",
            code="delegation_refused",
        )
    return None


def _extract_chain_id(args: dict[str, Any] | None) -> int | None:
    if not args:
        return None
    raw = args.get("chain_id") or args.get("chain")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _extract_value_wei(args: dict[str, Any] | None) -> int | None:
    """Best-effort extraction of the wei value at risk.

    Order of preference:

    1. Explicit ``value_wei`` / ``amount_wei`` — caller knows the gate
       will quantify on this exact integer.
    2. ``amount`` for a native transfer (no ``token``) — every chain
       in our registry uses 18 decimals for the gas token, so we
       convert without an RPC round-trip.
    3. ``amount`` for an ERC-20 transfer — read decimals from the
       :class:`TokenDecimalsService` cache (seed values + previously
       verified/fallback). We use the non-blocking ``peek`` so the
       gate never triggers an RPC; unseeded unknown tokens skip the
       quantitative gate cleanly. Once the tool body has done a
       successful ``get_strict`` once, the cache fills and subsequent
       gate evaluations on the same token quantify correctly.

    Returns ``None`` when none of these paths produce an integer.
    Quantitative gates silently won't fire on ``None``; callers that
    require a strict gate should set ``value_wei`` explicitly.
    """
    if not args:
        return None
    for key in ("value_wei", "amount_wei"):
        raw = args.get(key)
        if raw is None:
            continue
        try:
            return int(raw)
        except (TypeError, ValueError):
            continue

    amount_raw = args.get("amount")
    if amount_raw is None or amount_raw == "":
        return None

    from clawmes.lib.decimals import to_base_units

    token = args.get("token")
    if token:
        # ERC-20: decimals from the cache only (no RPC inside the gate).
        from clawmes.services.token_decimals import get_token_decimals_service

        chain_id = _extract_chain_id(args) or 8453
        decimals = get_token_decimals_service().peek(str(token), chain_id)
        if decimals is None:
            return None
    else:
        decimals = 18

    try:
        return to_base_units(amount_raw, decimals)
    except (ValueError, ArithmeticError, TypeError):
        return None


def read_tool(
    *,
    name: str,
    toolset: str,
    description: str,
    schema: dict[str, Any],
    requires_env: list[str] | None = None,
    emoji: str = "",
) -> Callable[[Callable[..., str]], Callable[..., str]]:
    """Register a read-only tool. Skips the write gate."""

    def decorator(fn: Callable[..., str]) -> Callable[..., str]:
        @functools.wraps(fn)
        def wrapped(args: dict[str, Any], **kwargs: Any) -> str:
            try:
                return fn(args, **kwargs)
            except ParamError as exc:
                return error_result(str(exc), code="param_error")
            except Exception as exc:  # noqa: BLE001
                return error_result(f"Tool execution failed: {exc!s}", code="tool_error")

        wrapped._clawmes_meta = {  # type: ignore[attr-defined]
            "name": name,
            "toolset": toolset,
            "schema": schema,
            "description": description,
            "requires_env": requires_env,
            "emoji": emoji,
            "is_write": False,
        }
        return wrapped

    return decorator


def register_with_ctx(ctx, fn: Callable[..., str]) -> None:
    """Push a decorated handler into Hermes' ``ctx.register_tool``.

    Centralizes the kwargs translation so every tool module looks the same:

    .. code-block:: python

        from clawmes.tools.registry import register_with_ctx
        from clawmes.tools.transfer import transfer

        def register(ctx):
            register_with_ctx(ctx, transfer)
    """
    meta = getattr(fn, "_clawmes_meta", None)
    if meta is None:
        raise RuntimeError(
            f"{fn.__qualname__} is not a clawmes tool — apply @write_tool or @read_tool"
        )
    ctx.register_tool(
        name=meta["name"],
        toolset=meta["toolset"],
        schema=meta["schema"],
        description=meta["description"],
        handler=fn,
        requires_env=meta["requires_env"],
        emoji=meta["emoji"],
    )
