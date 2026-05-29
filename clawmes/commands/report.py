"""``/report`` — autonomous performance reports across every automation surface.

A Clawmes Unlimited feature. The tier framing:

  * FREE   — you do the work with the agent.
  * HOLDER — you manage the agent.
  * UNLIMITED — the agent manages the agent.

``/report`` is part of the third tier: the agent reviews itself and
surfaces what happened, what worked, what failed, and what's worth
adjusting. The user doesn't have to remember which schedules they
created — the report finds them all.

Surface:

  * ``/report now``                  — aggregate snapshot for right-now
  * ``/report daily``                — daily summary (24h window)
  * ``/report weekly``               — weekly summary (7d window)
  * ``/report objectives``           — progress toward registered objectives

Aggregates from:

  * ``/dca``           — schedules + execution history
  * ``/copy``          — follows + execution history
  * ``/alerts``        — alerts + fire history
  * ``/limit_order``   — orders + attempt history
  * ``/sniper``        — configs + snipe history
  * ``/objective``     — registered goals + progress
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now_epoch() -> int:
    return int(time.time())


def _record(name: str, args: str, result: str) -> None:
    try:
        from clawmes.services.command_history import record_command_call

        record_command_call(name, args, result)
    except Exception:  # noqa: BLE001
        pass


# ── dispatch ────────────────────────────────────────────────────────


async def handle_report(raw_args: str, *, sender_id: str = "default", **_kwargs: Any) -> str:
    raw = (raw_args or "").strip().lower()

    # UNLIMITED-tier gate.
    from clawmes.services.token_gate import Tier, check_tier_or_error

    gate_err = check_tier_or_error(Tier.UNLIMITED, feature="/report")
    if gate_err:
        return gate_err

    if not raw or raw == "now":
        out = _render_report(sender_id, window_seconds=None, header="Now")
    elif raw == "daily":
        out = _render_report(sender_id, window_seconds=86400, header="Last 24 hours")
    elif raw == "weekly":
        out = _render_report(sender_id, window_seconds=86400 * 7, header="Last 7 days")
    elif raw == "objectives":
        out = _render_objectives(sender_id)
    else:
        out = f"Unknown report mode: {raw!r}\n\nUsage: /report now | daily | weekly | objectives"
    _record("report", raw_args, out)
    return out


# ── aggregation ────────────────────────────────────────────────────


def _parse_iso_to_epoch(s: str) -> int:
    """Best-effort ISO-8601 → epoch. Returns 0 on failure."""
    if not isinstance(s, str):
        return 0
    try:
        return int(datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC).timestamp())
    except ValueError:
        try:
            return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
        except ValueError:
            return 0


def _within_window(at_iso: str, window_seconds: int | None) -> bool:
    """Whether ``at_iso`` falls inside the lookback window."""
    if window_seconds is None:
        return True
    ts = _parse_iso_to_epoch(at_iso)
    if ts == 0:
        return False
    return ts >= _now_epoch() - window_seconds


def _summarize_dca(sender_id: str, window_seconds: int | None) -> dict[str, Any]:
    """Pull DCA stats for the sender within the window."""
    from clawmes.commands import dca

    state = dca._load_state()
    mine = [s for s in state.get("schedules", []) if s.get("sender_id") == sender_id]
    active = sum(1 for s in mine if s.get("status") == "active")
    paused = sum(1 for s in mine if s.get("status") == "paused")
    executions = 0
    successful = 0
    failed = 0
    eth_spent = 0.0
    for s in mine:
        for ex in s.get("executions", []):
            if not _within_window(ex.get("at", ""), window_seconds):
                continue
            executions += 1
            status = (ex.get("result") or {}).get("status", "")
            if status == "ok":
                successful += 1
                eth_spent += float(s.get("eth_amount", 0.0))
            elif status in ("error", "no_wallet", "daily_capped", "total_capped"):
                failed += 1
    return {
        "schedules": len(mine),
        "active": active,
        "paused": paused,
        "executions": executions,
        "successful": successful,
        "failed": failed,
        "eth_spent": eth_spent,
    }


def _summarize_copy(sender_id: str, window_seconds: int | None) -> dict[str, Any]:
    """Pull /copy stats for the sender within the window."""
    from clawmes.commands import copy

    state = copy._load_state()
    mine = [f for f in state.get("follows", []) if f.get("sender_id") == sender_id]
    active = sum(1 for f in mine if f.get("status") == "active")
    paused = sum(1 for f in mine if f.get("status") == "paused")
    executions = 0
    successful = 0
    failed = 0
    blocklisted = 0
    eth_spent = 0.0
    for f in mine:
        for ex in f.get("executions", []):
            if not _within_window(ex.get("at", ""), window_seconds):
                continue
            executions += 1
            status = (ex.get("result") or {}).get("status", "")
            if status == "ok":
                successful += 1
                eth_spent += float(ex.get("eth_amount", 0.0))
            elif status == "blocklisted":
                blocklisted += 1
            elif status in ("error", "no_wallet", "daily_capped", "total_capped"):
                failed += 1
    return {
        "follows": len(mine),
        "active": active,
        "paused": paused,
        "executions": executions,
        "successful": successful,
        "failed": failed,
        "blocklisted": blocklisted,
        "eth_spent": eth_spent,
    }


def _summarize_alerts(sender_id: str, window_seconds: int | None) -> dict[str, Any]:
    """Pull /alerts stats."""
    from clawmes.commands import alerts

    state = alerts._load_state()
    mine = [a for a in state.get("alerts", []) if a.get("sender_id") == sender_id]
    active = sum(1 for a in mine if a.get("status") == "active")
    fired_status = sum(1 for a in mine if a.get("status") == "fired")
    fires_in_window = 0
    for a in mine:
        for f in a.get("fires", []):
            if not _within_window(f.get("at", ""), window_seconds):
                continue
            fires_in_window += 1
    return {
        "alerts": len(mine),
        "active": active,
        "fired_status": fired_status,
        "fires": fires_in_window,
    }


def _summarize_limit_orders(sender_id: str, window_seconds: int | None) -> dict[str, Any]:
    """Pull /limit_order stats."""
    from clawmes.commands import limit_order

    state = limit_order._load_state()
    mine = [o for o in state.get("orders", []) if o.get("sender_id") == sender_id]
    by_status: dict[str, int] = {}
    for o in mine:
        st = o.get("status", "?")
        by_status[st] = by_status.get(st, 0) + 1
    attempts_in_window = 0
    fills_in_window = 0
    for o in mine:
        for at in o.get("attempts", []):
            if not _within_window(at.get("at", ""), window_seconds):
                continue
            attempts_in_window += 1
            if at.get("status") == "ok":
                fills_in_window += 1
    return {
        "orders": len(mine),
        "by_status": by_status,
        "attempts": attempts_in_window,
        "fills": fills_in_window,
    }


def _summarize_sniper(sender_id: str, window_seconds: int | None) -> dict[str, Any]:
    """Pull /sniper stats."""
    from clawmes.commands import sniper

    state = sniper._load_state()
    mine = [c for c in state.get("configs", []) if c.get("sender_id") == sender_id]
    active = sum(1 for c in mine if c.get("status") == "active")
    snipes_in_window = 0
    successful_in_window = 0
    auto_sells_in_window = 0
    for c in mine:
        for s in c.get("snipes", []):
            if not _within_window(s.get("at", ""), window_seconds):
                continue
            snipes_in_window += 1
            if (s.get("result") or {}).get("status") == "ok":
                successful_in_window += 1
        for w in c.get("auto_sell_watches", []):
            closed_at = w.get("closed_at")
            if closed_at and _within_window(closed_at, window_seconds):
                auto_sells_in_window += 1
    return {
        "configs": len(mine),
        "active": active,
        "snipes": snipes_in_window,
        "successful": successful_in_window,
        "auto_sells": auto_sells_in_window,
    }


# ── render ──────────────────────────────────────────────────────────


def _render_report(sender_id: str, *, window_seconds: int | None, header: str) -> str:
    dca_stats = _summarize_dca(sender_id, window_seconds)
    copy_stats = _summarize_copy(sender_id, window_seconds)
    alerts_stats = _summarize_alerts(sender_id, window_seconds)
    limit_stats = _summarize_limit_orders(sender_id, window_seconds)
    sniper_stats = _summarize_sniper(sender_id, window_seconds)

    lines = [
        f"clawmes report for {sender_id} — {header}",
        f"Generated: {_now_iso()}",
        "",
        "AUTOMATION COUNTS",
        f"  /dca           {dca_stats['active']} active, {dca_stats['paused']} paused, {dca_stats['schedules']} total",
        f"  /copy          {copy_stats['active']} active, {copy_stats['paused']} paused, {copy_stats['follows']} total",
        f"  /alerts        {alerts_stats['active']} active, {alerts_stats['fired_status']} fired, {alerts_stats['alerts']} total",
        f"  /limit_order   {limit_stats['orders']} total ({_format_status_breakdown(limit_stats['by_status'])})",
        f"  /sniper        {sniper_stats['active']} active, {sniper_stats['configs']} total",
        "",
        "EXECUTION ACTIVITY",
        f"  /dca           {dca_stats['successful']} successful / {dca_stats['failed']} failed / {dca_stats['executions']} total",
        f"                 {dca_stats['eth_spent']:.6f} ETH spent",
        f"  /copy          {copy_stats['successful']} successful / {copy_stats['failed']} failed / {copy_stats['blocklisted']} blocklisted",
        f"                 {copy_stats['eth_spent']:.6f} ETH spent",
        f"  /alerts        {alerts_stats['fires']} fires",
        f"  /limit_order   {limit_stats['attempts']} attempts / {limit_stats['fills']} fills",
        f"  /sniper        {sniper_stats['snipes']} snipes / {sniper_stats['successful']} successful / {sniper_stats['auto_sells']} auto-sells closed",
        "",
    ]

    total_eth = dca_stats["eth_spent"] + copy_stats["eth_spent"]
    lines.append(f"TOTAL ETH SPENT: {total_eth:.6f}")
    lines.append("")
    lines.append("Anomalies + recommendations: /auto-tune review")
    lines.append("Active goals + progress:     /report objectives")
    return "\n".join(lines)


def _format_status_breakdown(by_status: dict[str, int]) -> str:
    """Compact "active=N filled=M failed=K" rendering."""
    if not by_status:
        return "none"
    return " ".join(f"{k}={v}" for k, v in sorted(by_status.items()))


def _render_objectives(sender_id: str) -> str:
    """Show registered objectives + progress."""
    from clawmes.commands import objective

    state = objective._load_state()
    mine = [o for o in state.get("objectives", []) if o.get("sender_id") == sender_id]
    if not mine:
        return "No objectives registered. Set one with /objective add <name> <goal> --budget <eth>."

    lines = [f"Objectives for {sender_id} ({len(mine)}):", ""]
    for obj in mine:
        progress = objective._compute_progress(obj, sender_id)
        budget = float(obj.get("budget_eth", 0.0))
        spent = progress["eth_spent"]
        pct = (spent / budget * 100.0) if budget > 0 else 0.0
        lines.extend(
            [
                f"  {obj['id']}  {obj.get('status'):<8s}  {obj.get('name', '?')}",
                f"      Goal:     {obj.get('goal', '?')}",
                f"      Budget:   {budget:.6f} ETH (spent {spent:.6f}, {pct:.1f}%)",
                f"      Started:  {obj.get('created_at', '?')}",
            ]
        )
    return "\n".join(lines)


def register(ctx) -> None:
    ctx.register_command(
        name="report",
        handler=handle_report,
        description="Autonomous performance reports across every automation surface (Clawmes Unlimited)",
        args_hint="now | daily | weekly | objectives",
    )
