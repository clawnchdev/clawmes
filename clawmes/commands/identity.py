"""``/identity`` slash command — show / create the agent's DID identity.

Three sub-actions selected by the raw arg:

  * (no args)   — show the active identity (or "none set")
  * ``create``  — generate a fresh ed25519 keypair (refuses if one
    already exists; pass ``create force`` to overwrite)
  * ``create force`` — replace any existing identity

In-memory only in v1 — restart loses the keypair. Persistence lands
in a follow-up PR (mirror of the wallet keystore pattern).
"""

from __future__ import annotations


def _record(name: str, args: str, result: str) -> None:
    """Best-effort recording into command_history.

    The command-history service lives in a separate PR; on branches
    that don't have it yet, the import fails and we silently skip.
    Once both PRs land, /identity calls show up in /history without
    further wiring.
    """
    try:
        from clawmes.services.command_history import record_command_call

        record_command_call(name, args, result)
    except Exception:  # noqa: BLE001 — recording must never break a command
        pass


async def handle_identity(raw_args: str) -> str:
    from clawmes.services.identity import get_identity_service

    arg = raw_args.strip().lower()
    svc = get_identity_service()

    if not arg:
        summary = svc.show()
        if not summary:
            out = (
                "No agent identity set. Run /identity create to generate "
                "one. (In-memory only — restart loses it.)"
            )
        else:
            out = "\n".join(
                [
                    "Agent identity:",
                    f"  DID:        {summary['did']}",
                    f"  Public key: {summary['public_key_hex']}",
                    f"  Created:    epoch {int(summary['created_at'])}",
                ]
            )
        _record("identity", raw_args, out)
        return out

    if arg == "create" or arg.startswith("create"):
        force = "force" in arg.split()
        if svc.has_identity() and not force:
            existing = svc.show()
            out = (
                f"Agent identity already exists ({existing['did']}).\n"
                "Run /identity create force to replace it, or "
                "/identity to view the current one."
            )
        else:
            summary = svc.generate()
            out = "\n".join(
                [
                    "Generated agent identity:",
                    f"  DID:        {summary['did']}",
                    f"  Public key: {summary['public_key_hex']}",
                    "(In-memory only — restart loses the keypair.)",
                ]
            )
        _record("identity", raw_args, out)
        return out

    return (
        f"Unknown /identity arg {arg!r}. Use:\n"
        "  /identity              — show current identity\n"
        "  /identity create       — generate a new keypair\n"
        "  /identity create force — replace an existing keypair"
    )


def register(ctx) -> None:
    """Wire /identity into Hermes."""
    ctx.register_command(
        name="identity",
        handler=handle_identity,
        description="Show or generate the agent's DID identity (ed25519)",
        args_hint="[create [force]]",
    )
