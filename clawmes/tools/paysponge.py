"""``paysponge`` — fiat on/off-ramp via Paysponge.

Four actions:

  * ``quote``       — get a fiat→crypto or crypto→fiat quote.
  * ``buy``         — execute a buy (fiat → crypto).
  * ``sell``        — execute a sell (crypto → fiat).
  * ``kyc_status``  — check the user's KYC verification status.

Requires ``PAYSPONGE_API_KEY``. KYC is required for buy/sell actions
above $1k/day per regulatory framework.
"""

from __future__ import annotations

import os
from typing import Any

from clawmes.lib.http import http_get, http_post
from clawmes.lib.logger import logger_for
from clawmes.lib.params import read_str
from clawmes.lib.tool_result import error_result, json_result
from clawmes.tools.registry import register_with_ctx, write_tool

_log = logger_for("tools.paysponge")

_PAYSPONGE_BASE = "https://api.paysponge.xyz"

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["quote", "buy", "sell", "kyc_status"],
        },
        "from_currency": {"type": "string"},
        "to_currency": {"type": "string"},
        "amount": {"type": "string"},
        "destination": {
            "type": "string",
            "description": "Destination wallet for buy, bank for sell.",
        },
        "policyConfirmationNonce": {
            "type": "string",
            "description": "Set when retrying after POLICY HOLD.",
        },
    },
    "required": ["action"],
}


@write_tool(
    name="paysponge",
    toolset="clawmes-defi",
    description=(
        "Fiat on/off-ramp via Paysponge. quote returns a price; "
        "buy/sell execute (KYC required); kyc_status checks "
        "verification."
    ),
    schema=_SCHEMA,
    emoji="\U0001f4b3",
)
def paysponge(args: dict[str, Any], **kwargs: Any) -> str:
    api_key = os.environ.get("PAYSPONGE_API_KEY")
    if not api_key:
        return error_result(
            "PAYSPONGE_API_KEY required. Sign up at https://paysponge.xyz",
            code="no_credentials",
        )
    headers = {"Authorization": f"Bearer {api_key}"}
    action = read_str(args, "action", required=True)

    try:
        if action == "quote":
            params = {
                "from": read_str(args, "from_currency", required=True),
                "to": read_str(args, "to_currency", required=True),
                "amount": read_str(args, "amount", required=True),
            }
            result = http_get(
                f"{_PAYSPONGE_BASE}/v1/quote",
                params=params,
                headers=headers,
                timeout=15.0,
            )
        elif action in ("buy", "sell"):
            payload = {
                "from": read_str(args, "from_currency", required=True),
                "to": read_str(args, "to_currency", required=True),
                "amount": read_str(args, "amount", required=True),
                "destination": read_str(args, "destination", required=True),
            }
            result = http_post(
                f"{_PAYSPONGE_BASE}/v1/{action}",
                json=payload,
                headers=headers,
                timeout=30.0,
            )
        else:
            result = http_get(
                f"{_PAYSPONGE_BASE}/v1/kyc/status",
                headers=headers,
                timeout=15.0,
            )
    except Exception as exc:  # noqa: BLE001
        return error_result(f"Paysponge request failed: {exc}", code="api_error")
    return json_result({"action": action, "result": result}, summary=f"paysponge {action}")


def register(ctx) -> None:
    register_with_ctx(ctx, paysponge)
