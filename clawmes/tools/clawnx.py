"""``clawnx`` — agent-to-agent matching via Clawnx network.

Four actions:

  * ``match``    — find agents matching a request.
  * ``list``     — list active agents on the network.
  * ``request``  — send a job request to a specific agent.
  * ``accept``   — accept an incoming request from another agent.

Requires ``CLAWNX_API_KEY``. The agent network is the canonical
Clawnch infrastructure for agent-to-agent commerce.
"""

from __future__ import annotations

import os
from typing import Any

from clawmes.lib.http import http_get, http_post
from clawmes.lib.logger import logger_for
from clawmes.lib.params import read_str
from clawmes.lib.tool_result import error_result, json_result
from clawmes.tools.registry import register_with_ctx, write_tool

_log = logger_for("tools.clawnx")

_CLAWNX_BASE = "https://api.clawnx.io"

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["match", "list", "request", "accept"],
        },
        "criteria": {
            "type": "object",
            "description": "Match criteria (skills, price, etc.).",
        },
        "agent_id": {"type": "string"},
        "request_id": {"type": "string", "description": "For accept."},
        "payload": {"type": "object"},
        "policyConfirmationNonce": {"type": "string"},
    },
    "required": ["action"],
}


@write_tool(
    name="clawnx",
    toolset="clawmes-defi",
    description=(
        "Agent-to-agent matching on the Clawnx network. Find agents, "
        "list active ones, send job requests, accept incoming requests."
    ),
    schema=_SCHEMA,
    emoji="\U0001f91d",
)
def clawnx(args: dict[str, Any], **kwargs: Any) -> str:
    api_key = os.environ.get("CLAWNX_API_KEY")
    if not api_key:
        return error_result("CLAWNX_API_KEY required.", code="no_credentials")
    headers = {"Authorization": f"Bearer {api_key}"}
    action = read_str(args, "action", required=True)

    try:
        if action == "match":
            criteria = args.get("criteria") or {}
            result = http_post(
                f"{_CLAWNX_BASE}/v1/match",
                json=criteria if isinstance(criteria, dict) else {},
                headers=headers,
                timeout=15.0,
            )
        elif action == "list":
            result = http_get(f"{_CLAWNX_BASE}/v1/agents", headers=headers, timeout=15.0)
        elif action == "request":
            agent_id = read_str(args, "agent_id", required=True)
            payload = args.get("payload") or {}
            result = http_post(
                f"{_CLAWNX_BASE}/v1/agents/{agent_id}/request",
                json=payload if isinstance(payload, dict) else {},
                headers=headers,
                timeout=15.0,
            )
        else:
            request_id = read_str(args, "request_id", required=True)
            result = http_post(
                f"{_CLAWNX_BASE}/v1/requests/{request_id}/accept",
                json={},
                headers=headers,
                timeout=15.0,
            )
    except Exception as exc:  # noqa: BLE001
        return error_result(f"Clawnx request failed: {exc}", code="api_error")
    return json_result({"action": action, "result": result}, summary=f"clawnx {action}")


def register(ctx) -> None:
    register_with_ctx(ctx, clawnx)
