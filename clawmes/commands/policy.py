"""Policy commands: ``/policy``, ``/policy_clear``, ``/safemode``, ``/dangermode``, ``/audit``."""

from __future__ import annotations

from clawmes.policy.parser import ParseError, parse_policy
from clawmes.policy.storage import load_policies, save_policies
from clawmes.services.mode_service import get_mode_service


async def handle_policy(raw_args: str) -> str:
    text = raw_args.strip()
    if not text:
        return _list_policies()

    try:
        new_policy = parse_policy(text)
    except ParseError as exc:
        return f"Couldn't parse policy: {exc}"

    # Append to the persisted list. Block rules conventionally come
    # first (so they win over later confirm rules); for v0.1 we simply
    # append — users can re-order via `/policy_clear` and re-adding,
    # or by editing the JSON directly.
    existing = load_policies()
    existing.append(new_policy)
    save_policies(existing)
    return (
        f"Added policy {new_policy.name!r} → {new_policy.decision}.\n"
        f"  {_render_constraints(new_policy)}"
    )


def _render_constraints(p) -> str:
    parts: list[str] = []
    if p.applies_to_tools:
        parts.append("tools=" + ",".join(p.applies_to_tools))
    if p.chain_ids:
        parts.append("chains=" + ",".join(str(c) for c in p.chain_ids))
    if p.max_amount_wei is not None:
        parts.append(f"max_amount_wei={p.max_amount_wei}")
    if p.max_per_hour is not None:
        parts.append(f"max_per_hour={p.max_per_hour}")
    return "; ".join(parts) if parts else "(catch-all)"


def _list_policies() -> str:
    policies = load_policies()
    if not policies:
        return "Active policies: (none)"
    lines = ["Active policies:"]
    for p in policies:
        lines.append(f"  • {p.name} → {p.decision} :: {_render_constraints(p)}")
        if p.description:
            lines.append(f"      {p.description}")
    return "\n".join(lines)


async def handle_policy_clear(raw_args: str) -> str:
    save_policies([])
    return (
        "All policies cleared. Default safety policies will be re-installed on next plugin start."
    )


async def handle_safemode(raw_args: str) -> str:
    arg = raw_args.strip().lower()
    svc = get_mode_service()
    if arg == "off":
        svc.set_mode("normal")
        return "Safe mode disabled. Writes are now subject to the configured policies only."
    svc.set_mode("readonly")
    return (
        "Safe mode ON. All write tools will be blocked at the gate. "
        "Use `/safemode off` to re-enable writes."
    )


async def handle_dangermode(raw_args: str) -> str:
    arg = raw_args.strip().lower()
    svc = get_mode_service()
    if arg == "off":
        svc.set_mode("normal")
        return "Danger mode disabled. Returning to normal operation."
    svc.set_mode("danger")
    return (
        "DANGER MODE ON. Readonly check is bypassed. Policy gating still applies. "
        "Use `/dangermode off` to return to normal."
    )


async def handle_audit(raw_args: str) -> str:
    return "Risk audit (allowances + delegations + signers) not yet implemented at this milestone."


def register(ctx) -> None:
    ctx.register_command(
        name="policy",
        handler=handle_policy,
        description="Show or set spending policies in natural language",
        args_hint="[rules]",
    )
    ctx.register_command(
        name="policy_clear",
        handler=handle_policy_clear,
        description="Remove all policies (requires confirm)",
    )
    ctx.register_command(
        name="safemode",
        handler=handle_safemode,
        description="Block all write tools (read-only mode)",
    )
    ctx.register_command(
        name="dangermode",
        handler=handle_dangermode,
        description="Disable readonly check (requires explicit re-enable)",
    )
    ctx.register_command(
        name="audit",
        handler=handle_audit,
        description="Full risk audit: allowances, delegations, signers",
    )
