"""``manage_orders`` — limit / stop / trailing / DCA orders.

Seven actions for off-chain order management:

  * ``limit_buy``   — buy when price drops to / below a target.
  * ``limit_sell``  — sell when price rises to / above a target.
  * ``stop``        — stop-loss sell at a price floor.
  * ``trailing``    — trailing stop-loss with a percentage gap.
  * ``dca``         — dollar-cost-average over time / chunks.
  * ``cancel``      — cancel an active order.
  * ``list``        — list active / completed orders.

Orders persist to the plan_scheduler. Execution happens via the
configured DEX (defaults to 0x via defi_swap). Hermes' cron daemon
ticks the scheduler; price triggers fire via the price service.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from clawmes.lib.logger import logger_for
from clawmes.lib.params import read_str
from clawmes.lib.paths import hermes_home
from clawmes.lib.tool_result import error_result, json_result
from clawmes.tools.registry import register_with_ctx, write_tool

_log = logger_for("tools.manage_orders")


def _orders_dir() -> Path:
    return hermes_home() / "clawmes" / "orders"


_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": [
                "limit_buy",
                "limit_sell",
                "stop",
                "trailing",
                "dca",
                "cancel",
                "list",
            ],
        },
        "token": {"type": "string"},
        "amount": {"type": "string"},
        "trigger_price": {"type": "string", "description": "USD price trigger."},
        "trail_pct": {
            "type": "number",
            "description": "Trailing stop %: 0.05 = 5%.",
        },
        "chunks": {"type": "integer", "description": "DCA chunks."},
        "interval_seconds": {"type": "integer", "description": "DCA interval."},
        "order_id": {"type": "string", "description": "For cancel."},
        "policyConfirmationNonce": {"type": "string"},
    },
    "required": ["action"],
}


@write_tool(
    name="manage_orders",
    toolset="clawmes-trading",
    description=(
        "Off-chain order management — limit buy/sell, stop-loss, "
        "trailing, DCA. Orders persist to the plan scheduler and "
        "execute via defi_swap when triggers fire."
    ),
    schema=_SCHEMA,
    emoji="\U0001f4d1",
)
def manage_orders(args: dict[str, Any], **kwargs: Any) -> str:
    action = read_str(args, "action", required=True)
    base = _orders_dir()
    base.mkdir(parents=True, exist_ok=True)

    if action == "list":
        return _list_orders(base)
    if action == "cancel":
        return _cancel_order(args, base)

    # Create order: persist to disk, scheduler picks up on next tick
    return _create_order(action, args, base)


def _list_orders(base: Path) -> str:
    orders = []
    for p in base.glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            orders.append(data)
        except (json.JSONDecodeError, OSError):
            continue
    return json_result(
        {"count": len(orders), "orders": orders},
        summary=f"{len(orders)} active order(s)",
    )


def _cancel_order(args, base: Path) -> str:
    order_id = read_str(args, "order_id", required=True)
    path = base / f"{order_id}.json"
    if not path.exists():
        return error_result(f"Order {order_id!r} not found", code="not_found")
    try:
        path.unlink()
    except OSError as exc:
        return error_result(f"Could not cancel: {exc}", code="not_found")
    return json_result(
        {"cancelled": order_id},
        summary=f"Cancelled order {order_id}",
    )


def _create_order(action: str, args, base: Path) -> str:
    order_id = f"{action}-{int(time.time())}"
    record = {
        "id": order_id,
        "type": action,
        "token": args.get("token"),
        "amount": args.get("amount"),
        "created_at": time.time(),
        "status": "pending",
    }
    if action in ("limit_buy", "limit_sell", "stop"):
        record["trigger_price"] = args.get("trigger_price")
    if action == "trailing":
        record["trail_pct"] = args.get("trail_pct")
    if action == "dca":
        record["chunks"] = args.get("chunks")
        record["interval_seconds"] = args.get("interval_seconds")

    path = base / f"{order_id}.json"
    try:
        path.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
    except OSError as exc:
        return error_result(f"Could not persist order: {exc}", code="storage_error")

    return json_result(
        record,
        summary=(
            f"Order {order_id} created. The plan scheduler will fire "
            "it when the trigger condition is met."
        ),
    )


def register(ctx) -> None:
    register_with_ctx(ctx, manage_orders)
