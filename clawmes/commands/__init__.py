"""Slash commands.

Each module under ``clawmes/commands/`` exposes one or more
``register(ctx)`` calls that wire ``ctx.register_command(name, handler,
description, args_hint)``. Commands run synchronously in the gateway /
CLI loop and bypass the LLM (no inference cost).

Hermes' ``COMMAND_REGISTRY`` automatically surfaces registered commands
in autocomplete, ``/help``, the Telegram bot menu, and the Slack
subcommand router. Conflicts with built-in commands are silently
rejected.
"""

from __future__ import annotations

from clawmes.commands import (
    bv7x as bv7x_cmd,
)
from clawmes.commands import (
    doctor,
    plans,
    policy,
    tx,
    wallet,
)
from clawmes.commands import (
    help as help_cmd,
)

__all__ = ["register_all"]


def register_all(ctx) -> None:
    """Register every clawmes slash command with Hermes."""
    help_cmd.register(ctx)
    wallet.register(ctx)
    policy.register(ctx)
    tx.register(ctx)
    plans.register(ctx)
    doctor.register(ctx)
    bv7x_cmd.register(ctx)
    # TODO(v0.1.0+): onboarding, model, topup, bankr, delegation, webhook,
    # api_keys, usage, update, reports, interrupts, profile, upgrades,
    # forum, fiat, agents, skills, channel
