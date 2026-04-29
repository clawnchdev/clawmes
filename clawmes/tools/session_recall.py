"""``session_recall`` — search and summarize past sessions.

Three actions:

  * ``search``    — full-text search across past session transcripts.
  * ``summarize`` — return a summary of a specific session by ID.
  * ``recent``    — list N most-recent sessions.

Reads from Hermes' session store (typically
``${HERMES_HOME}/sessions/``). Read-only; doesn't mutate session
data.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from clawmes.lib.logger import logger_for
from clawmes.lib.params import read_int, read_str
from clawmes.lib.paths import hermes_home
from clawmes.lib.tool_result import error_result, json_result
from clawmes.tools.registry import read_tool, register_with_ctx

_log = logger_for("tools.session_recall")


def _sessions_dir() -> Path:
    return hermes_home() / "sessions"


_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["search", "summarize", "recent"],
        },
        "query": {"type": "string"},
        "session_id": {"type": "string"},
        "limit": {"type": "integer"},
    },
    "required": ["action"],
}


@read_tool(
    name="session_recall",
    toolset="clawmes-misc",
    description=(
        "Search and summarize past Hermes sessions. search returns "
        "matching session IDs; summarize returns a session's summary; "
        "recent lists the N latest sessions."
    ),
    schema=_SCHEMA,
    emoji="\U0001f4d6",
)
def session_recall(args: dict[str, Any], **kwargs: Any) -> str:
    action = read_str(args, "action", required=True)
    base = _sessions_dir()

    # For recent / search, an empty / missing dir → empty result.
    # For summarize, the specific session needs to exist; fall through
    # to the normal path which produces a not_found error.
    if not base.exists() and action != "summarize":
        return json_result(
            {"action": action, "sessions": []},
            summary="No sessions directory found.",
        )

    if action == "recent":
        limit = read_int(args, "limit") or 10
        sessions = sorted(
            base.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:limit]
        items = []
        for p in sessions:
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                items.append(
                    {
                        "id": p.stem,
                        "started_at": data.get("started_at"),
                        "title": data.get("title", "(untitled)"),
                    }
                )
            except (json.JSONDecodeError, OSError):
                continue
        return json_result(
            {"count": len(items), "sessions": items},
            summary=f"{len(items)} recent session(s)",
        )

    if action == "summarize":
        session_id = read_str(args, "session_id", required=True)
        path = base / f"{session_id}.json"
        if not path.exists():
            return error_result(f"Session {session_id!r} not found", code="not_found")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            return error_result(f"Could not read session: {exc}", code="not_found")
        return json_result(
            {"session_id": session_id, "data": data},
            summary=(
                f"Session {session_id}: "
                f"{data.get('title', '?')} "
                f"({len(data.get('messages', []))} messages)"
            ),
        )

    # search
    query = read_str(args, "query", required=True).lower()
    limit = read_int(args, "limit") or 10
    matches = []
    for p in base.glob("*.json"):
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        if query in text.lower():
            matches.append({"id": p.stem, "path": str(p)})
            if len(matches) >= limit:
                break
    return json_result(
        {"query": query, "count": len(matches), "matches": matches},
        summary=f"{len(matches)} session(s) match {query!r}",
    )


def register(ctx) -> None:
    register_with_ctx(ctx, session_recall)
