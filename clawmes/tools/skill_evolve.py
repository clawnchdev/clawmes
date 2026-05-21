"""``skill_evolve`` — agentic skill self-improvement.

Four actions:

  * ``propose``  — propose a skill update based on observed user
    interactions. Stored as a pending proposal awaiting user review.
  * ``update``   — apply a previously-proposed update.
  * ``list``     — list pending and applied updates.
  * ``revert``   — roll back an applied update.

Updates are stored as JSON files under
``${HERMES_HOME}/clawmes/skill_evolution/`` — proposals/, applied/,
and reverted/ subdirectories. Hermes' SOUL.md sees the applied
updates on subsequent sessions.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from clawmes.lib.logger import logger_for
from clawmes.lib.params import read_str
from clawmes.lib.paths import hermes_home
from clawmes.lib.tool_result import error_result, json_result
from clawmes.tools.registry import register_with_ctx, write_tool

_log = logger_for("tools.skill_evolve")


def _evolution_dir() -> Path:
    return hermes_home() / "clawmes" / "skill_evolution"


def _list_dir(subdir: str) -> list[dict[str, Any]]:
    d = _evolution_dir() / subdir
    if not d.exists():
        return []
    out = []
    for p in sorted(d.glob("*.json")):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            continue
    return out


_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["propose", "update", "list", "revert"],
        },
        "skill": {"type": "string", "description": "Skill name or path."},
        "change": {
            "type": "string",
            "description": "Description of the change (propose).",
        },
        "proposal_id": {
            "type": "string",
            "description": "ID for update / revert.",
        },
        "policyConfirmationNonce": {"type": "string"},
    },
    "required": ["action"],
}


@write_tool(
    name="skill_evolve",
    toolset="clawmes-misc",
    description=(
        "Propose, apply, list, or revert agentic skill updates. "
        "Updates persist to HERMES_HOME/clawmes/skill_evolution/ "
        "and influence subsequent sessions."
    ),
    schema=_SCHEMA,
    emoji="\U0001f9ec",
)
def skill_evolve(args: dict[str, Any], **kwargs: Any) -> str:
    action = read_str(args, "action", required=True)
    if action in ("propose", "update", "revert"):
        from clawmes.services.evolution_mode import is_evolving

        if not is_evolving():
            return error_result(
                "Evolution mode is disabled. skill_evolve write actions "
                "(propose / update / revert) require /evolve. Use "
                "/evolution to check status; the safe default is OFF so "
                "a prompt-injected LLM can't silently rewrite your skills.",
                code="evolution_gate",
            )
    base = _evolution_dir()

    if action == "list":
        return json_result(
            {
                "proposals": _list_dir("proposals"),
                "applied": _list_dir("applied"),
                "reverted": _list_dir("reverted"),
            },
            summary="skill evolution status",
        )

    if action == "propose":
        skill = read_str(args, "skill", required=True)
        change = read_str(args, "change", required=True)
        proposal_id = f"prop-{int(time.time())}"
        d = base / "proposals"
        d.mkdir(parents=True, exist_ok=True)
        record = {
            "id": proposal_id,
            "skill": skill,
            "change": change,
            "ts": time.time(),
        }
        (d / f"{proposal_id}.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
        return json_result(record, summary=f"Proposed {proposal_id} for {skill}")

    if action == "update":
        proposal_id = read_str(args, "proposal_id", required=True)
        src = base / "proposals" / f"{proposal_id}.json"
        if not src.exists():
            return error_result(f"Proposal {proposal_id!r} not found", code="not_found")
        dst_dir = base / "applied"
        dst_dir.mkdir(parents=True, exist_ok=True)
        record = json.loads(src.read_text(encoding="utf-8"))
        record["applied_at"] = time.time()
        (dst_dir / f"{proposal_id}.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
        src.unlink()
        return json_result(record, summary=f"Applied {proposal_id}")

    # revert
    proposal_id = read_str(args, "proposal_id", required=True)
    src = base / "applied" / f"{proposal_id}.json"
    if not src.exists():
        return error_result(f"Applied update {proposal_id!r} not found", code="not_found")
    dst_dir = base / "reverted"
    dst_dir.mkdir(parents=True, exist_ok=True)
    record = json.loads(src.read_text(encoding="utf-8"))
    record["reverted_at"] = time.time()
    (dst_dir / f"{proposal_id}.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    src.unlink()
    return json_result(record, summary=f"Reverted {proposal_id}")


def register(ctx) -> None:
    register_with_ctx(ctx, skill_evolve)
