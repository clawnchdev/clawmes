"""Policy evaluator — the real implementation.

Given an :class:`ActionContext`, walk the configured policies in order
and return the first matching :class:`Decision`. If no policy matches,
default to ``allow``.

A policy "matches" when:

  1. Its filter predicates accept the context (tool name, chain id).
  2. EITHER it has no quantitative gates (catch-all rule), OR at least
     one of its gates is exceeded:
       - ``max_amount_wei`` exceeded by ``ctx.value_wei``
       - ``max_per_hour`` exceeded by the usage counter

When a policy matches, its ``decision`` is the result. If multiple
policies could match the same context, the first one in storage order
wins — users can re-order via the policy file or the ``/policy``
command. Block decisions take precedence over confirm via this
ordering when a stricter policy is listed first.
"""

from __future__ import annotations

from collections.abc import Iterable

from clawmes.lib.logger import logger_for
from clawmes.policy.storage import load_policies
from clawmes.policy.types import ActionContext, Decision, Policy
from clawmes.policy.usage_counter import get_usage_counter

_log = logger_for("policy.evaluator")

_ALLOW = Decision(kind="allow")


def evaluate(
    ctx: ActionContext,
    *,
    policies: Iterable[Policy] | None = None,
) -> Decision:
    """Return the policy decision for ``ctx``.

    ``policies`` is exposed for tests; production callers use the
    persisted set via :func:`load_policies`.
    """
    rules = list(policies) if policies is not None else load_policies()
    counter = get_usage_counter()

    for policy in rules:
        if not policy.matches_filters(ctx):
            continue
        if policy.has_quantitative_gates():
            if not _gate_triggers(policy, ctx, counter):
                continue
        # Filters matched and either no gates or at least one fired.
        return Decision(
            kind=policy.decision,
            policy_name=policy.name,
            reason=policy.description or _default_reason(policy, ctx),
        )

    return _ALLOW


def record_invocation(ctx: ActionContext) -> None:
    """Record this invocation in the usage counter.

    Called by the ``@write_tool`` decorator on successful execution
    so future evaluations can see the rate.
    """
    get_usage_counter().record(ctx.user_id, ctx.tool_name)


def _gate_triggers(policy: Policy, ctx: ActionContext, counter) -> bool:
    """Return True iff at least one quantitative gate is exceeded."""
    if policy.max_amount_wei is not None:
        if ctx.value_wei is not None and ctx.value_wei >= policy.max_amount_wei:
            return True
    if policy.max_per_hour is not None:
        n = counter.count(ctx.user_id, ctx.tool_name)
        if n >= policy.max_per_hour:
            return True
    return False


def _default_reason(policy: Policy, ctx: ActionContext) -> str:
    parts: list[str] = []
    if policy.max_amount_wei is not None and ctx.value_wei is not None:
        parts.append(f"value {ctx.value_wei} >= threshold {policy.max_amount_wei}")
    if policy.max_per_hour is not None:
        n = get_usage_counter().count(ctx.user_id, ctx.tool_name)
        parts.append(f"{n} invocations >= rate cap {policy.max_per_hour}/hr")
    return "; ".join(parts) if parts else f"matched policy {policy.name!r}"
