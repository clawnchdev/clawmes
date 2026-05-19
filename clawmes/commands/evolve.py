"""Evolution-mode slash commands.

Three commands wrapping :class:`clawmes.services.evolution_mode.EvolutionModeService`:

  * ``/evolve``    — enable self-modification (memory + skill writes)
  * ``/stable``    — disable self-modification (default)
  * ``/evolution`` — show current state

When disabled (the default), write actions on ``agent_memory`` and
``skill_evolve`` are gated with an ``evolution_gate`` error. Read
actions (``query``, ``list``) are always allowed.

The toggle is global / in-memory and resets to ``stable`` on
restart. This is intentional: an attacker who gets the agent to
flip the bit can't keep it set across sessions.
"""

from __future__ import annotations


async def handle_evolve(raw_args: str) -> str:
    from clawmes.services.evolution_mode import get_evolution_mode_service

    state = get_evolution_mode_service().set_evolving(True)
    if not state:
        # Defensive — should not be reachable with set_evolving(True).
        return "Evolution mode toggle failed."
    return (
        "Evolution mode ENABLED. The agent can now write to its own memory "
        "(/agent_memory add/replace/remove) and self-modify its skills "
        "(skill_evolve propose/update/revert). Use /stable to lock back."
    )


async def handle_stable(raw_args: str) -> str:
    from clawmes.services.evolution_mode import get_evolution_mode_service

    get_evolution_mode_service().set_evolving(False)
    return (
        "Evolution mode DISABLED. Memory writes and skill self-modification "
        "are blocked. Read actions (query / list) still work. Use /evolve "
        "to re-enable when you're ready."
    )


async def handle_evolution(raw_args: str) -> str:
    from clawmes.services.evolution_mode import get_evolution_mode_service

    state = get_evolution_mode_service().is_evolving()
    label = "ENABLED" if state else "DISABLED"
    return "\n".join(
        [
            f"Evolution mode: {label}",
            "",
            "When disabled (the safe default), the following actions are blocked:",
            "  * agent_memory: add, replace, remove",
            "  * skill_evolve: propose, update, revert",
            "",
            "When enabled, all actions are permitted. Toggle:",
            "  /evolve  — turn ON  (allow self-modification)",
            "  /stable  — turn OFF (block self-modification, default)",
        ]
    )


def register(ctx) -> None:
    """Wire evolution-mode commands into Hermes."""
    ctx.register_command(
        name="evolve",
        handler=handle_evolve,
        description="Enable self-modifying writes (memory + skill_evolve)",
    )
    ctx.register_command(
        name="stable",
        handler=handle_stable,
        description="Disable self-modifying writes — the safe default",
    )
    ctx.register_command(
        name="evolution",
        handler=handle_evolution,
        description="Show evolution-mode status",
    )
