"""Policy commands: ``/policy``, ``/safemode``, ``/dangermode``, ``/audit``."""

from __future__ import annotations


async def handle_policy(raw_args: str) -> str:
    text = raw_args.strip()
    if not text:
        # No args — show active policies.
        return _list_policies()
    return (
        f"Setting policies from natural language is not yet implemented "
        f"at this milestone. Got: {text!r}"
    )


def _list_policies() -> str:
    # TODO(v0.1.0): read from policy/storage.py
    return "Active policies: (none configured)\n\nSet via `/policy <natural language rules>`."


async def handle_policy_clear(raw_args: str) -> str:
    return "Policy clear not yet implemented at this milestone."


async def handle_safemode(raw_args: str) -> str:
    return (
        "Safe mode toggle not yet implemented at this milestone. "
        "Will block all writes when enabled."
    )


async def handle_dangermode(raw_args: str) -> str:
    return (
        "Danger mode toggle not yet implemented at this milestone. "
        "Will require explicit re-enablement once active."
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
