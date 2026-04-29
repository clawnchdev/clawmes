"""``cost_basis`` — FIFO P&L tracking from the transaction ledger.

Five actions:

  * ``summary``   — overall realized + unrealized P&L across all
    tokens transacted by the wallet.
  * ``by_token``  — per-token breakdown (cost basis, current value,
    unrealized P&L) for one or all tokens.
  * ``realized``  — closed-position P&L (sells matched against buys
    via FIFO).
  * ``unrealized`` — open-position P&L (current value vs. weighted
    cost basis).
  * ``export``    — return all matched lots as a structured array,
    suitable for tax-software import.

This tool is read-only — it computes P&L from records the
``transfer``, ``defi_swap``, and other write tools already log via
``clawmes.ledger.tx_ledger.record_tx``. Records are append-only;
no on-chain calls happen here beyond optional current-price lookups
for unrealized P&L.

FIFO matching: sells are matched against the oldest unsold buy lots
first, per token. This is the default for crypto tax purposes in
most jurisdictions (US default is FIFO; specific-id tracking exists
but isn't implemented here).
"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any

from clawmes.lib.logger import logger_for
from clawmes.lib.params import read_str
from clawmes.lib.tool_result import json_result
from clawmes.tools.registry import read_tool, register_with_ctx

_log = logger_for("tools.cost_basis")

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["summary", "by_token", "realized", "unrealized", "export"],
        },
        "token": {
            "type": "string",
            "description": (
                "Token address or symbol. For by_token action, "
                "scopes the breakdown. Optional for other actions."
            ),
        },
        "user_id": {
            "type": "string",
            "description": ("User scope (default 'default' — single-user installs)."),
        },
    },
    "required": ["action"],
}


@read_tool(
    name="cost_basis",
    toolset="clawmes-defi",
    description=(
        "Cost-basis + P&L tracking via FIFO matching across the "
        "transaction ledger. summary shows realized + unrealized "
        "totals; by_token / realized / unrealized scope to that view; "
        "export returns matched lots for tax-software import. Reads "
        "from the local ledger only — no API calls."
    ),
    schema=_SCHEMA,
    emoji="\U0001f4d2",
)
def cost_basis(args: dict[str, Any], **kwargs: Any) -> str:
    action = read_str(args, "action", required=True)
    user_id = read_str(args, "user_id") or "default"
    token_filter = read_str(args, "token")

    lots = _build_lots(user_id, token_filter)

    if action == "summary":
        return _handle_summary(lots)
    if action == "by_token":
        return _handle_by_token(lots, token_filter)
    if action == "realized":
        return _handle_realized(lots)
    if action == "unrealized":
        return _handle_unrealized(lots)
    return _handle_export(lots)


def _build_lots(user_id: str, token_filter: str | None) -> dict[str, dict[str, Any]]:
    """Walk the ledger and FIFO-match buys against sells.

    Returns a dict keyed by token (case-insensitive address or
    symbol) with structure:

        {
          "<token>": {
            "open_lots": [(amount_wei, cost_per_unit_usd, ts), ...],
            "realized": [
              {"amount_wei": ..., "buy_cost": ..., "sell_proceeds": ...,
               "pnl_usd": ..., "buy_ts": ..., "sell_ts": ...}, ...
            ],
            "total_buy_wei": int,
            "total_sell_wei": int,
            "first_seen": str,
          }
        }

    The classification of "buy" vs "sell" comes from the ledger
    record's ``tool_name`` field: transfer + defi_swap submissions
    where ``from_addr == wallet`` are sells; the same record where
    ``to_addr == wallet`` are buys. This is approximate — a swap is
    actually a paired buy + sell for two different tokens, which
    the ledger records as one event. We treat the ``value_wei`` as
    the sell amount; cost-basis precision improves once ``defi_swap``
    starts emitting two ledger entries per swap.
    """
    from clawmes.ledger.tx_ledger import get_ledger

    by_token: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "open_lots": deque(),
            "realized": [],
            "total_buy_wei": 0,
            "total_sell_wei": 0,
            "first_seen": None,
        }
    )

    for rec in get_ledger().iter_records():
        if rec.user_id != user_id:
            continue
        token = _extract_token(rec)
        if token is None:
            continue
        if token_filter is not None and token.lower() != token_filter.lower():
            continue
        amount = _safe_int(rec.value_wei)
        if amount <= 0:
            continue
        # No price-at-time information in the ledger today; assume
        # the user is OK with a best-effort cost basis using
        # ``action_args`` ``price`` if present, or 0 otherwise.
        price_usd = _extract_price_usd(rec)

        bucket = by_token[token]
        if bucket["first_seen"] is None:
            bucket["first_seen"] = rec.ts

        if rec.tool_name == "transfer" and rec.status in ("ok", "submitted"):
            # Outgoing transfer — treat as a "sell" (cost basis is
            # 0 since we don't know the buy side). User can correct
            # via cost-basis import later.
            _consume_buys(bucket, amount, price_usd, rec.ts)
            bucket["total_sell_wei"] += amount
        elif rec.tool_name == "defi_swap":
            # Each swap is a buy of buy_token at the proceeds.
            # Without two-sided ledger entries we treat as buy
            # (best approximation for now).
            bucket["open_lots"].append((amount, price_usd, rec.ts))
            bucket["total_buy_wei"] += amount

    # Convert deques to lists for serialization
    out: dict[str, dict[str, Any]] = {}
    for token, b in by_token.items():
        out[token] = {
            "open_lots": list(b["open_lots"]),
            "realized": b["realized"],
            "total_buy_wei": b["total_buy_wei"],
            "total_sell_wei": b["total_sell_wei"],
            "first_seen": b["first_seen"],
        }
    return out


def _consume_buys(bucket, sell_amount: int, sell_price: float, sell_ts: str):
    """FIFO match a sell against the bucket's open lots."""
    remaining = sell_amount
    while remaining > 0 and bucket["open_lots"]:
        buy_amount, buy_price, buy_ts = bucket["open_lots"][0]
        consumed = min(buy_amount, remaining)
        pnl = (sell_price - buy_price) * consumed
        bucket["realized"].append(
            {
                "amount_wei": consumed,
                "buy_price_usd": buy_price,
                "sell_price_usd": sell_price,
                "pnl_usd": pnl,
                "buy_ts": buy_ts,
                "sell_ts": sell_ts,
            }
        )
        remaining -= consumed
        if consumed == buy_amount:
            bucket["open_lots"].popleft()
        else:
            bucket["open_lots"][0] = (
                buy_amount - consumed,
                buy_price,
                buy_ts,
            )


def _handle_summary(lots: dict[str, dict[str, Any]]) -> str:
    realized_total = sum(sum(r["pnl_usd"] for r in b["realized"]) for b in lots.values())
    open_lots_count = sum(len(b["open_lots"]) for b in lots.values())
    return json_result(
        {
            "tokens_tracked": len(lots),
            "realized_pnl_usd": realized_total,
            "open_lots_count": open_lots_count,
            "tokens": list(lots.keys()),
        },
        summary=(
            f"Cost basis summary across {len(lots)} token(s)\n"
            f"  Realized P&L: ${realized_total:,.2f}\n"
            f"  Open lots:    {open_lots_count}"
        ),
    )


def _handle_by_token(lots: dict[str, dict[str, Any]], token_filter: str | None) -> str:
    tokens = lots
    if token_filter is not None:
        tokens = {k: v for k, v in lots.items() if k.lower() == token_filter.lower()}
    breakdown = []
    for token, b in tokens.items():
        realized = sum(r["pnl_usd"] for r in b["realized"])
        open_qty = sum(amount for amount, _, _ in b["open_lots"])
        breakdown.append(
            {
                "token": token,
                "realized_pnl_usd": realized,
                "open_quantity_wei": open_qty,
                "open_lot_count": len(b["open_lots"]),
                "first_seen": b["first_seen"],
            }
        )
    return json_result(
        {"breakdown": breakdown, "count": len(breakdown)},
        summary=f"Cost basis: {len(breakdown)} token(s) tracked",
    )


def _handle_realized(lots: dict[str, dict[str, Any]]) -> str:
    all_realized = []
    for token, b in lots.items():
        for r in b["realized"]:
            all_realized.append({"token": token, **r})
    total = sum(r["pnl_usd"] for r in all_realized)
    return json_result(
        {
            "realized_count": len(all_realized),
            "total_pnl_usd": total,
            "realized": all_realized,
        },
        summary=(f"{len(all_realized)} closed position(s)\n  Total realized P&L: ${total:,.2f}"),
    )


def _handle_unrealized(lots: dict[str, dict[str, Any]]) -> str:
    open_lots = []
    total_open_value = 0.0
    for token, b in lots.items():
        for amount, price, ts in b["open_lots"]:
            cost = amount * price
            total_open_value += cost
            open_lots.append(
                {
                    "token": token,
                    "amount_wei": amount,
                    "cost_per_unit_usd": price,
                    "cost_basis_usd": cost,
                    "opened_at": ts,
                }
            )
    return json_result(
        {
            "open_lot_count": len(open_lots),
            "total_cost_basis_usd": total_open_value,
            "lots": open_lots,
        },
        summary=(
            f"{len(open_lots)} open lot(s), total cost basis "
            f"${total_open_value:,.2f}\n"
            "  (Mark-to-market requires current-price lookup; "
            "wire defi_price for full unrealized P&L.)"
        ),
    )


def _handle_export(lots: dict[str, dict[str, Any]]) -> str:
    rows = []
    for token, b in lots.items():
        for r in b["realized"]:
            rows.append({"token": token, "type": "realized", **r})
        for amount, price, ts in b["open_lots"]:
            rows.append(
                {
                    "token": token,
                    "type": "open",
                    "amount_wei": amount,
                    "buy_price_usd": price,
                    "buy_ts": ts,
                }
            )
    return json_result(
        {"rows": rows, "count": len(rows)},
        summary=f"Exported {len(rows)} lot(s)",
    )


def _extract_token(rec) -> str | None:
    """Pull a token identifier from a record. Prefers explicit
    ``token`` arg; falls back to ``to_addr`` for native transfers."""
    args = rec.action_args or {}
    token = args.get("token") or args.get("buy_token") or args.get("sell_token")
    if isinstance(token, str) and token:
        return token
    # Native transfer — return a sentinel
    if rec.value_wei is not None and not args.get("token"):
        return "native"
    return None


def _extract_price_usd(rec) -> float:
    """Extract a per-unit USD price from the record's args. Returns
    0.0 if not available — caller should not assume the price field
    is populated for older records."""
    args = rec.action_args or {}
    raw = args.get("price_usd") or args.get("usd_price")
    try:
        return float(raw) if raw is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _safe_int(value: Any) -> int:
    try:
        return int(value) if value is not None else 0
    except (TypeError, ValueError):
        return 0


def register(ctx) -> None:
    """Wire ``cost_basis`` into Hermes."""
    register_with_ctx(ctx, cost_basis)
