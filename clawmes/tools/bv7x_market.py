"""``bv7x_market`` — raw BTC market data via BV-7X.

Three free, public actions for quick reads of BTC market state:

  * ``btc_price``  — current BTC price + 24h change + market cap.
  * ``fear_greed`` — current Bitcoin Fear & Greed Index with label.
  * ``etf_flows``  — 7-day and 30-day Bitcoin ETF flow totals.

Why not just CoinGecko: BV-7X's endpoints aggregate from multiple
sources (their ``$BV7X`` tokenomics depend on accurate data) and
include the ETF-flow breakdown that CoinGecko doesn't expose
cleanly. Two free sources are better than one.

Read-only by design. No on-chain side effects.
"""

from __future__ import annotations

from typing import Any

from clawmes.lib.params import ParamError, read_enum
from clawmes.lib.tool_result import error_result, json_result
from clawmes.services.bv7x import BV7XError, get_bv7x_service
from clawmes.tools.registry import read_tool, register_with_ctx

_VALID_ACTIONS = ["btc_price", "fear_greed", "etf_flows"]


_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": _VALID_ACTIONS,
        },
    },
    "required": ["action"],
}


@read_tool(
    name="bv7x_market",
    toolset="clawmes-intelligence",
    description=(
        "Quick BTC market reads from BV-7X. btc_price = price + 24h "
        "change. fear_greed = F&G Index with label. etf_flows = "
        "7d/30d Bitcoin ETF flow totals. All free."
    ),
    schema=_SCHEMA,
    emoji="\U0001f4c8",  # 📈
)
def bv7x_market(args: dict[str, Any], **kwargs: Any) -> str:
    try:
        action = read_enum(args, "action", _VALID_ACTIONS, required=True)
    except ParamError as exc:
        return error_result(str(exc), code="param_error")

    svc = get_bv7x_service()
    try:
        if action == "btc_price":
            data = svc.get_btc_price()
            return json_result(data, summary=_format_price(data))
        if action == "fear_greed":
            data = svc.get_fear_greed()
            return json_result(data, summary=_format_fear_greed(data))
        # action == "etf_flows" — the only remaining option.
        data = svc.get_etf_flows()
        return json_result(data, summary=_format_etf(data))
    except BV7XError as exc:
        return error_result(exc.message, code=exc.code)


def _format_price(data: dict[str, Any]) -> str:
    price = data.get("price") or data.get("btc_price") or "?"
    change = data.get("change_24h") or data.get("price_change_24h") or 0
    sign = "+" if isinstance(change, (int, float)) and change >= 0 else ""
    return f"BTC ${price} ({sign}{change}% 24h)"


def _format_fear_greed(data: dict[str, Any]) -> str:
    value = data.get("value") or data.get("score") or "?"
    classification = data.get("classification") or data.get("label") or ""
    return f"Fear & Greed: {value}" + (f" ({classification})" if classification else "")


def _format_etf(data: dict[str, Any]) -> str:
    flow_7d = data.get("flow_7d") or data.get("seven_day_flow") or "?"
    flow_30d = data.get("flow_30d") or data.get("thirty_day_flow") or "?"
    return f"BTC ETF flows: 7d={flow_7d}, 30d={flow_30d}"


def register(ctx) -> None:
    register_with_ctx(ctx, bv7x_market)
