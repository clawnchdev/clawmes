"""``bankr_polymarket`` — Polymarket prediction-market via Bankr.

Bankr-only feature (no non-Bankr alternative): Polymarket execution
on Polygon, custodial-side. Five actions:

  * ``markets``   — list available markets.
  * ``positions`` — list user's open positions.
  * ``bet``       — buy a YES / NO position.
  * ``sell``      — close a position.
  * ``claim``     — claim winnings on resolved markets.
"""

from __future__ import annotations

from typing import Any

from clawmes.lib.logger import logger_for
from clawmes.lib.params import read_str
from clawmes.lib.tool_result import error_result, json_result
from clawmes.services.bankr_service import BankrError, get_bankr_service
from clawmes.tools.registry import register_with_ctx, write_tool

_log = logger_for("tools.bankr_polymarket")

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["markets", "positions", "bet", "sell", "claim"],
        },
        "payload": {
            "type": "object",
            "description": (
                "Action-specific body. bet: market_id / outcome / amount. "
                "sell: position_id / amount. claim: position_id."
            ),
        },
        "policyConfirmationNonce": {
            "type": "string",
            "description": "Set when retrying after POLICY HOLD.",
        },
    },
    "required": ["action"],
}


@write_tool(
    name="bankr_polymarket",
    toolset="clawmes-bankr",
    description=(
        "Polymarket prediction markets via Bankr (Polygon, custodial). "
        "List markets / positions; bet, sell, claim. Bankr handles the "
        "underlying contract calls."
    ),
    schema=_SCHEMA,
    emoji="\U0001f3b2",
)
def bankr_polymarket(args: dict[str, Any], **kwargs: Any) -> str:
    action = read_str(args, "action", required=True)

    try:
        if action in ("markets", "positions"):
            result = get_bankr_service().request("GET", f"/v1/polymarket/{action}")
        else:
            payload = args.get("payload")
            if not isinstance(payload, dict):
                return error_result(
                    f"{action} requires a 'payload' dict",
                    code="param_error",
                )
            result = get_bankr_service().request("POST", f"/v1/polymarket/{action}", body=payload)
    except BankrError as exc:
        return error_result(exc.message, code=exc.code)

    return json_result(
        {"action": action, "result": result},
        summary=f"bankr_polymarket {action}",
    )


def register(ctx) -> None:
    register_with_ctx(ctx, bankr_polymarket)
