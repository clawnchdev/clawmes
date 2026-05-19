"""Info / status slash commands.

Five lightweight commands that surface state the user might want
without going through the LLM:

  * ``/history``  — show recent slash-command calls + summaries.
  * ``/version``  — print the clawmes version string.
  * ``/about``    — print a one-paragraph description of clawmes.
  * ``/clear_history`` — wipe the command-history ring (e.g. before a
    screen-share so private summaries don't surface in /history).
  * ``/uptime``   — show how long the current process has been running.

Each handler that's worth remembering records itself to the
:mod:`clawmes.services.command_history` ring. The two sensitive
commands (``/export_wallet``, ``/recover``) deliberately don't —
mnemonics shouldn't appear in a recap.
"""

from __future__ import annotations

import time

from clawmes._version import __version__
from clawmes.services.command_history import record_command_call

_PROCESS_START = time.time()


async def handle_history(raw_args: str) -> str:
    from clawmes.services.command_history import get_command_history_service

    limit_raw = raw_args.strip()
    try:
        limit = max(1, int(limit_raw)) if limit_raw else 10
    except ValueError:
        return f"Bad limit {limit_raw!r}. Usage: /history [N] (default 10, max 20)."
    limit = min(limit, 20)

    entries = get_command_history_service().recent(limit=limit)
    if not entries:
        out = "No recent slash commands recorded this session."
    else:
        lines = [f"Last {len(entries)} slash command call(s) (newest first):"]
        for entry in entries:
            arg_str = f" {entry['args']}" if entry.get("args") else ""
            summary = entry.get("summary", "")
            first_line = summary.splitlines()[0] if summary else "(no output)"
            lines.append(f"  /{entry['name']}{arg_str}")
            lines.append(f"      -> {first_line}")
        out = "\n".join(lines)

    record_command_call("history", raw_args, out)
    return out


async def handle_clear_history(raw_args: str) -> str:
    from clawmes.services.command_history import get_command_history_service

    get_command_history_service().clear()
    out = "Command history cleared."
    # Record AFTER clearing so /history shows the clear itself happened.
    record_command_call("clear_history", raw_args, out)
    return out


async def handle_version(raw_args: str) -> str:
    out = f"clawmes {__version__}"
    record_command_call("version", raw_args, out)
    return out


async def handle_about(raw_args: str) -> str:
    out = (
        "clawmes — the crypto plugin for Hermes Agent.\n\n"
        "Wallets, swaps, DeFi, governance, and on-chain automation, driven from "
        "chat (Telegram, Discord, Slack, Signal, WhatsApp, iMessage, LINE) or the "
        "CLI. WalletConnect / local-key / Bankr wallet modes; user-controlled "
        "spending policies; auditable network allowlist; persona + onboarding "
        "flow tailored to your style.\n\n"
        f"Version: {__version__}\n"
        "Source:  https://github.com/clawnchdev/clawmes\n"
        "License: MIT"
    )
    record_command_call("about", raw_args, out)
    return out


async def handle_uptime(raw_args: str) -> str:
    elapsed = time.time() - _PROCESS_START
    out = f"clawmes uptime: {_format_duration(elapsed)}"
    record_command_call("uptime", raw_args, out)
    return out


def _format_duration(seconds: float) -> str:
    seconds = max(0.0, seconds)
    days, rem = divmod(int(seconds), 86_400)
    hours, rem = divmod(rem, 3_600)
    mins, secs = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if mins:
        parts.append(f"{mins}m")
    parts.append(f"{secs}s")
    return " ".join(parts)


def register(ctx) -> None:
    """Wire info / status commands into Hermes."""
    ctx.register_command(
        name="history",
        handler=handle_history,
        description="Show recent slash-command calls and their result summaries",
        args_hint="[N]",
    )
    ctx.register_command(
        name="clear_history",
        handler=handle_clear_history,
        description="Wipe the command-history ring",
    )
    ctx.register_command(
        name="version",
        handler=handle_version,
        description="Show clawmes version",
    )
    ctx.register_command(
        name="about",
        handler=handle_about,
        description="One-paragraph description of clawmes",
    )
    ctx.register_command(
        name="uptime",
        handler=handle_uptime,
        description="Show clawmes process uptime",
    )
