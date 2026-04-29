"""``bankr_leverage`` — leveraged trading via Bankr / Avantis.

Bankr-only feature (no non-Bankr alternative): perpetual futures
on Avantis (the leading Base perp DEX) executed through Bankr's
custodial layer. Five actions:

  * ``open``      — open a leveraged long / short position.
  * ``close``     — close a position fully.
  * ``adjust``    — modify size / leverage / TP-SL of an open
    position.
  * ``positions`` — list open positions.
  * ``funding``   — read current funding rate for a market.
"""

from __future__ import annotations

from typing import Any

from clawmes.lib.logger import logger_for
from clawmes.lib.params import read_str
from clawmes.lib.tool_result import error_result, json_result
from clawmes.services.bankr_service import BankrError, get_bankr_service
from clawmes.tools.registry import register_with_ctx, write_tool

_log = logger_for("tools.bankr_leverage")

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["open", "close", "adjust", "positions", "funding"],
        },
        "payload": {
            "type": "object",
            "description": (
                "Action-specific body. open: market / direction / "
                "size / leverage. close: position_id. adjust: "
                "position_id + new params. funding: market."
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
    name="bankr_leverage",
    toolset="clawmes-bankr",
    description=(
        "Leveraged perp trading via Bankr + Avantis (1-10x long/short). "
        "Open/close/adjust positions; list current positions; read "
        "funding rate. Bankr-only."
    ),
    schema=_SCHEMA,
    emoji="\U0001f4c8",
)
def bankr_leverage(args: dict[str, Any], **kwargs: Any) -> str:
    action = read_str(args, "action", required=True)

    try:
        if action == "positions":
            result = get_bankr_service().request("GET", "/v1/leverage/positions")
        elif action == "funding":
            payload = args.get("payload") or {}
            market = payload.get("market") if isinstance(payload, dict) else None
            if not market:
                return error_result("funding requires payload.market", code="param_error")
            result = get_bankr_service().request("GET", f"/v1/leverage/funding?market={market}")
        else:
            payload = args.get("payload")
            if not isinstance(payload, dict):
                return error_result(
                    f"{action} requires a 'payload' dict",
                    code="param_error",
                )
            result = get_bankr_service().request("POST", f"/v1/leverage/{action}", body=payload)
    except BankrError as exc:
        return error_result(exc.message, code=exc.code)

    return json_result(
        {"action": action, "result": result},
        summary=f"bankr_leverage {action}",
    )


def register(ctx) -> None:
    register_with_ctx(ctx, bankr_leverage)
