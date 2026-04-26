"""Policy IR types.

A :class:`Policy` is a deterministic rule that, given an
:class:`ActionContext`, returns one of three decisions:

  * ``allow``   — proceed with the tool call
  * ``block``   — refuse with a human-readable reason
  * ``confirm`` — require a one-time nonce round-trip with the user

Policies are matched in storage order and the first one whose
conditions are all satisfied wins. A policy with no conditions
(``applies_to_tools=()`` and no thresholds) is a catch-all.

The IR is intentionally narrow at this milestone — five fields,
all primitives. Additional dimensions (token symbol, recipient
allowlist, time-of-day windows) come in a follow-up commit.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class ActionContext:
    """Snapshot of a single write-tool invocation, fed to the evaluator."""

    tool_name: str
    args: dict[str, Any]
    user_id: str = "default"
    chain_id: int | None = None
    #: Wei value at risk (e.g. transfer amount). ``None`` means we
    #: don't know (the tool's args don't carry an obvious amount field).
    value_wei: int | None = None


@dataclass(frozen=True)
class Decision:
    """Output of policy evaluation."""

    kind: Literal["allow", "block", "confirm"]
    policy_name: str = ""
    reason: str = ""


@dataclass(frozen=True)
class Policy:
    """A single policy rule.

    All non-default fields act as filters — only when EVERY filter
    matches does the policy apply. ``decision`` is then returned.

    Filters:
      * ``applies_to_tools`` — tuple of tool names (empty = match all)
      * ``chain_ids``        — tuple of chain ids (empty = match all)

    Quantitative gates (any one triggers):
      * ``max_amount_wei``     — value at risk above this triggers the
                                 decision
      * ``max_per_hour``       — invocations per rolling 60 min above
                                 this triggers the decision

    A policy with no quantitative gates fires whenever the filters
    match — useful for catch-all "block" or "confirm everything"
    rules.
    """

    name: str
    decision: Literal["allow", "block", "confirm"]
    applies_to_tools: tuple[str, ...] = ()
    chain_ids: tuple[int, ...] = ()
    max_amount_wei: int | None = None
    max_per_hour: int | None = None
    description: str = ""

    def matches_filters(self, ctx: ActionContext) -> bool:
        """Return True iff the filter conditions match this context.

        The quantitative gates are NOT checked here — they're checked
        by the evaluator with help from the usage counter.
        """
        if self.applies_to_tools and ctx.tool_name not in self.applies_to_tools:
            return False
        if self.chain_ids and ctx.chain_id not in self.chain_ids:
            return False
        return True

    def has_quantitative_gates(self) -> bool:
        return self.max_amount_wei is not None or self.max_per_hour is not None


# Default policies installed on first run. Conservative — the catch-all
# at the end means any unrecognized write tool requires user confirm.
DEFAULT_POLICIES: tuple[Policy, ...] = (
    Policy(
        name="block_unbounded_token_approvals",
        decision="block",
        applies_to_tools=("approvals",),
        max_amount_wei=2**256 - 1,  # uint256.max — anything below the cap is fine
        description="Block ERC-20 approvals for the maximum uint256 value",
    ),
    Policy(
        name="confirm_large_transfers",
        decision="confirm",
        applies_to_tools=("transfer",),
        max_amount_wei=50_000_000_000_000_000,  # 0.05 ETH
        description="Require user confirmation for transfers above 0.05 ETH",
    ),
    Policy(
        name="rate_limit_swaps",
        decision="confirm",
        applies_to_tools=("defi_swap",),
        max_per_hour=20,
        description="Require user confirmation after 20 swaps in the last hour",
    ),
)
