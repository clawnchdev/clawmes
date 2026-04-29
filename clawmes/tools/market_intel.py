"""``market_intel`` — trending tokens + market overview.

Four actions:

  * ``trending``    — CoinGecko's trending list (last 24h search-volume
    leaderboard). Useful for "what's hot right now."
  * ``whales``      — large-wallet activity. Not yet implemented;
    requires a whale-tracking provider integration (Nansen, Arkham).
  * ``flows``       — net token flow into / out of an address class
    (CEX, DeFi, etc.). Not yet implemented; same dependency.
  * ``top_movers``  — biggest 24h gainers / losers from CoinGecko's
    market data.

Read-only. The CG-backed actions have known caveats: the trending list
is search-volume-based (people *looking up* the token, not buying it)
and the top-movers list reflects 24h price change, not volume-weighted
performance. Both are useful signals, neither is a buy recommendation.
"""

from __future__ import annotations

from typing import Any

from clawmes.lib.http import http_get
from clawmes.lib.logger import logger_for
from clawmes.lib.params import read_int, read_str
from clawmes.lib.tool_result import error_result, json_result
from clawmes.tools.registry import read_tool, register_with_ctx

_log = logger_for("tools.market_intel")

_CG_BASE = "https://api.coingecko.com/api/v3"

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["trending", "whales", "flows", "top_movers"],
        },
        "limit": {
            "type": "integer",
            "description": "Max items to return (default 10).",
        },
        "direction": {
            "type": "string",
            "enum": ["gainers", "losers"],
            "description": ("For top_movers — gainers (default) or losers."),
        },
    },
    "required": ["action"],
}


@read_tool(
    name="market_intel",
    toolset="clawmes-defi",
    description=(
        "Market overview — trending tokens, top movers, and (planned) "
        "whale activity. CoinGecko-backed; whales / flows require a "
        "third-party data provider and are not yet implemented."
    ),
    schema=_SCHEMA,
    emoji="\U0001f50d",
)
def market_intel(args: dict[str, Any], **kwargs: Any) -> str:
    action = read_str(args, "action", required=True)
    limit = read_int(args, "limit") or 10

    if action == "trending":
        return _handle_trending(limit)
    if action == "top_movers":
        direction = read_str(args, "direction") or "gainers"
        return _handle_top_movers(direction, limit)
    if action == "whales":
        return error_result(
            "Whale tracking requires a third-party data provider "
            "(Nansen, Arkham). Not yet implemented.",
            code="not_implemented",
        )
    return error_result(
        "Address-flow analysis requires a third-party data provider. Not yet implemented.",
        code="not_implemented",
    )


def _handle_trending(limit: int) -> str:
    try:
        data = http_get(f"{_CG_BASE}/search/trending", timeout=15.0)
    except Exception as exc:  # noqa: BLE001
        return error_result(f"Could not fetch trending list: {exc}", code="api_error")
    if not isinstance(data, dict):
        return error_result("CoinGecko returned non-dict response", code="api_error")
    raw = data.get("coins") or []
    items = []
    for entry in raw[:limit]:
        item = entry.get("item") or {}
        items.append(
            {
                "rank": item.get("market_cap_rank"),
                "id": item.get("id"),
                "symbol": item.get("symbol"),
                "name": item.get("name"),
                "score": item.get("score"),
            }
        )
    return json_result(
        {"count": len(items), "trending": items},
        summary=(
            f"{len(items)} trending coin(s) right now:\n"
            + "\n".join(
                f"  {i + 1}. {c['symbol'].upper()} — {c['name']} (rank {c['rank']})"
                for i, c in enumerate(items)
            )
        ),
    )


def _handle_top_movers(direction: str, limit: int) -> str:
    try:
        data = http_get(
            f"{_CG_BASE}/coins/markets",
            params={
                "vs_currency": "usd",
                "order": (
                    "price_change_percentage_24h_desc"
                    if direction == "gainers"
                    else "price_change_percentage_24h_asc"
                ),
                "per_page": str(limit),
                "page": "1",
                "sparkline": "false",
            },
            timeout=15.0,
        )
    except Exception as exc:  # noqa: BLE001
        return error_result(f"Could not fetch top movers: {exc}", code="api_error")
    if not isinstance(data, list):
        return error_result("CoinGecko returned non-list response", code="api_error")
    items = []
    for c in data[:limit]:
        if not isinstance(c, dict):
            continue
        items.append(
            {
                "id": c.get("id"),
                "symbol": c.get("symbol"),
                "name": c.get("name"),
                "current_price": c.get("current_price"),
                "price_change_pct_24h": c.get("price_change_percentage_24h"),
                "market_cap": c.get("market_cap"),
            }
        )
    return json_result(
        {"direction": direction, "count": len(items), "movers": items},
        summary=(
            f"Top {len(items)} {direction} (24h):\n"
            + "\n".join(
                f"  {i + 1}. {(c.get('symbol') or '?').upper()} "
                f"{(c.get('price_change_pct_24h') or 0):+.2f}%  "
                f"${(c.get('current_price') or 0):,}"
                for i, c in enumerate(items)
            )
        ),
    )


def register(ctx) -> None:
    """Wire ``market_intel`` into Hermes."""
    register_with_ctx(ctx, market_intel)
