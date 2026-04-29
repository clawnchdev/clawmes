"""``hummingbot`` — market-making bot management.

Five actions for managing a local Hummingbot instance:

  * ``start``       — start a strategy.
  * ``stop``        — stop a running strategy.
  * ``status``      — get status of running strategies.
  * ``strategies``  — list available strategy templates.
  * ``pnl``         — read current P&L.

Hummingbot runs locally; this tool talks to its REST gateway. Set
``HUMMINGBOT_GATEWAY_URL`` (default http://localhost:15888) and
``HUMMINGBOT_API_KEY`` if your gateway requires auth.
"""

from __future__ import annotations

import os
from typing import Any

from clawmes.lib.http import http_get, http_post
from clawmes.lib.logger import logger_for
from clawmes.lib.params import read_str
from clawmes.lib.tool_result import error_result, json_result
from clawmes.tools.registry import register_with_ctx, write_tool

_log = logger_for("tools.hummingbot")

_DEFAULT_GATEWAY = "http://localhost:15888"

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["start", "stop", "status", "strategies", "pnl"],
        },
        "strategy_id": {"type": "string"},
        "config": {
            "type": "object",
            "description": "Strategy config (start action).",
        },
        "policyConfirmationNonce": {"type": "string"},
    },
    "required": ["action"],
}


@write_tool(
    name="hummingbot",
    toolset="clawmes-defi",
    description=(
        "Market-making bot management via local Hummingbot gateway. "
        "Start/stop strategies, check status, list templates, read P&L. "
        "Requires Hummingbot running locally."
    ),
    schema=_SCHEMA,
    emoji="\U0001f41d",
)
def hummingbot(args: dict[str, Any], **kwargs: Any) -> str:
    base = os.environ.get("HUMMINGBOT_GATEWAY_URL") or _DEFAULT_GATEWAY
    headers = {}
    api_key = os.environ.get("HUMMINGBOT_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    action = read_str(args, "action", required=True)

    try:
        if action == "start":
            strategy_id = read_str(args, "strategy_id", required=True)
            config = args.get("config") or {}
            result = http_post(
                f"{base}/strategies/{strategy_id}/start",
                json=config if isinstance(config, dict) else {},
                headers=headers,
                timeout=15.0,
            )
        elif action == "stop":
            strategy_id = read_str(args, "strategy_id", required=True)
            result = http_post(
                f"{base}/strategies/{strategy_id}/stop",
                json={},
                headers=headers,
                timeout=15.0,
            )
        elif action == "status":
            result = http_get(f"{base}/status", headers=headers, timeout=15.0)
        elif action == "strategies":
            result = http_get(f"{base}/strategies", headers=headers, timeout=15.0)
        else:
            result = http_get(f"{base}/pnl", headers=headers, timeout=15.0)
    except Exception as exc:  # noqa: BLE001
        return error_result(f"Hummingbot gateway request failed: {exc}", code="api_error")
    return json_result({"action": action, "result": result}, summary=f"hummingbot {action}")


def register(ctx) -> None:
    register_with_ctx(ctx, hummingbot)
