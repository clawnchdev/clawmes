"""Tool registry — decorators that wrap tool handlers with the gating pipeline.

Two decorators:

  * :func:`write_tool` — for any tool that mutates on-chain state. The
    decorated handler runs through the pipeline:

      1. Readonly-mode check (``/safemode`` toggle)
      2. Policy evaluation (``allow | block | confirm``)
         - ``confirm`` returns a ``POLICY HOLD`` instruction to the LLM
           with a one-time nonce; the LLM relays to the user, gets approval,
           and retries with the nonce in ``policyConfirmationNonce``.
      3. Delegation execution (EIP-7710) — if a delegation is configured
         for this action, the SA bridge handles the tx and we skip the
         original handler.
      4. Original handler execution.
      5. Ledger record on success.

  * :func:`read_tool` — for read-only tools. Skips the gate; only wraps in
    a generic try/except that converts unexpected exceptions to a clean
    error tool result.

The set of write tools is implicit — ``WRITE_TOOL_NAMES`` is populated as
``@write_tool`` decorators are evaluated at import time. There is no second
source of truth, so additions can never drift.

The actual policy / readonly / delegation / ledger machinery is built up in
later commits. Until those land, the gate stages are no-ops and only stage
4 executes — but the wiring is in place so adding the gates is a
non-disruptive change.
"""

from __future__ import annotations

import functools
from typing import Any, Callable

from clawmes.lib.params import ParamError
from clawmes.lib.tool_result import error_result

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
            # Stages 1-3 are skeletons until the policy / delegation
            # services land. They short-circuit safely for now.
            try:
                # Stage 1: readonly-mode check (no-op stub)
                # Stage 2: policy evaluation (no-op stub)
                # Stage 3: delegation attempt (no-op stub)

                # Stage 4: actual handler
                return fn(args, **kwargs)
            except ParamError as exc:
                return error_result(str(exc), code="param_error")
            except Exception as exc:  # noqa: BLE001 — defensive; tools must never raise
                return error_result(f"Tool execution failed: {exc!s}", code="tool_error")
            finally:
                # Stage 5: ledger record on success (no-op stub)
                pass

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
