"""Bundled clawmes skills.

Skills live as ``clawmes/skills/<name>/SKILL.md`` directories. Each is
registered with Hermes via :func:`register_all`, namespaced as
``clawmes:<name>``. They are NOT in the system prompt's
``<available_skills>`` index by default — opt-in explicit loads via
``skill_view("clawmes:<name>")``. Users who want them globally
discoverable can run ``hermes clawmes skills install`` to copy bundles
into ``~/.hermes/skills/clawmes/`` (writable namespace).

This avoids polluting the global skill namespace and respects Hermes'
read-only-bundled-skill rule.
"""

from __future__ import annotations

from pathlib import Path

from clawmes.lib.logger import logger_for

_log = logger_for("skills")
_SKILLS_DIR = Path(__file__).parent

__all__ = ["register_all"]


def register_all(ctx) -> None:
    """Walk ``clawmes/skills/`` and register each via ``ctx.register_skill``."""
    if not _SKILLS_DIR.exists():
        return
    for child in sorted(_SKILLS_DIR.iterdir()):
        if not child.is_dir():
            continue
        skill_md = child / "SKILL.md"
        if not skill_md.exists():
            continue
        description = _extract_description(skill_md)
        try:
            ctx.register_skill(name=child.name, path=skill_md, description=description)
            _log.debug("registered skill clawmes:%s", child.name)
        except Exception:
            _log.exception("failed to register skill %s", child.name)


def _extract_description(skill_md: Path) -> str:
    """Read the YAML frontmatter ``description`` line.

    Avoids a yaml dependency for this hot path — the frontmatter format
    is stable enough for a string scan.
    """
    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError:
        return ""
    if not text.startswith("---"):
        return ""
    # Walk to the closing '---' and look for 'description:' lines
    for line in text.splitlines()[1:]:
        if line.strip() == "---":
            break
        if line.startswith("description:"):
            return line.split(":", 1)[1].strip().strip('"').strip("'")
    return ""
