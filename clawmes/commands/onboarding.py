"""Onboarding slash commands.

Slash-command surface over the existing ``persona_service`` and the new
:class:`OnboardingService`. Adds:

  * **1 status command** — ``/welcome`` shows current step, persona,
    capabilities.
  * **5 persona switches** — one per built-in persona in
    :data:`clawmes.onboarding.personas.PERSONAS`. Calling any of them
    sets the active persona for the next LLM turn.
  * **10 capability toggles** — one per entry in
    :data:`clawmes.services.onboarding_service.CAPABILITIES`. Toggle
    with no arg, or pass ``on``/``off`` for explicit state.
    Capabilities are *recorded* today; enforcement (suppressing tool
    registrations based on user picks) is future work.
  * **3 flow controls** — ``/skip``, ``/back``, ``/reonboard``.

Single-user assumption: ``sender_id="default"`` matches
``persona_service`` (in-memory only) and ``policy`` (default user).
Multi-sender persistence is future work.
"""

from __future__ import annotations

from clawmes.onboarding.personas import PERSONAS
from clawmes.services.onboarding_service import CAPABILITIES

# Pre-compute description map for capability commands.
_CAPABILITY_DESCRIPTIONS: dict[str, str] = {cap_id: label for cap_id, label in CAPABILITIES}

_ON_TOKENS = frozenset({"on", "enable", "enabled", "true", "yes", "y", "1"})
_OFF_TOKENS = frozenset({"off", "disable", "disabled", "false", "no", "n", "0"})


# --- Status -------------------------------------------------------------


async def handle_welcome(raw_args: str) -> str:
    from clawmes.services.onboarding_service import get_onboarding_service
    from clawmes.services.persona_service import get_persona_service

    ob = get_onboarding_service()
    state = ob.get_state()
    caps = ob.get_capabilities()
    persona = get_persona_service().active_persona()

    persona_label = persona.name if persona else "(not set)"
    caps_label = ", ".join(sorted(caps)) if caps else "(none selected)"
    return "\n".join(
        [
            "Onboarding status:",
            f"  Step:         {state.step}",
            f"  Persona:      {persona_label}",
            f"  Capabilities: {caps_label}",
            f"  Complete:     {state.complete}",
            "",
            "Commands:",
            "  Personas:     /professional, /degen, /chill, /technical, /mentor",
            "  Capabilities: /cap_<name> (e.g. /cap_trading on)",
            "  Flow:         /skip, /back, /reonboard",
        ]
    )


# --- Persona switches ---------------------------------------------------


def _make_persona_handler(persona_name: str):
    async def handler(raw_args: str) -> str:
        from clawmes.services.onboarding_service import get_onboarding_service
        from clawmes.services.persona_service import get_persona_service

        persona = get_persona_service().set_persona(persona_name)
        if persona is None:
            return f"Failed to load {persona_name!r} persona."
        ob = get_onboarding_service()
        state = ob.get_state()
        # If the user is still in the pick_persona step, advance them
        # to pick_wallet now that they've made a choice.
        if state.step in ("welcome", "pick_persona"):
            ob.advance_step("pick_wallet")
        state.chosen_persona = persona.name
        return f"Persona set to {persona.name}: {persona.tagline}"

    handler.__name__ = f"handle_{persona_name}"
    return handler


# --- Capability toggles -------------------------------------------------


def _parse_toggle(raw_args: str) -> bool | None:
    """Parse the toggle arg.

    Returns ``True`` / ``False`` for explicit on/off, ``None`` for an
    empty arg (which the caller interprets as "flip current state").
    Raises :class:`ValueError` for unrecognized inputs so the command
    can return a clear error instead of silently toggling.
    """
    s = raw_args.strip().lower()
    if not s:
        return None
    if s in _ON_TOKENS:
        return True
    if s in _OFF_TOKENS:
        return False
    raise ValueError(
        f"Unknown toggle value {raw_args.strip()!r}. Use 'on' / 'off' "
        "(or no argument to flip the current state)."
    )


def _make_capability_handler(cap_id: str):
    async def handler(raw_args: str) -> str:
        from clawmes.services.onboarding_service import get_onboarding_service

        try:
            desired = _parse_toggle(raw_args)
        except ValueError as exc:
            return f"Capability error: {exc}"

        ob = get_onboarding_service()
        if desired is None:
            enabled = ob.toggle_capability(cap_id)
        else:
            enabled = ob.set_capability(cap_id, desired)

        verb = "enabled" if enabled else "disabled"
        return f"Capability {cap_id!r} {verb}: {_CAPABILITY_DESCRIPTIONS[cap_id]}"

    handler.__name__ = f"handle_cap_{cap_id}"
    return handler


# --- Flow controls ------------------------------------------------------


async def handle_skip(raw_args: str) -> str:
    from clawmes.services.onboarding_service import get_onboarding_service

    state = get_onboarding_service().skip()
    if state.complete:
        return "Onboarding complete. Welcome!"
    return f"Skipped to next step: {state.step}"


async def handle_back(raw_args: str) -> str:
    from clawmes.services.onboarding_service import get_onboarding_service

    state = get_onboarding_service().back()
    if state is None:
        return "No previous step to go back to. (Use /reonboard to restart from welcome.)"
    return f"Stepped back to: {state.step}"


async def handle_reonboard(raw_args: str) -> str:
    from clawmes.services.onboarding_service import get_onboarding_service

    state = get_onboarding_service().reonboard()
    return (
        f"Onboarding reset. Current step: {state.step}.\n"
        "Persona cleared. Choose one of /professional, /degen, /chill, "
        "/technical, /mentor to set a new one."
    )


# --- Registration -------------------------------------------------------


def register(ctx) -> None:
    """Wire every onboarding command into Hermes."""
    ctx.register_command(
        name="welcome",
        handler=handle_welcome,
        description="Show current onboarding state: step, persona, capabilities",
    )

    for name, persona in PERSONAS.items():
        ctx.register_command(
            name=name,
            handler=_make_persona_handler(name),
            description=f"Set the {name!r} persona — {persona.tagline}",
        )

    for cap_id, label in _CAPABILITY_DESCRIPTIONS.items():
        ctx.register_command(
            name=f"cap_{cap_id}",
            handler=_make_capability_handler(cap_id),
            description=f"Toggle {label}",
            args_hint="[on|off]",
        )

    ctx.register_command(
        name="skip",
        handler=handle_skip,
        description="Skip to the next onboarding step",
    )
    ctx.register_command(
        name="back",
        handler=handle_back,
        description="Go back to the previous onboarding step",
    )
    ctx.register_command(
        name="reonboard",
        handler=handle_reonboard,
        description="Reset onboarding state and clear the active persona",
    )
