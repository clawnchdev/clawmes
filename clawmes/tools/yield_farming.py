"""``yield`` — yield farming opportunities via DeFiLlama.

Four actions:

  * ``find``  — search yield pools by chain / token / minimum APY.
    Returns the top matches sorted by APY.
  * ``info``  — single-pool detail (TVL, APY breakdown, IL risk, etc.).
  * ``enter`` — placeholder. Yield strategies vary too widely (Aave
    supply, Yearn vault deposit, Convex stake, Curve LP) to dispatch
    generically. Returns ``not_implemented`` with a hint to use the
    strategy-specific tool (``defi_lend supply``, etc.).
  * ``exit``  — same placeholder as enter.

DeFiLlama's /yields/pools endpoint covers ~10,000 pools across every
major chain — the canonical aggregator for yield discovery.
"""

from __future__ import annotations

from typing import Any

from clawmes.lib.http import http_get
from clawmes.lib.logger import logger_for
from clawmes.lib.params import read_int, read_str
from clawmes.lib.tool_result import error_result, json_result
from clawmes.tools.registry import register_with_ctx, write_tool

_log = logger_for("tools.yield")

_DEFILLAMA_BASE = "https://yields.llama.fi"

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["find", "info", "enter", "exit"],
        },
        "chain": {
            "type": "string",
            "description": "Chain filter (Ethereum, Base, Arbitrum, Polygon, etc.)",
        },
        "token": {
            "type": "string",
            "description": "Token symbol filter (USDC, ETH, WETH, etc.)",
        },
        "min_apy": {
            "type": "number",
            "description": "Minimum APY filter (default 0).",
        },
        "min_tvl": {
            "type": "number",
            "description": "Minimum TVL filter in USD (default 1_000_000).",
        },
        "limit": {
            "type": "integer",
            "description": "Max results (default 10).",
        },
        "pool_id": {
            "type": "string",
            "description": "Pool ID for info action (DeFiLlama's pool UUID).",
        },
        "policyConfirmationNonce": {
            "type": "string",
            "description": "Set when retrying after POLICY HOLD.",
        },
    },
    "required": ["action"],
}


@write_tool(
    name="yield",
    toolset="clawmes-defi",
    description=(
        "Yield farming via DeFiLlama. find searches pools by "
        "chain/token/APY/TVL filters; info returns detail for a "
        "specific pool. enter/exit are stubs — use the strategy-"
        "specific tools (defi_lend supply for Aave, defi_stake stake "
        "for Lido, etc.)."
    ),
    schema=_SCHEMA,
    emoji="\U0001f33e",
)
def yield_(args: dict[str, Any], **kwargs: Any) -> str:
    action = read_str(args, "action", required=True)
    if action == "find":
        return _handle_find(args)
    if action == "info":
        return _handle_info(args)
    return error_result(
        f"yield {action} is not yet implemented. Each strategy "
        "uses a different protocol — call the protocol-specific "
        "tool (defi_lend, defi_stake, etc.) after using "
        "yield find / info to discover opportunities.",
        code="not_implemented",
    )


def _handle_find(args: dict[str, Any]) -> str:
    chain = read_str(args, "chain")
    token = read_str(args, "token")
    min_apy = float(args.get("min_apy") or 0)
    min_tvl = float(args.get("min_tvl") or 1_000_000)
    limit = read_int(args, "limit") or 10

    try:
        data = http_get(f"{_DEFILLAMA_BASE}/pools", timeout=20.0)
    except Exception as exc:  # noqa: BLE001
        return error_result(f"DeFiLlama request failed: {exc}", code="api_error")
    if not isinstance(data, dict):
        return error_result("DeFiLlama returned non-dict response", code="api_error")
    pools = data.get("data") or []
    filtered = []
    for p in pools:
        if not isinstance(p, dict):
            continue
        if chain and (p.get("chain") or "").lower() != chain.lower():
            continue
        if token and token.upper() not in (p.get("symbol") or "").upper():
            continue
        try:
            apy = float(p.get("apy") or 0)
            tvl = float(p.get("tvlUsd") or 0)
        except (TypeError, ValueError):
            continue
        if apy < min_apy or tvl < min_tvl:
            continue
        filtered.append(
            {
                "pool_id": p.get("pool"),
                "project": p.get("project"),
                "chain": p.get("chain"),
                "symbol": p.get("symbol"),
                "tvl_usd": tvl,
                "apy": apy,
                "apy_base": p.get("apyBase"),
                "apy_reward": p.get("apyReward"),
                "il_risk": p.get("ilRisk"),
                "exposure": p.get("exposure"),
            }
        )
    filtered.sort(key=lambda x: x["apy"], reverse=True)
    top = filtered[:limit]
    return json_result(
        {
            "filters": {
                "chain": chain,
                "token": token,
                "min_apy": min_apy,
                "min_tvl": min_tvl,
            },
            "count": len(top),
            "total_matched": len(filtered),
            "pools": top,
        },
        summary=(
            f"{len(top)} pool(s) match (of {len(filtered)} after filter):\n"
            + "\n".join(
                f"  {i + 1}. {p['project']} {p['symbol']} on {p['chain']}: "
                f"{p['apy']:.2f}% APY, ${p['tvl_usd']:,.0f} TVL"
                for i, p in enumerate(top)
            )
        ),
    )


def _handle_info(args: dict[str, Any]) -> str:
    pool_id = read_str(args, "pool_id", required=True)
    try:
        data = http_get(f"{_DEFILLAMA_BASE}/chart/{pool_id}", timeout=15.0)
    except Exception as exc:  # noqa: BLE001
        return error_result(f"DeFiLlama request failed: {exc}", code="api_error")
    if not isinstance(data, dict):
        return error_result("DeFiLlama returned non-dict response", code="api_error")
    history = data.get("data") or []
    if not isinstance(history, list) or not history:
        return error_result(f"No history found for pool {pool_id!r}", code="not_found")
    latest = history[-1] if history else {}
    return json_result(
        {
            "pool_id": pool_id,
            "history_points": len(history),
            "latest": latest,
        },
        summary=(
            f"Pool {pool_id} latest:\n"
            f"  TVL: ${(latest.get('tvlUsd') or 0):,.0f}\n"
            f"  APY: {(latest.get('apy') or 0):.2f}%"
        ),
    )


def register(ctx) -> None:
    """Wire ``yield`` into Hermes."""
    register_with_ctx(ctx, yield_)
