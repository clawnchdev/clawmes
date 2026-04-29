"""``molten`` — X (Twitter) integration via Molten / X service.

Four actions for X/Twitter agent operations:

  * ``post``     — post a new tweet.
  * ``search``   — search tweets by query.
  * ``mention``  — list recent mentions of the authenticated user.
  * ``dm``       — send a direct message.

Requires ``MOLTEN_API_KEY`` (Molten provides X API access for agents).
"""

from __future__ import annotations

import os
from typing import Any

from clawmes.lib.http import http_get, http_post
from clawmes.lib.logger import logger_for
from clawmes.lib.params import read_int, read_str
from clawmes.lib.tool_result import error_result, json_result
from clawmes.tools.registry import register_with_ctx, write_tool

_log = logger_for("tools.molten")

_MOLTEN_BASE = "https://api.molten.so"

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["post", "search", "mention", "dm"],
        },
        "text": {"type": "string"},
        "query": {"type": "string"},
        "to_user": {"type": "string", "description": "DM recipient handle."},
        "limit": {"type": "integer"},
        "policyConfirmationNonce": {"type": "string"},
    },
    "required": ["action"],
}


@write_tool(
    name="molten",
    toolset="clawmes-defi",
    description=(
        "X (Twitter) integration via Molten. post tweets, search, list mentions, send DMs."
    ),
    schema=_SCHEMA,
    emoji="\U0001f426",
)
def molten(args: dict[str, Any], **kwargs: Any) -> str:
    api_key = os.environ.get("MOLTEN_API_KEY")
    if not api_key:
        return error_result("MOLTEN_API_KEY required.", code="no_credentials")
    headers = {"Authorization": f"Bearer {api_key}"}
    action = read_str(args, "action", required=True)

    try:
        if action == "post":
            text = read_str(args, "text", required=True)
            result = http_post(
                f"{_MOLTEN_BASE}/v1/tweets",
                json={"text": text},
                headers=headers,
                timeout=15.0,
            )
        elif action == "search":
            query = read_str(args, "query", required=True)
            limit = read_int(args, "limit") or 25
            result = http_get(
                f"{_MOLTEN_BASE}/v1/tweets/search",
                params={"q": query, "limit": str(limit)},
                headers=headers,
                timeout=15.0,
            )
        elif action == "mention":
            limit = read_int(args, "limit") or 25
            result = http_get(
                f"{_MOLTEN_BASE}/v1/mentions",
                params={"limit": str(limit)},
                headers=headers,
                timeout=15.0,
            )
        else:
            payload = {
                "to": read_str(args, "to_user", required=True),
                "text": read_str(args, "text", required=True),
            }
            result = http_post(
                f"{_MOLTEN_BASE}/v1/dm",
                json=payload,
                headers=headers,
                timeout=15.0,
            )
    except Exception as exc:  # noqa: BLE001
        return error_result(f"Molten request failed: {exc}", code="api_error")
    return json_result({"action": action, "result": result}, summary=f"molten {action}")


def register(ctx) -> None:
    register_with_ctx(ctx, molten)
