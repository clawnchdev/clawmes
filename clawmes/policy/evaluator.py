"""Policy evaluator.

Given an :class:`ActionContext` (tool name, args, wallet state, current
time), return a :class:`Decision`:

  * ``allow``   — proceed without prompt
  * ``block``   — refuse with a human-readable reason
  * ``confirm`` — require a one-time nonce round-trip with the user

The evaluator is **deterministic** and **stateless** with respect to the
calling ``ActionContext`` — same input → same decision. The confirm
nonce is issued separately by ``ConfirmStore``.

This module is a stub at this milestone — every action returns ``allow``
so the gating decorator skeleton in ``tools/registry.py`` is exercised
end-to-end. The real evaluator comes online in v0.2.0 alongside the NL
policy parser.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class ActionContext:
    """Snapshot of a write-tool invocation, fed to the evaluator."""

    tool_name: str
    args: dict[str, Any]
    user_id: str = "default"
    chain_id: int | None = None


@dataclass(frozen=True)
class Decision:
    kind: Literal["allow", "block", "confirm"]
    policy_name: str = ""
    reason: str = ""


_ALLOW = Decision(kind="allow")


def evaluate(ctx: ActionContext) -> Decision:
    """Return the policy decision for ``ctx``.

    Stub implementation — always allows. The real evaluator iterates
    configured policies (loaded from
    ``${HERMES_HOME}/clawmes/policy/policies.json``) and short-circuits
    on the first match.
    """
    return _ALLOW
