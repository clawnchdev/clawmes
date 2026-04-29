"""``wayfinder`` — multi-step route optimization.

Three actions:

  * ``route``    — given a desired end state (token X on chain Y),
    plan the cheapest / fastest path through bridges + swaps.
  * ``compare``  — compare alternative routes side-by-side.
  * ``optimize`` — refine a route against a constraint (gas budget,
    max steps, time limit).

Wayfinder is the route-optimization service. ``WAYFINDER_API_KEY``
required.
"""

from __future__ import annotations

import os
from typing import Any

from clawmes.lib.http import http_post
from clawmes.lib.logger import logger_for
from clawmes.lib.params import read_str
from clawmes.lib.tool_result import error_result, json_result
from clawmes.tools.registry import read_tool, register_with_ctx

_log = logger_for("tools.wayfinder")

_WAYFINDER_BASE = "https://api.wayfinder.xyz"

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["route", "compare", "optimize"],
        },
        "from_chain": {"type": "integer"},
        "from_token": {"type": "string"},
        "to_chain": {"type": "integer"},
        "to_token": {"type": "string"},
        "amount": {"type": "string"},
        "constraints": {
            "type": "object",
            "description": "Optimization constraints.",
        },
    },
    "required": ["action"],
}


@read_tool(
    name="wayfinder",
    toolset="clawmes-defi",
    description=(
        "Multi-step route optimization. Plans the cheapest/fastest path "
        "through bridges + swaps to get from (chain X, token A) to "
        "(chain Y, token B). Read-only: returns route plans without "
        "executing."
    ),
    schema=_SCHEMA,
    emoji="\U0001f9ed",
)
def wayfinder(args: dict[str, Any], **kwargs: Any) -> str:
    api_key = os.environ.get("WAYFINDER_API_KEY")
    if not api_key:
        return error_result("WAYFINDER_API_KEY required.", code="no_credentials")
    headers = {"Authorization": f"Bearer {api_key}"}
    action = read_str(args, "action", required=True)

    payload: dict[str, Any] = {
        "from_chain": args.get("from_chain"),
        "from_token": args.get("from_token"),
        "to_chain": args.get("to_chain"),
        "to_token": args.get("to_token"),
        "amount": args.get("amount"),
    }
    if action == "optimize":
        constraints = args.get("constraints") or {}
        payload["constraints"] = constraints if isinstance(constraints, dict) else {}

    try:
        result = http_post(
            f"{_WAYFINDER_BASE}/v1/{action}",
            json=payload,
            headers=headers,
            timeout=20.0,
        )
    except Exception as exc:  # noqa: BLE001
        return error_result(f"Wayfinder request failed: {exc}", code="api_error")
    return json_result({"action": action, "result": result}, summary=f"wayfinder {action}")


def register(ctx) -> None:
    register_with_ctx(ctx, wayfinder)
