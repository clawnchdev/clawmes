"""Tool registry — decorators that wrap tool handlers with the gating pipeline.

Two decorators:

  * :func:`write_tool` — for any tool that mutates on-chain state. The
    decorated handler runs through the pipeline:

      1. Readonly-mode check (``/safemode`` toggle) — block if active
      2. Policy evaluation (``allow | block | confirm``)
         - ``confirm`` returns a ``POLICY HOLD`` instruction to the LLM
           with a one-time nonce; the LLM relays to the user, gets approval,
           and retries with the nonce in ``policyConfirmationNonce``.
      3. Delegation execution (EIP-7710) — TODO; tracked in PRD §6.7.
      4. Original handler execution.
      5. Usage counter + ledger record on success.

  * :func:`read_tool` — for read-only tools. Skips the gate; only wraps in
    a generic try/except that converts unexpected exceptions to a clean
    error tool result.

The set of write tools is implicit — ``WRITE_TOOL_NAMES`` is populated as
``@write_tool`` decorators are evaluated at import time. There is no second
source of truth, so additions can never drift.

Stage 3 (delegation) remains a TODO at this milestone; everything else
is live.
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

            # Stage 3: delegation attempt — TODO when SA bridge lands

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

    Looks at common arg names — ``value_wei``, ``amount_wei``,
    ``amount`` — and converts. ``amount`` is human-readable so it
    requires a token decimals lookup we don't have here; we conservatively
    skip it and leave the policy gate to the per-tool layer for that case.

    Returns None when the arg shape doesn't map cleanly. Policy
    quantitative gates that depend on value_wei silently won't fire on
    None; callers that need a strict gate should set the arg explicitly.
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
