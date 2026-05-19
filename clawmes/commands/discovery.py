"""Discoverability slash commands.

Five read-only commands that surface what's installed / configured /
running. All wrappers over already-shipped data:

  * ``/skills``        — list bundled clawmes skills (walks
    ``clawmes/skills/*/SKILL.md``).
  * ``/persona``       — show the active persona (or list available
    ones when none is active).
  * ``/chains``        — list every EVM chain the chain registry
    knows about (``clawmes/lib/chains.CHAINS``).
  * ``/tools_list``    — list every clawmes tool from the
    plugin manifest's ``provides_tools`` array.
  * ``/safety_status`` — show the current
    :class:`clawmes.services.mode_service.ModeService` mode and what
    that means for write tools.

Why these aren't covered by ``/doctor``: doctor is for *diagnostic*
output (what's broken, what's missing). These are *navigational* (what
exists, what's available). Different surface, different audience.
"""

from __future__ import annotations

import importlib.resources
from pathlib import Path

from clawmes.onboarding.personas import PERSONAS


async def handle_skills(raw_args: str) -> str:
    skills_dir = Path(__file__).parent.parent / "skills"
    if not skills_dir.exists():
        return "No clawmes skills directory found. (Expected clawmes/skills/.)"

    entries: list[tuple[str, str]] = []
    for child in sorted(skills_dir.iterdir()):
        if not child.is_dir():
            continue
        skill_md = child / "SKILL.md"
        if not skill_md.exists():
            continue
        description = _read_skill_description(skill_md)
        entries.append((child.name, description))

    if not entries:
        return "No clawmes skills installed."

    lines = [f"{len(entries)} clawmes skill(s) bundled:"]
    for name, description in entries:
        head = description.splitlines()[0] if description else "(no description)"
        if len(head) > 100:
            head = head[:97] + "..."
        lines.append(f"  clawmes:{name} - {head}")
    return "\n".join(lines)


def _read_skill_description(skill_md: Path) -> str:
    """Extract the YAML-frontmatter ``description:`` line.

    Same string-scan approach the registry uses
    (:mod:`clawmes.skills.__init__`) to avoid a yaml dependency.
    """
    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError:
        return ""
    in_frontmatter = False
    for line in text.splitlines():
        if line.strip() == "---":
            if not in_frontmatter:
                in_frontmatter = True
                continue
            break
        if in_frontmatter and line.lower().startswith("description:"):
            return line.split(":", 1)[1].strip().strip("\"'")
    return ""


async def handle_persona(raw_args: str) -> str:
    from clawmes.services.persona_service import get_persona_service

    svc = get_persona_service()
    active = svc.active_persona()
    if active is not None:
        return (
            f"Active persona: {active.name}\n"
            f"  Tagline: {active.tagline}\n\n"
            "Switch via /professional, /degen, /chill, /technical, /mentor."
        )

    lines = [
        "No active persona. Available choices:",
    ]
    for name, persona in PERSONAS.items():
        lines.append(f"  /{name} - {persona.tagline}")
    return "\n".join(lines)


async def handle_chains(raw_args: str) -> str:
    from clawmes.lib.chains import CHAINS, default_chain_id
    from clawmes.services.rpc import get_rpc_service

    rpc = get_rpc_service()
    default = default_chain_id()

    lines = [f"{len(CHAINS)} supported chain(s) (default = chain id {default}):"]
    for chain_id, chain in sorted(CHAINS.items()):
        rpc_marker = "[rpc]" if rpc.has_endpoint(chain_id) else "[no-rpc]"
        l2_marker = " (L2)" if chain.is_l2 else ""
        default_marker = "*" if chain_id == default else " "
        lines.append(
            f" {default_marker}{rpc_marker:<8} {chain_id:>6}  "
            f"{chain.short_name:<10} {chain.name}{l2_marker}"
        )
    return "\n".join(lines)


async def handle_tools_list(raw_args: str) -> str:
    manifest = _read_manifest_tools()
    if manifest is None:
        return "Could not locate plugin.yaml — clawmes installation is missing it."
    if not manifest:
        return "No tools declared in plugin.yaml."
    lines = [f"{len(manifest)} clawmes tool(s) declared in plugin.yaml:"]
    for name in sorted(manifest):
        lines.append(f"  {name}")
    return "\n".join(lines)


def _read_manifest_tools() -> list[str] | None:
    """Parse ``provides_tools`` from the bundled plugin.yaml.

    Avoids the YAML dependency for this hot path. The schema is stable
    enough for a string scan and matches what ``tests/test_plugin_manifest``
    enforces.
    """
    try:
        text = importlib.resources.files("clawmes").joinpath("plugin.yaml").read_text()
    except (OSError, ModuleNotFoundError):
        return None

    tools: list[str] = []
    in_provides = False
    for line in text.splitlines():
        if line.startswith("provides_tools:"):
            in_provides = True
            continue
        if in_provides:
            stripped = line.rstrip()
            if stripped.startswith("  - "):
                tools.append(stripped[4:].strip())
            elif stripped and not stripped.startswith(" "):
                break  # Next top-level key — provides_tools section ended.
    return tools


async def handle_safety_status(raw_args: str) -> str:
    from clawmes.services.mode_service import get_mode_service

    mode = get_mode_service().mode
    if mode == "normal":
        return (
            "Safety mode: NORMAL.\n"
            "  Readonly check:  off (writes pass to policy gate)\n"
            "  Danger override: off\n"
            "Switch via /safemode (lock writes) or /dangermode (bypass readonly)."
        )
    if mode == "readonly":
        return (
            "Safety mode: READONLY.\n"
            "  Every write tool will be blocked at stage 1 of the @write_tool gate.\n"
            "  Read tools (defi_price, defi_balance, etc.) still work.\n"
            "Switch via /safemode off or /dangermode to enable writes again."
        )
    # mode == "danger"
    return (
        "Safety mode: DANGER.\n"
        "  Readonly check is BYPASSED. Policy gating still applies.\n"
        "  Use with care — large or irreversible transactions are easier to send.\n"
        "Switch via /safemode (lock writes) to return to a safe posture."
    )


def register(ctx) -> None:
    """Wire discovery commands into Hermes."""
    ctx.register_command(
        name="skills",
        handler=handle_skills,
        description="List bundled clawmes skills",
    )
    ctx.register_command(
        name="persona",
        handler=handle_persona,
        description="Show the active persona (or list available ones)",
    )
    ctx.register_command(
        name="chains",
        handler=handle_chains,
        description="List supported EVM chains + RPC status",
    )
    ctx.register_command(
        name="tools_list",
        handler=handle_tools_list,
        description="List clawmes tools registered in plugin.yaml",
    )
    ctx.register_command(
        name="safety_status",
        handler=handle_safety_status,
        description="Show current safety mode (normal / readonly / danger)",
    )
