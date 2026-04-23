"""``defi_price`` — read-only spot price lookup.

Read-only tool, no wallet required. Backed by
:mod:`clawmes.services.price` which routes to CoinGecko in v0.1.0.

Two actions:

  * ``quote``  — single token, returns ``{"symbol": "ETH", "price": 3500, "vs": "usd"}``
  * ``multi``  — list of tokens, returns ``{"prices": {"ETH": 3500, "USDC": 1}}``

Symbol resolution: pass common tickers (``ETH``, ``USDC``); the price
router maps to CoinGecko IDs automatically. Pass an explicit CoinGecko
ID (``ethereum``, ``usd-coin``) if you need a less-common token.
"""

from __future__ import annotations

from typing import Any

from clawmes.lib.params import read_enum, read_list, read_str
from clawmes.lib.tool_result import error_result, json_result
from clawmes.services.price import get_price_service
from clawmes.tools.registry import read_tool, register_with_ctx

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["quote", "multi"],
            "description": "quote = single token; multi = batch lookup",
        },
        "symbol": {
            "type": "string",
            "description": "Token ticker (e.g. 'ETH') or CoinGecko ID (e.g. 'ethereum'). Required for action='quote'.",
        },
        "tokens": {
            "type": "array",
            "items": {"type": "string"},
            "description": "List of tickers / CoinGecko IDs. Required for action='multi'.",
        },
        "vs_currency": {
            "type": "string",
            "description": "Quote currency (default: 'usd')",
            "default": "usd",
        },
    },
    "required": ["action"],
}


@read_tool(
    name="defi_price",
    toolset="clawmes-analytics",
    description=(
        "Get current spot prices for crypto tokens. Read-only, no wallet "
        "required. Pass a common ticker like 'ETH' or a CoinGecko id like "
        "'ethereum'. Use action='quote' for a single token and "
        "action='multi' for a list."
    ),
    schema=_SCHEMA,
    emoji="💲",
)
def defi_price(args: dict[str, Any], **kwargs: Any) -> str:
    action = read_enum(args, "action", ["quote", "multi"], required=True)
    vs_currency = (read_str(args, "vs_currency") or "usd").lower()

    if action == "quote":
        return _handle_quote(args, vs_currency)
    # action == "multi" — read_enum guarantees one of {quote, multi}
    return _handle_multi(args, vs_currency)


def _handle_quote(args: dict[str, Any], vs_currency: str) -> str:
    # read_str(required=True) raises ParamError if missing — caught by the
    # @read_tool decorator and converted to a param_error envelope. We
    # therefore know symbol is a non-empty string here.
    symbol = read_str(args, "symbol", required=True)
    assert symbol is not None  # narrow for type checkers

    price = get_price_service().get_price(symbol, vs_currency)
    if price is None:
        return error_result(
            f"No price available for {symbol!r} in {vs_currency!r}. "
            "Check the symbol and try again.",
            code="price_unavailable",
        )

    return json_result(
        {"symbol": symbol, "price": price, "vs": vs_currency},
        summary=f"{symbol} = {price:,.4f} {vs_currency.upper()}",
    )


def _handle_multi(args: dict[str, Any], vs_currency: str) -> str:
    tokens = read_list(args, "tokens")
    if not tokens:
        return error_result(
            "Missing or empty 'tokens' list for action='multi'",
            code="param_error",
        )

    prices = get_price_service().get_prices(tokens, vs_currency)
    if not prices:
        return error_result(
            f"No prices available for any of {tokens!r} in {vs_currency!r}",
            code="price_unavailable",
        )

    # Order summary by request order; missing tokens get a "—"
    lines = []
    for tok in tokens:
        if tok in prices:
            lines.append(f"  {tok}: {prices[tok]:,.4f} {vs_currency.upper()}")
        else:
            lines.append(f"  {tok}: (unavailable)")
    summary = f"Prices ({vs_currency.upper()}):\n" + "\n".join(lines)
    return json_result(
        {"prices": prices, "vs": vs_currency, "missing": [t for t in tokens if t not in prices]},
        summary=summary,
    )


def register(ctx) -> None:
    register_with_ctx(ctx, defi_price)
