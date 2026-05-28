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
    agent,
    agent_plan,
    alerts,
    allowlist,
    balance,
    burn,
    buy,
    claim,
    copy,
    dca,
    discovery,
    doctor,
    evolve,
    identity,
    info,
    launch,
    leaderboard,
    limit_order,
    my_launches,
    onboarding,
    onramp,
    plans,
    policy,
    sniper,
    trending,
    tx,
    wallet,
    wallet_recovery,
)
from clawmes.commands import (
    bv7x as bv7x_cmd,
)
from clawmes.commands import (
    help as help_cmd,
)

__all__ = ["register_all"]


def register_all(ctx) -> None:
    """Register every clawmes slash command with Hermes."""
    help_cmd.register(ctx)
    wallet.register(ctx)
    wallet_recovery.register(ctx)
    policy.register(ctx)
    tx.register(ctx)
    plans.register(ctx)
    doctor.register(ctx)
    discovery.register(ctx)
    onboarding.register(ctx)
    allowlist.register(ctx)
    balance.register(ctx)
    evolve.register(ctx)
    info.register(ctx)
    identity.register(ctx)
    bv7x_cmd.register(ctx)
    launch.register(ctx)
    agent.register(ctx)
    buy.register(ctx)
    trending.register(ctx)
    my_launches.register(ctx)
    burn.register(ctx)
    onramp.register(ctx)
    leaderboard.register(ctx)
    claim.register(ctx)
    dca.register(ctx)
    copy.register(ctx)
    agent_plan.register(ctx)
    alerts.register(ctx)
    limit_order.register(ctx)
    sniper.register(ctx)
    # TODO(v0.1.0+): model, topup, bankr, delegation, webhook,
    # api_keys, usage, update, reports, interrupts, profile, upgrades,
    # forum, fiat, agents, skills (now /skills), channel
