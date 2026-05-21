"""``policy_manage`` — LLM-callable spending-policy management.

Lets the agent propose new policies, list/get/revise/disable/enable/delete
existing ones, dry-run-evaluate hypothetical actions against the policy
set, and surface usage counters. Backs onto the already-shipped
``clawmes.policy`` engine and shares the on-disk policy store at
``${HERMES_HOME}/clawmes/policy/policies.json``.

Eleven actions:

  * ``propose`` — construct a Policy from typed args, issue a one-time
    confirmation nonce, do NOT persist. The LLM is expected to show the
    proposed policy to the user and retry with ``action=confirm``.
  * ``confirm`` — validate the nonce against the same propose args and
    persist the policy.
  * ``revise`` — load an existing policy by ``policyId``, overlay the
    provided fields, save back. No confirm step (the policy already
    exists by user consent at creation time).
  * ``list`` — return active + disabled policies.
  * ``get`` — return a single policy by name; searches active then
    disabled.
  * ``disable`` — move from active to a disabled side-car. Evaluator
    skips disabled policies; they can be re-enabled later.
  * ``enable`` — move back from disabled to active.
  * ``delete`` — permanently remove from active OR disabled.
  * ``evaluate`` — dry-run the evaluator against synthetic args.
    Returns the Decision without recording an invocation.
  * ``usage`` — return per-tool invocation counts in the rolling 60-min
    window. Per-policy counters are not tracked (the underlying
    ``UsageCounter`` is keyed by ``(user, tool)``, not policy); the
    closest surfacing is "counts for every tool this policy applies
    to" when ``policyId`` is supplied.
  * ``categories`` — return the static tool-category map.

Decorated with ``@read_tool`` (not ``@write_tool``) — the tool that
manages policies must not itself be gated by policies (recursion).
Mutating actions persist directly to disk; the safety boundary is the
LLM's user-visible ``propose → confirm`` flow.

OC parity caveats: OpenClawnch supports richer policy schemas
(allowlists, time-of-day windows, approval thresholds, period-window
rate limits). Clawmes' :class:`Policy` IR covers tool filters + chain
filters + amount threshold + rate threshold only. Args that don't map
return ``not_implemented``. Per-policy ``usage`` history doesn't exist
in the clawmes counter; this tool surfaces per-(user, tool) counts as
the closest available signal.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from clawmes.lib.logger import logger_for
from clawmes.lib.params import ParamError, read_enum, read_int, read_list, read_str
from clawmes.lib.tool_result import error_result, json_result
from clawmes.policy.confirm_store import store as confirm_store
from clawmes.policy.evaluator import evaluate
from clawmes.policy.storage import load_policies, policies_path, save_policies
from clawmes.policy.types import ActionContext, Policy
from clawmes.policy.usage_counter import get_usage_counter
from clawmes.tools.registry import read_tool, register_with_ctx

_log = logger_for("tools.policy_manage")

# --- Tool categories ------------------------------------------------------

# Bucket every shipped clawmes tool by domain. Surfaced by the
# ``categories`` action so an LLM proposing a policy can ask the user
# something like "do you want this to apply to all trading tools?"
# and translate the answer into a concrete ``applies_to_tools`` list.
_TOOL_CATEGORIES: dict[str, tuple[str, ...]] = {
    "wallet": ("clawnchconnect", "transfer", "approvals", "permit2"),
    "trading": ("defi_swap", "manage_orders"),
    "lending": ("defi_lend",),
    "staking": ("defi_stake",),
    "bridging": ("bridge",),
    "liquidity": ("liquidity", "yield"),
    "market_data": (
        "defi_price",
        "defi_balance",
        "market_intel",
        "analytics",
        "cost_basis",
    ),
    "launchpad": (
        "clawnch_launch",
        "clawnch_fees",
        "bankr_launch",
        "bankr_automate",
        "bankr_polymarket",
        "bankr_leverage",
    ),
    "nft": ("nft",),
    "governance": ("governance",),
    "automation": ("watch_activity", "compound_action"),
    "memory": ("agent_memory", "session_recall", "skill_evolve"),
    "social": ("farcaster", "molten", "nookplot"),
    "intelligence": ("herd_intelligence", "wayfinder", "block_explorer", "browser"),
    "privacy": ("privacy", "lobster_cash"),
    "safety": ("safe",),
    "fiat": ("paysponge",),
    "airdrop": ("airdrop",),
    "extension": ("giza", "clawnx", "hummingbot", "_user_tools"),
}

_VALID_ACTIONS = [
    "propose",
    "confirm",
    "revise",
    "list",
    "get",
    "disable",
    "enable",
    "delete",
    "evaluate",
    "usage",
    "categories",
]
_VALID_DECISIONS = ["allow", "block", "confirm"]

# --- Schema ---------------------------------------------------------------

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": _VALID_ACTIONS,
        },
        "policyId": {
            "type": "string",
            "description": (
                "Policy name (canonical ID). Required for get / revise / "
                "disable / enable / delete. Optional for usage (filters "
                "counts to the policy's applies_to_tools)."
            ),
        },
        "name": {
            "type": "string",
            "description": (
                "Display name for the policy. Required for propose. "
                "Used as the canonical ID — must be unique across active "
                "and disabled policies."
            ),
        },
        "decision": {
            "type": "string",
            "enum": _VALID_DECISIONS,
            "description": "What the policy returns when filters match. Required for propose.",
        },
        "applies_to_tools": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Tool names this policy gates. Empty list = catch-all (every write tool)."
            ),
        },
        "chain_ids": {
            "type": "array",
            "items": {"type": "integer"},
            "description": "Chain IDs this policy applies to. Empty list = all chains.",
        },
        "max_amount_wei": {
            "type": "integer",
            "description": (
                "Value-at-risk threshold (wei). Policy fires when "
                "amount >= this. Omit for catch-all rules."
            ),
        },
        "max_per_hour": {
            "type": "integer",
            "description": (
                "Rate-limit threshold. Policy fires when this many "
                "invocations have happened in the past hour."
            ),
        },
        "description": {
            "type": "string",
            "description": "Human-readable rationale shown when the policy triggers.",
        },
        "policyConfirmationNonce": {
            "type": "string",
            "description": "Required for action=confirm. Returned by a prior action=propose.",
        },
        "toolName": {
            "type": "string",
            "description": "Tool to simulate for action=evaluate.",
        },
        "value_wei": {
            "type": "integer",
            "description": "Value-at-risk in wei for action=evaluate (optional).",
        },
        "chain_id": {
            "type": "integer",
            "description": "Chain ID for action=evaluate (optional).",
        },
        "user_id": {
            "type": "string",
            "description": "User ID for evaluate / usage actions. Defaults to 'default'.",
        },
    },
    "required": ["action"],
}


@read_tool(
    name="policy_manage",
    toolset="clawmes-policy",
    description=(
        "Manage spending policies through an LLM-callable surface. Propose, "
        "confirm, list, get, revise, enable/disable, delete, evaluate "
        "(dry-run), and inspect usage. The propose -> confirm flow asks "
        "the user before persisting any new rule. Backs the same on-disk "
        "policy store the /policy slash command uses."
    ),
    schema=_SCHEMA,
    emoji="\U0001f4dc",
)
def policy_manage(args: dict[str, Any], **kwargs: Any) -> str:
    action = read_enum(args, "action", _VALID_ACTIONS, required=True)

    if action == "list":
        return _handle_list()
    if action == "get":
        return _handle_get(args)
    if action == "propose":
        return _handle_propose(args)
    if action == "confirm":
        return _handle_confirm(args)
    if action == "revise":
        return _handle_revise(args)
    if action == "disable":
        return _handle_disable(args)
    if action == "enable":
        return _handle_enable(args)
    if action == "delete":
        return _handle_delete(args)
    if action == "evaluate":
        return _handle_evaluate(args)
    if action == "usage":
        return _handle_usage(args)
    # action == "categories" — the only remaining valid action
    return _handle_categories()


# --- Action handlers ------------------------------------------------------


def _handle_list() -> str:
    active = load_policies()
    disabled = _load_disabled()
    return json_result(
        {
            "active": [_policy_to_dict(p) for p in active],
            "disabled": [_policy_to_dict(p) for p in disabled],
            "active_count": len(active),
            "disabled_count": len(disabled),
        },
        summary=_format_list_summary(active, disabled),
    )


def _format_list_summary(active: list[Policy], disabled: list[Policy]) -> str:
    lines = [f"{len(active)} active, {len(disabled)} disabled policy(ies)"]
    for p in active[:10]:
        lines.append(f"  \u2713 {p.name}: {p.decision}")
    if len(active) > 10:
        lines.append(f"  ... +{len(active) - 10} more")
    for p in disabled[:5]:
        lines.append(f"  \u2717 {p.name} (disabled)")
    if len(disabled) > 5:
        lines.append(f"  ... +{len(disabled) - 5} more disabled")
    return "\n".join(lines)


def _handle_get(args: dict[str, Any]) -> str:
    policy_id = read_str(args, "policyId", required=True)
    for source, label in ((load_policies(), "active"), (_load_disabled(), "disabled")):
        for p in source:
            if p.name == policy_id:
                return json_result(
                    {"status": label, "policy": _policy_to_dict(p)},
                    summary=f"Policy {policy_id!r} ({label})",
                )
    return error_result(f"Policy {policy_id!r} not found", code="not_found")


def _handle_propose(args: dict[str, Any]) -> str:
    try:
        policy = _build_policy_from_args(args)
    except ParamError as exc:
        return error_result(str(exc), code="param_error")

    for p in load_policies():
        if p.name == policy.name:
            return error_result(
                f"Active policy {policy.name!r} already exists. Use action=revise "
                "to modify or action=delete to remove first.",
                code="conflict",
            )
    for p in _load_disabled():
        if p.name == policy.name:
            return error_result(
                f"Disabled policy {policy.name!r} already exists. Use action=enable "
                "to restore, action=delete to remove, then re-propose if you want a "
                "fresh definition.",
                code="conflict",
            )

    nonce = confirm_store.issue(_propose_ctx(policy))
    return json_result(
        {
            "status": "proposed",
            "nonce": nonce,
            "policy": _policy_to_dict(policy),
        },
        summary=(
            f"POLICY PROPOSED: {policy.name}\n"
            f"  Decision: {policy.decision}\n"
            f"  Applies to: {', '.join(policy.applies_to_tools) or 'all write tools'}\n"
            f"  Chains: {', '.join(str(c) for c in policy.chain_ids) or 'all'}\n"
            f"  Max amount (wei): {policy.max_amount_wei}\n"
            f"  Max per hour: {policy.max_per_hour}\n"
            f"  Description: {policy.description or '(none)'}\n\n"
            "Show this to the user. To confirm and persist, retry with the same "
            f"name/decision/etc. and: action=confirm, policyConfirmationNonce={nonce!r}."
        ),
    )


def _handle_confirm(args: dict[str, Any]) -> str:
    try:
        policy = _build_policy_from_args(args)
    except ParamError as exc:
        return error_result(str(exc), code="param_error")
    nonce = read_str(args, "policyConfirmationNonce", required=True)
    if not confirm_store.consume(_propose_ctx(policy), nonce):
        return error_result(
            "Confirmation nonce invalid, expired, or doesn't match the original "
            "propose (the policy args must be identical). Re-run action=propose "
            "to get a fresh nonce.",
            code="confirm_failed",
        )
    policies = load_policies()
    for p in policies:
        if p.name == policy.name:
            return error_result(
                f"Policy {policy.name!r} was created concurrently between propose "
                "and confirm. Aborting.",
                code="conflict",
            )
    policies.append(policy)
    save_policies(policies)
    return json_result(
        {"status": "active", "policy": _policy_to_dict(policy)},
        summary=f"Policy {policy.name!r} is now active.",
    )


def _handle_revise(args: dict[str, Any]) -> str:
    policy_id = read_str(args, "policyId", required=True)
    policies = load_policies()
    for i, p in enumerate(policies):
        if p.name == policy_id:
            try:
                updated = _overlay_policy(p, args)
            except ParamError as exc:
                return error_result(str(exc), code="param_error")
            policies[i] = updated
            save_policies(policies)
            return json_result(
                {"status": "revised", "policy": _policy_to_dict(updated)},
                summary=f"Policy {policy_id!r} revised.",
            )
    return error_result(
        f"Active policy {policy_id!r} not found. (Disabled policies must be "
        "enabled before revision.)",
        code="not_found",
    )


def _handle_disable(args: dict[str, Any]) -> str:
    policy_id = read_str(args, "policyId", required=True)
    policies = load_policies()
    for i, p in enumerate(policies):
        if p.name == policy_id:
            policies.pop(i)
            save_policies(policies)
            disabled = _load_disabled()
            disabled.append(p)
            _save_disabled(disabled)
            return json_result(
                {"status": "disabled", "policy": _policy_to_dict(p)},
                summary=f"Policy {policy_id!r} disabled.",
            )
    return error_result(f"Active policy {policy_id!r} not found", code="not_found")


def _handle_enable(args: dict[str, Any]) -> str:
    policy_id = read_str(args, "policyId", required=True)
    disabled = _load_disabled()
    for i, p in enumerate(disabled):
        if p.name == policy_id:
            disabled.pop(i)
            _save_disabled(disabled)
            policies = load_policies()
            policies.append(p)
            save_policies(policies)
            return json_result(
                {"status": "active", "policy": _policy_to_dict(p)},
                summary=f"Policy {policy_id!r} enabled.",
            )
    return error_result(f"Disabled policy {policy_id!r} not found", code="not_found")


def _handle_delete(args: dict[str, Any]) -> str:
    policy_id = read_str(args, "policyId", required=True)
    policies = load_policies()
    for i, p in enumerate(policies):
        if p.name == policy_id:
            policies.pop(i)
            save_policies(policies)
            return json_result(
                {"status": "deleted", "from": "active", "policy": _policy_to_dict(p)},
                summary=f"Policy {policy_id!r} deleted (was active).",
            )
    disabled = _load_disabled()
    for i, p in enumerate(disabled):
        if p.name == policy_id:
            disabled.pop(i)
            _save_disabled(disabled)
            return json_result(
                {"status": "deleted", "from": "disabled", "policy": _policy_to_dict(p)},
                summary=f"Policy {policy_id!r} deleted (was disabled).",
            )
    return error_result(f"Policy {policy_id!r} not found", code="not_found")


def _handle_evaluate(args: dict[str, Any]) -> str:
    tool_name = read_str(args, "toolName", required=True)
    user_id = read_str(args, "user_id") or "default"
    value_wei = read_int(args, "value_wei")
    chain_id = read_int(args, "chain_id")
    ctx = ActionContext(
        tool_name=tool_name,
        args={},
        user_id=user_id,
        chain_id=chain_id,
        value_wei=value_wei,
    )
    decision = evaluate(ctx)
    return json_result(
        {
            "input": {
                "tool_name": tool_name,
                "user_id": user_id,
                "value_wei": value_wei,
                "chain_id": chain_id,
            },
            "decision": {
                "kind": decision.kind,
                "policy_name": decision.policy_name,
                "reason": decision.reason,
            },
        },
        summary=(
            f"Decision: {decision.kind}"
            + (
                f" (policy {decision.policy_name!r}: {decision.reason})"
                if decision.policy_name
                else ""
            )
        ),
    )


def _handle_usage(args: dict[str, Any]) -> str:
    user_id = read_str(args, "user_id") or "default"
    counter = get_usage_counter()
    policy_id = read_str(args, "policyId")

    if policy_id:
        for source in (load_policies(), _load_disabled()):
            for p in source:
                if p.name == policy_id:
                    tools = p.applies_to_tools or _all_tool_names()
                    counts = {t: counter.count(user_id, t) for t in tools}
                    return json_result(
                        {
                            "policy": policy_id,
                            "user_id": user_id,
                            "tool_counts": counts,
                        },
                        summary=_format_usage_summary(policy_id, user_id, counts),
                    )
        return error_result(f"Policy {policy_id!r} not found", code="not_found")

    all_tools = _all_tool_names()
    counts = {t: counter.count(user_id, t) for t in sorted(all_tools)}
    nonzero = {t: n for t, n in counts.items() if n > 0}
    summary_lines = [f"Usage (last hour, user={user_id!r}):"]
    if nonzero:
        for t, n in nonzero.items():
            summary_lines.append(f"  {t}: {n}")
    else:
        summary_lines.append("  (no recorded invocations)")
    return json_result(
        {"user_id": user_id, "tool_counts": counts},
        summary="\n".join(summary_lines),
    )


def _format_usage_summary(policy_id: str, user_id: str, counts: dict[str, int]) -> str:
    lines = [f"Usage for policy {policy_id!r} (user={user_id!r}):"]
    if counts:
        for t, n in counts.items():
            lines.append(f"  {t}: {n}")
    else:
        lines.append("  (policy applies to no tools)")
    return "\n".join(lines)


def _handle_categories() -> str:
    return json_result(
        {"categories": {name: list(tools) for name, tools in _TOOL_CATEGORIES.items()}},
        summary=_format_categories_summary(),
    )


def _format_categories_summary() -> str:
    lines = [f"{len(_TOOL_CATEGORIES)} tool categories:"]
    for name, tools in _TOOL_CATEGORIES.items():
        head = ", ".join(tools[:5])
        suffix = " ..." if len(tools) > 5 else ""
        lines.append(f"  {name}: {head}{suffix}")
    return "\n".join(lines)


# --- Builders / helpers ---------------------------------------------------


def _all_tool_names() -> tuple[str, ...]:
    """Return every tool name across all categories, deduplicated."""
    seen: list[str] = []
    for tools in _TOOL_CATEGORIES.values():
        for t in tools:
            if t not in seen:
                seen.append(t)
    return tuple(seen)


def _build_policy_from_args(args: dict[str, Any]) -> Policy:
    """Build a fully-typed Policy from typed args, validating each field.

    Raises :class:`ParamError` on missing required fields. ``name`` and
    ``decision`` are required; everything else is optional.
    """
    name = read_str(args, "name", required=True)
    decision = read_enum(args, "decision", _VALID_DECISIONS, required=True)
    applies_to_tools = tuple(str(t) for t in read_list(args, "applies_to_tools"))
    chain_ids = tuple(int(c) for c in read_list(args, "chain_ids"))
    max_amount_wei = read_int(args, "max_amount_wei")
    max_per_hour = read_int(args, "max_per_hour")
    description = read_str(args, "description") or ""
    # decision is validated via read_enum; assert non-None for type-check happiness
    if (
        name is None or decision is None
    ):  # pragma: no cover — read_str/read_enum required=True raise
        raise ParamError("name and decision are required to build a policy")
    return Policy(
        name=name,
        decision=decision,  # type: ignore[arg-type]
        applies_to_tools=applies_to_tools,
        chain_ids=chain_ids,
        max_amount_wei=max_amount_wei,
        max_per_hour=max_per_hour,
        description=description,
    )


def _overlay_policy(existing: Policy, args: dict[str, Any]) -> Policy:
    """Return a new :class:`Policy` with provided fields overriding ``existing``."""
    name = read_str(args, "name") or existing.name
    decision = read_enum(args, "decision", _VALID_DECISIONS) or existing.decision
    if args.get("applies_to_tools") is not None:
        applies_to_tools = tuple(str(t) for t in read_list(args, "applies_to_tools"))
    else:
        applies_to_tools = existing.applies_to_tools
    if args.get("chain_ids") is not None:
        chain_ids = tuple(int(c) for c in read_list(args, "chain_ids"))
    else:
        chain_ids = existing.chain_ids
    if args.get("max_amount_wei") is not None:
        max_amount_wei = read_int(args, "max_amount_wei")
    else:
        max_amount_wei = existing.max_amount_wei
    if args.get("max_per_hour") is not None:
        max_per_hour = read_int(args, "max_per_hour")
    else:
        max_per_hour = existing.max_per_hour
    description_arg = read_str(args, "description")
    description = existing.description if description_arg is None else description_arg
    return Policy(
        name=name,
        decision=decision,  # type: ignore[arg-type]
        applies_to_tools=applies_to_tools,
        chain_ids=chain_ids,
        max_amount_wei=max_amount_wei,
        max_per_hour=max_per_hour,
        description=description,
    )


def _policy_to_dict(p: Policy) -> dict[str, Any]:
    return {
        "name": p.name,
        "decision": p.decision,
        "applies_to_tools": list(p.applies_to_tools),
        "chain_ids": list(p.chain_ids),
        "max_amount_wei": p.max_amount_wei,
        "max_per_hour": p.max_per_hour,
        "description": p.description,
    }


def _propose_ctx(policy: Policy) -> ActionContext:
    """Build a stable :class:`ActionContext` for confirm-store fingerprinting.

    ``confirm_store`` fingerprints on ``(tool_name, sorted(args - nonce))``.
    Using a distinct synthetic tool name avoids collision with real
    write-tool confirm flows that share the same store.
    """
    return ActionContext(
        tool_name="policy_manage_propose",
        args={
            "name": policy.name,
            "decision": policy.decision,
            "applies_to_tools": list(policy.applies_to_tools),
            "chain_ids": list(policy.chain_ids),
            "max_amount_wei": policy.max_amount_wei,
            "max_per_hour": policy.max_per_hour,
            "description": policy.description,
        },
    )


# --- Disabled side-car storage --------------------------------------------


def _disabled_path() -> Path:
    return policies_path().parent / "disabled_policies.json"


def _load_disabled() -> list[Policy]:
    path = _disabled_path()
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _log.warning("disabled_policies.json unreadable (%s); treating as empty", exc)
        return []
    if not isinstance(raw, list):
        _log.warning("disabled_policies.json must be a list; got %s", type(raw).__name__)
        return []
    out: list[Policy] = []
    for entry in raw:
        decoded = _decode_policy(entry)
        if decoded is not None:
            out.append(decoded)
    return out


def _save_disabled(policies: list[Policy]) -> None:
    path = _disabled_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [_policy_to_dict(p) for p in policies]
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _decode_policy(entry: Any) -> Policy | None:
    if not isinstance(entry, dict):
        return None
    try:
        return Policy(
            name=str(entry["name"]),
            decision=entry["decision"],
            applies_to_tools=tuple(entry.get("applies_to_tools") or ()),
            chain_ids=tuple(entry.get("chain_ids") or ()),
            max_amount_wei=_int_or_none(entry.get("max_amount_wei")),
            max_per_hour=_int_or_none(entry.get("max_per_hour")),
            description=str(entry.get("description") or ""),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def register(ctx) -> None:
    """Wire ``policy_manage`` into Hermes."""
    register_with_ctx(ctx, policy_manage)
