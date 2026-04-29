"""``herd_intelligence`` — whale + smart-money tracking via Herd.

Four actions:

  * ``swaps``           — recent large swaps across DEXes.
  * ``wallet_activity`` — activity for a specific wallet.
  * ``whale_alerts``    — alerts for transactions above thresholds.
  * ``subscribe``       — register for ongoing alerts (Hermes cron picks up).

``HERD_ACCESS_TOKEN`` required. Herd aggregates DEX trades + wallet
labels for the canonical "what are smart-money traders doing right
now" view.
"""

from __future__ import annotations

import os
from typing import Any

from clawmes.lib.http import http_get, http_post
from clawmes.lib.logger import logger_for
from clawmes.lib.params import read_int, read_str
from clawmes.lib.tool_result import error_result, json_result
from clawmes.tools.registry import read_tool, register_with_ctx

_log = logger_for("tools.herd_intelligence")

_HERD_BASE = "https://api.herd.eco"

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["swaps", "wallet_activity", "whale_alerts", "subscribe"],
        },
        "address": {"type": "string"},
        "min_usd": {
            "type": "number",
            "description": "Minimum USD threshold (whale_alerts).",
        },
        "limit": {"type": "integer"},
        "filters": {
            "type": "object",
            "description": "Subscribe filter config.",
        },
    },
    "required": ["action"],
}


@read_tool(
    name="herd_intelligence",
    toolset="clawmes-defi",
    description=(
        "Whale + smart-money tracking via Herd. Recent large swaps, "
        "wallet activity, threshold-triggered alerts, ongoing "
        "subscriptions for Hermes' cron daemon."
    ),
    schema=_SCHEMA,
    emoji="\U0001f40b",
)
def herd_intelligence(args: dict[str, Any], **kwargs: Any) -> str:
    api_key = os.environ.get("HERD_ACCESS_TOKEN")
    if not api_key:
        return error_result("HERD_ACCESS_TOKEN required.", code="no_credentials")
    headers = {"Authorization": f"Bearer {api_key}"}
    action = read_str(args, "action", required=True)
    limit = read_int(args, "limit") or 25

    try:
        if action == "swaps":
            result = http_get(
                f"{_HERD_BASE}/v1/swaps",
                params={"limit": str(limit)},
                headers=headers,
                timeout=15.0,
            )
        elif action == "wallet_activity":
            address = read_str(args, "address", required=True)
            result = http_get(
                f"{_HERD_BASE}/v1/wallets/{address}/activity",
                params={"limit": str(limit)},
                headers=headers,
                timeout=15.0,
            )
        elif action == "whale_alerts":
            min_usd = float(args.get("min_usd") or 100_000)
            result = http_get(
                f"{_HERD_BASE}/v1/whale-alerts",
                params={
                    "min_usd": str(int(min_usd)),
                    "limit": str(limit),
                },
                headers=headers,
                timeout=15.0,
            )
        else:
            payload = args.get("filters") or {}
            result = http_post(
                f"{_HERD_BASE}/v1/subscribe",
                json=payload if isinstance(payload, dict) else {},
                headers=headers,
                timeout=15.0,
            )
    except Exception as exc:  # noqa: BLE001
        return error_result(f"Herd request failed: {exc}", code="api_error")
    return json_result({"action": action, "result": result}, summary=f"herd {action}")


def register(ctx) -> None:
    register_with_ctx(ctx, herd_intelligence)
