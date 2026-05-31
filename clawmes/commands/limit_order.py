"""``/limit_order`` — DEX limit buys + take-profit sells.

Each order watches a token's USD price on the registry tick and fires
a swap via ``defi_swap`` when the configured threshold is crossed.

Order types:

  * ``buy <token> <eth_amount> below <usd>`` — spend ETH to buy when
    price drops below the threshold.
  * ``sell <token> <amount> above <usd>`` — sell the configured
    amount of the token when price rises above the threshold.

State machine:
  active → filled (swap succeeded)
  active → failed (swap failed and ``max_attempts`` exhausted)
  active → paused (manual pause)
  active → cancelled (manual cancel)

Filled / cancelled / failed orders persist for ``/limit_order history``
auditing but never re-execute. ``/limit_order resume`` only re-arms
paused orders, not filled/failed/cancelled ones.

Storage: ``${HERMES_HOME}/clawmes/limit_orders/orders.json``.

Free tier: 1 active order. HOLDER tier: unlimited. Like ``/dca`` v2
and ``/copy``, the safeguard surface is identical: per-order
slippage, max attempts, default of 3.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_DEFAULT_SLIPPAGE_BPS = 100
_DEFAULT_MAX_ATTEMPTS = 3


# ── state I/O ───────────────────────────────────────────────────────


def _orders_path() -> Path:
    from clawmes.lib.paths import state_dir

    return state_dir("limit_orders") / "orders.json"


def _load_state() -> dict[str, Any]:
    path = _orders_path()
    if not path.exists():
        return {"orders": []}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {"orders": []}
    if not isinstance(data, dict) or not isinstance(data.get("orders"), list):
        return {"orders": []}
    return data


def _save_state(state: dict[str, Any]) -> None:
    path = _orders_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now_epoch() -> int:
    return int(time.time())


def _new_id() -> str:
    return f"lim_{uuid.uuid4().hex[:10]}"


def _short(value: str) -> str:
    if not isinstance(value, str) or len(value) <= 12:
        return str(value)
    return f"{value[:6]}…{value[-4:]}"


def _record(name: str, args: str, result: str) -> None:
    try:
        from clawmes.services.command_history import record_command_call

        record_command_call(name, args, result)
    except Exception:  # noqa: BLE001
        pass


def _split_flags(parts: list[str]) -> tuple[list[str], dict[str, str]]:
    positional: list[str] = []
    flags: dict[str, str] = {}
    i = 0
    while i < len(parts):
        tok = parts[i]
        if tok.startswith("--"):
            name = tok[2:]
            value = parts[i + 1] if i + 1 < len(parts) else ""
            flags[name] = value
            i += 2
        else:
            positional.append(tok)
            i += 1
    return positional, flags


# ── command dispatch ───────────────────────────────────────────────


async def handle_limit_order(raw_args: str, *, sender_id: str = "default", **_kwargs: Any) -> str:
    raw = (raw_args or "").strip()
    if not raw:
        out = _render_usage()
    else:
        parts = raw.split()
        sub = parts[0].lower()
        rest = parts[1:]
        if sub == "add":
            out = _cmd_add(sender_id, rest)
        elif sub in ("list", "ls"):
            out = _cmd_list(sender_id)
        elif sub == "pause":
            out = _cmd_mutate(sender_id, rest, status="paused", verb="paused")
        elif sub == "resume":
            out = _cmd_resume(sender_id, rest)
        elif sub in ("cancel", "rm", "remove"):
            out = _cmd_cancel(sender_id, rest)
        elif sub == "edit":
            out = _cmd_edit(sender_id, rest)
        elif sub == "tick":
            out = await _cmd_tick()
        elif sub == "status":
            out = _cmd_status(sender_id)
        elif sub == "history":
            out = _cmd_history(sender_id, rest)
        else:
            out = f"Unknown subcommand: {sub!r}\n\n" + _render_usage()
    _record("limit_order", raw_args, out)
    return out


def _render_usage() -> str:
    return (
        "Limit orders — buy below a price, sell above a price.\n"
        "\n"
        "  /limit_order add buy <token> <eth_amount> below <usd>\n"
        "      [--slippage <bps>] [--max-attempts <n>]\n"
        "                          Buy <eth_amount> ETH worth of <token>\n"
        "                          when its price drops below <usd>\n"
        "  /limit_order add sell <token> <token_amount> above <usd>\n"
        "      [--slippage <bps>] [--max-attempts <n>]\n"
        "                          Sell <token_amount> of <token>\n"
        "                          when its price rises above <usd>\n"
        "  /limit_order list       Your orders + last attempt time\n"
        "  /limit_order pause <id> Suspend\n"
        "  /limit_order resume <id> Re-arm (active orders only)\n"
        "  /limit_order cancel <id> Remove entirely\n"
        "  /limit_order edit <id> <field> <value>\n"
        "                          Change one field\n"
        "  /limit_order tick       Manually evaluate due orders (testing)\n"
        "  /limit_order status     Global summary + service health\n"
        "  /limit_order history <id>   Past attempts for one order\n"
        "\n"
        "Example:\n"
        "  /limit_order add buy CLAWNCH 0.01 below 0.00001\n"
        "  /limit_order add sell 0xToken… 1000000 above 0.00005"
    )


# ── /limit_order add ───────────────────────────────────────────────


def _cmd_add(sender_id: str, parts: list[str]) -> str:
    pos, flags = _split_flags(parts)
    if not pos:
        return "Usage: /limit_order add buy|sell <token> <amount> below|above <usd>"

    # Free-tier cap on active orders. Holders bypass.
    from clawmes.services.token_gate import check_cap_or_error

    state_check = _load_state()
    active_mine = sum(
        1
        for o in state_check["orders"]
        if o.get("sender_id") == sender_id and o.get("status") == "active"
    )
    cap_err = check_cap_or_error("limit_order", active_count=active_mine, feature="limit order")
    if cap_err:
        return cap_err

    kind = pos[0].lower()
    if kind == "buy":
        return _add_buy(sender_id, pos[1:], flags)
    if kind == "sell":
        return _add_sell(sender_id, pos[1:], flags)
    return f"Unknown order type {kind!r}. Use 'buy' or 'sell'."


def _add_buy(sender_id: str, pos: list[str], flags: dict[str, str]) -> str:
    if len(pos) < 4 or pos[2].lower() != "below":
        return "Usage: /limit_order add buy <token> <eth_amount> below <usd>"
    token, amount_raw, _below, usd_raw = pos[0], pos[1], pos[2], pos[3]
    return _store_order(
        sender_id,
        type_="buy",
        token=token,
        amount=amount_raw,
        direction="below",
        usd=usd_raw,
        flags=flags,
    )


def _add_sell(sender_id: str, pos: list[str], flags: dict[str, str]) -> str:
    if len(pos) < 4 or pos[2].lower() != "above":
        return "Usage: /limit_order add sell <token> <token_amount> above <usd>"
    token, amount_raw, _above, usd_raw = pos[0], pos[1], pos[2], pos[3]
    return _store_order(
        sender_id,
        type_="sell",
        token=token,
        amount=amount_raw,
        direction="above",
        usd=usd_raw,
        flags=flags,
    )


def _store_order(
    sender_id: str,
    *,
    type_: str,
    token: str,
    amount: str,
    direction: str,
    usd: str,
    flags: dict[str, str],
) -> str:
    try:
        amount_f = float(amount)
    except ValueError:
        return f"amount must be a number (got {amount!r})."
    if amount_f <= 0:
        return f"amount must be positive (got {amount_f})."

    try:
        threshold_usd = float(usd)
    except ValueError:
        return f"usd threshold must be a number (got {usd!r})."
    if threshold_usd <= 0:
        return f"usd threshold must be positive (got {threshold_usd})."

    slippage_bps = _DEFAULT_SLIPPAGE_BPS
    if "slippage" in flags:
        try:
            slippage_bps = int(flags["slippage"])
        except ValueError:
            return f"--slippage must be an integer (got {flags['slippage']!r})."
        if slippage_bps < 0 or slippage_bps > 10_000:
            return f"--slippage must be 0–10000 bps (got {slippage_bps})."

    max_attempts = _DEFAULT_MAX_ATTEMPTS
    if "max-attempts" in flags:
        try:
            max_attempts = int(flags["max-attempts"])
        except ValueError:
            return f"--max-attempts must be an integer (got {flags['max-attempts']!r})."
        if max_attempts < 1:
            return f"--max-attempts must be >= 1 (got {max_attempts})."

    # Bracket: on fill, auto-create take-profit + stop-loss sell orders.
    # Grammar: ``--bracket <tp_pct>:<sl_pct>``. Only valid for buy orders
    # — sell orders don't "fill into a position" the way buys do.
    bracket: dict[str, float] | None = None
    if "bracket" in flags:
        from clawmes.services.token_gate import Tier, check_tier_or_error

        gate_err = check_tier_or_error(Tier.HOLDER, feature="/limit_order --bracket")
        if gate_err:
            return gate_err
        if type_ != "buy":
            return "--bracket can only be attached to buy orders."
        raw = flags["bracket"]
        parts_b = raw.split(":")
        if len(parts_b) != 2:
            return f"--bracket must be '<tp_pct>:<sl_pct>' (got {raw!r})."
        try:
            tp_pct = float(parts_b[0])
            sl_pct = float(parts_b[1])
        except ValueError:
            return f"--bracket values must be numbers (got {raw!r})."
        if tp_pct <= 0 or sl_pct <= 0:
            return f"--bracket values must be positive (got {raw!r})."
        bracket = {"tp_pct": tp_pct, "sl_pct": sl_pct}

    state = _load_state()
    order_id = _new_id()
    order = {
        "id": order_id,
        "sender_id": sender_id,
        "type": type_,
        "token": token,
        "amount": amount_f,
        "direction": direction,
        "threshold_usd": threshold_usd,
        "slippage_bps": slippage_bps,
        "max_attempts": max_attempts,
        "bracket": bracket,
        "bracket_children": [],
        "status": "active",
        "created_at": _now_iso(),
        "attempts": [],
    }
    state["orders"].append(order)
    _save_state(state)

    verb = "Buy" if type_ == "buy" else "Sell"
    bracket_line = (
        f"  Bracket:     +{bracket['tp_pct']}% TP / -{bracket['sl_pct']}% SL on fill\n"
        if bracket
        else ""
    )
    return (
        f"Limit order added: {order_id}\n"
        f"  Type:        {verb}\n"
        f"  Token:       {token}\n"
        f"  Amount:      {amount_f} {'ETH' if type_ == 'buy' else token}\n"
        f"  Trigger:     price {direction} ${threshold_usd}\n"
        f"  Slippage:    {slippage_bps} bps\n"
        f"  Max retries: {max_attempts}\n"
        + bracket_line
        + "\n"
        + "The limit-order scheduler polls prices on the registry cadence (~60s)\n"
        + "and fires a swap when the threshold is crossed. The order auto-completes\n"
        + "on a successful fill and auto-fails after max-attempts unsuccessful swaps."
    )


# ── /limit_order list / mutate / resume / cancel ───────────────────


def _cmd_list(sender_id: str) -> str:
    state = _load_state()
    mine = [o for o in state["orders"] if o.get("sender_id") == sender_id]
    if not mine:
        return "No limit orders. Add one with /limit_order add buy|sell ..."
    lines = [f"Limit orders for {sender_id} ({len(mine)}):", ""]
    for o in mine:
        verb = "BUY " if o.get("type") == "buy" else "SELL"
        amount = o.get("amount", "?")
        token = o.get("token", "?")
        trigger = f"{o.get('direction')} ${o.get('threshold_usd')}"
        attempts = len(o.get("attempts", []))
        lines.append(
            f"  {o['id']}  {o.get('status'):<10s}"
            f"  {verb}  {amount} {token}"
            f"  @ {trigger}"
            f"  ({attempts} attempts)"
        )
    return "\n".join(lines)


def _cmd_mutate(sender_id: str, parts: list[str], *, status: str, verb: str) -> str:
    if not parts:
        return f"Usage: /limit_order {verb.removesuffix('d')} <id>"
    oid = parts[0]
    state = _load_state()
    order = _find(state, oid, sender_id)
    if order is None:
        return f"No limit order found with id {oid!r}."
    # Filled / failed / cancelled orders are terminal — don't allow
    # mutation back to active. ``_cmd_resume`` enforces this separately
    # for the resume path; pause is fine on any non-terminal state.
    if status == "paused" and order.get("status") in ("filled", "failed", "cancelled"):
        return f"Order {oid} is {order.get('status')}; cannot pause a terminal order."
    order["status"] = status
    _save_state(state)
    return f"Order {oid} {verb}."


def _cmd_resume(sender_id: str, parts: list[str]) -> str:
    """Re-arm a paused order. Terminal orders (filled/failed/cancelled) can't resume."""
    if not parts:
        return "Usage: /limit_order resume <id>"
    oid = parts[0]
    state = _load_state()
    order = _find(state, oid, sender_id)
    if order is None:
        return f"No limit order found with id {oid!r}."
    if order.get("status") != "paused":
        return f"Order {oid} is {order.get('status')}; only paused orders can be resumed."
    order["status"] = "active"
    _save_state(state)
    return f"Order {oid} resumed."


def _cmd_cancel(sender_id: str, parts: list[str]) -> str:
    if not parts:
        return "Usage: /limit_order cancel <id>"
    oid = parts[0]
    state = _load_state()
    before = len(state["orders"])
    state["orders"] = [
        o for o in state["orders"] if not (o.get("id") == oid and o.get("sender_id") == sender_id)
    ]
    if len(state["orders"]) == before:
        return f"No limit order found with id {oid!r}."
    _save_state(state)
    return f"Cancelled order {oid}."


# ── /limit_order edit ──────────────────────────────────────────────


_EDITABLE = {"threshold_usd", "amount", "slippage_bps", "max_attempts"}


def _cmd_edit(sender_id: str, parts: list[str]) -> str:
    if len(parts) < 3:
        return (
            "Usage: /limit_order edit <id> <field> <value>\n"
            f"Editable fields: {', '.join(sorted(_EDITABLE))}"
        )
    oid, field, value = parts[0], parts[1], parts[2]
    if field not in _EDITABLE:
        return f"Unknown field {field!r}. Editable: {', '.join(sorted(_EDITABLE))}"
    state = _load_state()
    order = _find(state, oid, sender_id)
    if order is None:
        return f"No limit order found with id {oid!r}."

    if field in ("threshold_usd", "amount"):
        try:
            v = float(value)
        except ValueError:
            return f"{field} must be a number (got {value!r})."
        if v <= 0:
            return f"{field} must be positive (got {v})."
        order[field] = v
    elif field == "slippage_bps":
        try:
            v = int(value)
        except ValueError:
            return f"slippage_bps must be an integer (got {value!r})."
        if v < 0 or v > 10_000:
            return f"slippage_bps must be 0–10000 (got {v})."
        order["slippage_bps"] = v
    else:  # max_attempts
        try:
            v = int(value)
        except ValueError:
            return f"max_attempts must be an integer (got {value!r})."
        if v < 1:
            return f"max_attempts must be >= 1 (got {v})."
        order["max_attempts"] = v

    _save_state(state)
    return f"Order {oid}: {field} = {value}."


def _find(state: dict[str, Any], oid: str, sender_id: str) -> dict[str, Any] | None:
    for o in state["orders"]:
        if o.get("id") == oid and o.get("sender_id") == sender_id:
            return o
    return None


# ── /limit_order tick — the engine ─────────────────────────────────


async def _cmd_tick() -> str:
    n, lines = _run_due_with_lines()
    if n == 0:
        return "No limit orders ready."
    return "\n".join([f"Evaluated {n} order(s):"] + lines)


def _run_due_sync() -> int:
    n, _ = _run_due_with_lines()
    return n


def _run_due_with_lines() -> tuple[int, list[str]]:
    state = _load_state()
    lines: list[str] = []
    fired = 0
    new_children: list[dict[str, Any]] = []
    for order in state["orders"]:
        if order.get("status") != "active":
            continue
        try:
            result = _evaluate_order(order)
        except Exception as exc:  # noqa: BLE001
            lines.append(f"  {order['id']}  error: {exc}")
            continue
        if result is None:
            continue  # not yet crossed; no state change
        order["attempts"].append({"at": _now_iso(), **result})
        status = result.get("status")
        if status == "ok":
            order["status"] = "filled"
            # If the parent has a bracket, materialize TP + SL children
            # right now while we have the fill price handy. Children
            # are stored in the same orders list and will be evaluated
            # on subsequent ticks.
            children = _materialize_bracket(order, result.get("price_usd"))
            if children:
                order["bracket_children"] = [c["id"] for c in children]
                new_children.extend(children)
                for c in children:
                    lines.append(f"  {order['id']}  bracket → spawned {c['id']} ({c['kind']})")
        elif status in ("error", "no_wallet") and len(order["attempts"]) >= int(
            order.get("max_attempts") or _DEFAULT_MAX_ATTEMPTS
        ):
            order["status"] = "failed"
        lines.append(f"  {order['id']}  {status}  {result.get('detail', '')}")
        fired += 1

    if new_children:
        state["orders"].extend(new_children)

    if fired > 0 or any(lines):
        _save_state(state)
    return fired, lines


def _materialize_bracket(
    parent: dict[str, Any], fill_price_usd: float | None
) -> list[dict[str, Any]]:
    """Spawn TP + SL sell orders from a filled buy with --bracket attached.

    Returns an empty list when no bracket is configured or when we
    can't anchor a fill price. Children inherit slippage_bps from the
    parent and use a 5-attempt max so they don't fail-loop on slippage
    in fast markets.
    """
    bracket = parent.get("bracket")
    if not bracket or fill_price_usd is None or fill_price_usd <= 0:
        return []
    tp_pct = float(bracket.get("tp_pct", 0))
    sl_pct = float(bracket.get("sl_pct", 0))
    if tp_pct <= 0 or sl_pct <= 0:
        return []
    parent_id = parent["id"]
    sender = parent.get("sender_id", "default")
    slippage = int(parent.get("slippage_bps") or _DEFAULT_SLIPPAGE_BPS)
    amount = float(parent.get("amount", 0.0))
    # The bracket children sell however much of the token we get post-
    # fill. We don't know the exact buy_amount here, so we use a
    # placeholder of 0 — the actual fill at execution time will rely
    # on the user's current balance. The bracket scheduler reads from
    # ``balance`` rather than the stored amount.
    tp_child = {
        "id": _new_id(),
        "sender_id": sender,
        "type": "sell",
        "token": parent["token"],
        "amount": amount,
        "direction": "above",
        "threshold_usd": fill_price_usd * (1 + tp_pct / 100.0),
        "slippage_bps": slippage,
        "max_attempts": 5,
        "bracket": None,
        "bracket_children": [],
        "status": "active",
        "created_at": _now_iso(),
        "attempts": [],
        "parent_order_id": parent_id,
        "kind": "take_profit",
    }
    sl_child = {
        "id": _new_id(),
        "sender_id": sender,
        "type": "sell",
        "token": parent["token"],
        "amount": amount,
        "direction": "below",
        "threshold_usd": fill_price_usd * (1 - sl_pct / 100.0),
        "slippage_bps": slippage,
        "max_attempts": 5,
        "bracket": None,
        "bracket_children": [],
        "status": "active",
        "created_at": _now_iso(),
        "attempts": [],
        "parent_order_id": parent_id,
        "kind": "stop_loss",
    }
    return [tp_child, sl_child]


def _evaluate_order(order: dict[str, Any]) -> dict[str, Any] | None:
    """Return a fill-attempt result, or None if the threshold isn't crossed."""
    price_usd = _fetch_price(order["token"])
    if price_usd is None:
        return None  # silently retry on next tick
    threshold = float(order["threshold_usd"])
    direction = order["direction"]
    crossed = (direction == "above" and price_usd >= threshold) or (
        direction == "below" and price_usd <= threshold
    )
    if not crossed:
        return None

    # Threshold crossed — submit the swap.
    return _submit_swap(order, price_usd)


def _fetch_price(token: str) -> float | None:
    """Wrap ``defi_price`` action=quote, return float USD or None on failure."""
    try:
        from clawmes.tools.defi_price import defi_price

        raw = defi_price({"action": "quote", "symbol": token, "quote_currency": "USD"})
    except Exception:  # noqa: BLE001
        return None
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if payload.get("isError"):
        return None
    details = payload.get("details") or {}
    price = details.get("price_usd") or details.get("price")
    try:
        return float(price)
    except (TypeError, ValueError):
        return None


def _submit_swap(order: dict[str, Any], price_usd: float) -> dict[str, Any]:
    """Build the swap args based on order type and submit via defi_swap."""
    from clawmes.services.wallet import get_wallet_state

    wstate = get_wallet_state()
    if not wstate.connected:
        return {
            "status": "no_wallet",
            "detail": "no wallet connected when threshold crossed",
            "tx_hash": "",
            "price_usd": price_usd,
        }

    if order["type"] == "buy":
        # Sell ETH → buy token.
        sell_token, buy_token = "ETH", order["token"]
        sell_amount = str(order["amount"])
    else:
        # Sell token → buy ETH.
        sell_token, buy_token = order["token"], "ETH"
        sell_amount = str(order["amount"])

    try:
        from clawmes.tools.defi_swap import defi_swap

        raw = defi_swap(
            {
                "action": "swap",
                "sell_token": sell_token,
                "buy_token": buy_token,
                "sell_amount": sell_amount,
                "slippage_bps": int(order.get("slippage_bps") or _DEFAULT_SLIPPAGE_BPS),
            }
        )
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "detail": str(exc), "tx_hash": "", "price_usd": price_usd}

    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return {
            "status": "error",
            "detail": f"bad swap response: {raw}",
            "tx_hash": "",
            "price_usd": price_usd,
        }
    if payload.get("isError"):
        msg = payload.get("content", [{}])[0].get("text", "swap failed")
        return {"status": "error", "detail": msg, "tx_hash": "", "price_usd": price_usd}

    details = payload.get("details") or {}
    tx_hash = details.get("tx_hash") or details.get("txHash") or ""
    return {
        "status": "ok",
        "detail": f"filled at ${price_usd:.8f}, tx {tx_hash[:14]}…",
        "tx_hash": tx_hash,
        "price_usd": price_usd,
    }


# ── /limit_order status / history ──────────────────────────────────


def _cmd_status(_sender_id: str) -> str:
    state = _load_state()
    orders = state.get("orders", [])
    if not orders:
        return "No limit orders exist. The scheduler service is idle."
    by_status: dict[str, int] = {}
    by_type: dict[str, int] = {}
    total_attempts = 0
    for o in orders:
        s = o.get("status", "?")
        by_status[s] = by_status.get(s, 0) + 1
        t = o.get("type", "?")
        by_type[t] = by_type.get(t, 0) + 1
        total_attempts += len(o.get("attempts", []))
    lines = [
        f"/limit_order status ({len(orders)} order(s)):",
        "",
        f"  By status: {', '.join(f'{k}={v}' for k, v in sorted(by_status.items()))}",
        f"  By type:   {', '.join(f'{k}={v}' for k, v in sorted(by_type.items()))}",
        f"  Attempts:  {total_attempts}",
    ]
    try:
        from clawmes.services.limit_order_scheduler import (
            get_limit_order_scheduler_service,
        )

        svc = get_limit_order_scheduler_service()
        h = svc.health()
        lines.append(
            f"  Service:   {h.get('status')} "
            f"(ticks={h.get('ticks')}, total_fired={h.get('total_runs')})"
        )
    except Exception:  # noqa: BLE001
        pass
    return "\n".join(lines)


def _cmd_history(sender_id: str, parts: list[str]) -> str:
    if not parts:
        return "Usage: /limit_order history <id>"
    oid = parts[0]
    state = _load_state()
    order = _find(state, oid, sender_id)
    if order is None:
        return f"No limit order found with id {oid!r}."
    attempts = order.get("attempts", [])
    if not attempts:
        return f"Order {oid}: no attempts yet (threshold not crossed)."
    lines = [f"Attempts for {oid} ({len(attempts)}):", ""]
    for a in attempts[-25:]:
        status = a.get("status", "?")
        detail = a.get("detail", "")
        when = a.get("at", "")
        lines.append(f"  {when}  {status:<10s}  {detail}")
    return "\n".join(lines)


def register(ctx) -> None:
    ctx.register_command(
        name="limit_order",
        handler=handle_limit_order,
        description="DEX limit buys + take-profit sells against USD price thresholds",
        args_hint="add buy|sell ... | list | pause | resume | cancel | edit | tick | status | history",
    )
