"""``nookplot`` — Farcaster creator analytics (read-only).

Three actions:

  * ``analyze``       — top-level engagement stats for a user.
  * ``top_creators``  — leaderboard of high-engagement Farcaster
    creators.
  * ``engagement``    — detailed engagement breakdown for a cast.

Nookplot.xyz provides Farcaster analytics. ``NOOKPLOT_API_KEY``
optional; free tier sufficient for personal use.
"""

from __future__ import annotations

import os
from typing import Any

from clawmes.lib.http import http_get
from clawmes.lib.logger import logger_for
from clawmes.lib.params import read_int, read_str
from clawmes.lib.tool_result import error_result, json_result
from clawmes.tools.registry import read_tool, register_with_ctx

_log = logger_for("tools.nookplot")

_NOOK_BASE = "https://api.nookplot.xyz"

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["analyze", "top_creators", "engagement"],
        },
        "fid": {"type": "integer", "description": "Farcaster user ID."},
        "cast_hash": {"type": "string", "description": "For engagement action."},
        "limit": {"type": "integer", "description": "Default 10."},
    },
    "required": ["action"],
}


@read_tool(
    name="nookplot",
    toolset="clawmes-defi",
    description=(
        "Farcaster creator analytics via Nookplot. Engagement, top creators, per-cast breakdown."
    ),
    schema=_SCHEMA,
    emoji="\U0001f4ca",
)
def nookplot(args: dict[str, Any], **kwargs: Any) -> str:
    api_key = os.environ.get("NOOKPLOT_API_KEY")
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    action = read_str(args, "action", required=True)
    limit = read_int(args, "limit") or 10

    try:
        if action == "analyze":
            fid = read_int(args, "fid")
            if fid is None:
                return error_result("fid required", code="param_error")
            result = http_get(
                f"{_NOOK_BASE}/v1/users/{fid}",
                headers=headers,
                timeout=15.0,
            )
        elif action == "top_creators":
            result = http_get(
                f"{_NOOK_BASE}/v1/creators/top",
                params={"limit": str(limit)},
                headers=headers,
                timeout=15.0,
            )
        else:
            cast_hash = read_str(args, "cast_hash", required=True)
            result = http_get(
                f"{_NOOK_BASE}/v1/casts/{cast_hash}/engagement",
                headers=headers,
                timeout=15.0,
            )
    except Exception as exc:  # noqa: BLE001
        return error_result(f"Nookplot request failed: {exc}", code="api_error")
    return json_result({"action": action, "result": result}, summary=f"nookplot {action}")


def register(ctx) -> None:
    register_with_ctx(ctx, nookplot)
