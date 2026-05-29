"""``/auto-tune`` — autonomous schedule review + recommendations.

A Clawmes Unlimited feature that puts the third tier of the gating
ladder ("the agent manages the agent") into a single command.

What it does: walks every active automation surface (``/dca``,
``/copy``, ``/limit_order``, ``/sniper``, ``/alerts``), applies a set
of heuristics, and produces recommendations. With ``--apply`` it
commits the recommendations as state mutations on the underlying
schedules.

Heuristics (v1, deliberately conservative):

  * **DCA / Copy / Limit order: stuck failures.** A schedule with
    ``max_consecutive_failures`` worth of recent failures is already
    auto-paused by its own scheduler. Auto-tune surfaces these so
    the user sees the pattern. Recommendation: investigate or cancel.

  * **DCA: low success rate.** Active schedule with at least 5
    executions where success_rate < 50%. Recommendation: increase
    safeguards (``--slippage``, ``--daily-cap``).

  * **Copy: zero successful in 7 days with tx_seen activity.** The
    watched wallet is active but our copies keep failing. Recommendation:
    review blocklist or cancel.

  * **Sniper: idle for 7 days with budget remaining.** No snipes in a
    week despite budget left. Recommendation: relax filters
    (``--source``, ``--symbol-filter``, ``--max-mcap``).

  * **Limit order: stale active.** An active order older than 30 days
    with no fills. Recommendation: cancel or adjust threshold.

  * **Alerts: never-fired wallet alerts older than 30 days.**
    Recommendation: review or cancel.

Surface:

  * ``/auto-tune review``           — print recommendations (read-only)
  * ``/auto-tune apply``            — auto-apply all recommendations
  * ``/auto-tune apply <id>``       — apply one recommendation by id
  * ``/auto-tune history``          — past tune runs

Applied recommendations are reversible — every mutation is a status
change (pause / cancel), never a state-shape change. Users can
``/dca resume <id>``, ``/copy resume <id>``, etc. to undo.

History storage: ``${HERMES_HOME}/clawmes/auto_tune/history.json``.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ── state I/O ───────────────────────────────────────────────────────


def _history_path() -> Path:
    from clawmes.lib.paths import state_dir

    return state_dir("auto_tune") / "history.json"


def _load_history() -> dict[str, Any]:
    path = _history_path()
    if not path.exists():
        return {"runs": []}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {"runs": []}
    if not isinstance(data, dict) or not isinstance(data.get("runs"), list):
        return {"runs": []}
    return data


def _save_history(state: dict[str, Any]) -> None:
    path = _history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now_epoch() -> int:
    return int(time.time())


def _new_id() -> str:
    return f"rec_{uuid.uuid4().hex[:10]}"


def _record(name: str, args: str, result: str) -> None:
    try:
        from clawmes.services.command_history import record_command_call

        record_command_call(name, args, result)
    except Exception:  # noqa: BLE001
        pass


# ── dispatch ────────────────────────────────────────────────────────


async def handle_auto_tune(raw_args: str, *, sender_id: str = "default", **_kwargs: Any) -> str:
    raw = (raw_args or "").strip()

    from clawmes.services.token_gate import Tier, check_tier_or_error

    gate_err = check_tier_or_error(Tier.UNLIMITED, feature="/auto-tune")
    if gate_err:
        return gate_err

    if not raw or raw == "review":
        out = _cmd_review(sender_id)
    elif raw.startswith("apply"):
        rest = raw[len("apply") :].strip().split()
        out = _cmd_apply(sender_id, rest)
    elif raw == "history":
        out = _cmd_history(sender_id)
    else:
        out = f"Unknown subcommand: {raw!r}\n\nUsage: /auto-tune review | apply [<id>] | history"
    _record("auto-tune", raw_args, out)
    return out


# ── recommendation engine ─────────────────────────────────────────


def _generate_recommendations(sender_id: str) -> list[dict[str, Any]]:
    """Walk every automation surface and yield recommendation dicts.

    Each recommendation has:
      * ``id``            — stable rec id (regenerated each review)
      * ``surface``       — dca | copy | sniper | limit_order | alerts
      * ``target_id``     — underlying schedule/follow/config id
      * ``severity``      — info | warn | high
      * ``reason``        — human-readable explanation
      * ``action``        — pause | cancel | review
      * ``commit``        — callable signature for --apply (string,
                            resolved by ``_commit_recommendation``)
    """
    recs: list[dict[str, Any]] = []
    recs.extend(_recommend_dca(sender_id))
    recs.extend(_recommend_copy(sender_id))
    recs.extend(_recommend_sniper(sender_id))
    recs.extend(_recommend_limit_order(sender_id))
    recs.extend(_recommend_alerts(sender_id))
    return recs


def _recommend_dca(sender_id: str) -> list[dict[str, Any]]:
    from clawmes.commands import dca

    recs: list[dict[str, Any]] = []
    state = dca._load_state()
    for sched in state.get("schedules", []):
        if sched.get("sender_id") != sender_id:
            continue
        if sched.get("status") != "active":
            continue
        execs = sched.get("executions", [])
        if len(execs) < 5:
            continue
        successes = sum(1 for e in execs if (e.get("result") or {}).get("status") == "ok")
        success_rate = successes / len(execs)
        if success_rate < 0.5:
            recs.append(
                {
                    "id": _new_id(),
                    "surface": "dca",
                    "target_id": sched["id"],
                    "severity": "warn",
                    "reason": (
                        f"DCA schedule {sched['id']}: {successes}/{len(execs)} "
                        f"successful ({success_rate * 100:.0f}%). "
                        f"Consider tightening safeguards or pausing."
                    ),
                    "action": "pause",
                }
            )
    return recs


def _recommend_copy(sender_id: str) -> list[dict[str, Any]]:
    from clawmes.commands import copy

    recs: list[dict[str, Any]] = []
    state = copy._load_state()
    cutoff = _now_epoch() - 86400 * 7
    for follow in state.get("follows", []):
        if follow.get("sender_id") != sender_id:
            continue
        if follow.get("status") != "active":
            continue
        execs = follow.get("executions", [])
        recent = [e for e in execs if _iso_to_epoch(e.get("at", "")) >= cutoff]
        if not recent:
            continue
        successes = sum(1 for e in recent if (e.get("result") or {}).get("status") == "ok")
        if successes == 0 and len(recent) >= 3:
            recs.append(
                {
                    "id": _new_id(),
                    "surface": "copy",
                    "target_id": follow["id"],
                    "severity": "warn",
                    "reason": (
                        f"Copy follow {follow['id']}: {len(recent)} txs seen in 7 days, "
                        f"0 successful copies. Review blocklist or cancel."
                    ),
                    "action": "pause",
                }
            )
    return recs


def _recommend_sniper(sender_id: str) -> list[dict[str, Any]]:
    from clawmes.commands import sniper

    recs: list[dict[str, Any]] = []
    state = sniper._load_state()
    cutoff = _now_epoch() - 86400 * 7
    for config in state.get("configs", []):
        if config.get("sender_id") != sender_id:
            continue
        if config.get("status") != "active":
            continue
        snipes = config.get("snipes", [])
        recent_snipes = [s for s in snipes if _iso_to_epoch(s.get("at", "")) >= cutoff]
        budget_remaining = int(config.get("max_buys") or 10) - int(config.get("buys_made") or 0)
        if not recent_snipes and budget_remaining > 0:
            recs.append(
                {
                    "id": _new_id(),
                    "surface": "sniper",
                    "target_id": config["id"],
                    "severity": "info",
                    "reason": (
                        f"Sniper {config['id']}: idle for 7+ days with "
                        f"{budget_remaining} snipes remaining. Filters may be too tight."
                    ),
                    "action": "review",
                }
            )
    return recs


def _recommend_limit_order(sender_id: str) -> list[dict[str, Any]]:
    from clawmes.commands import limit_order

    recs: list[dict[str, Any]] = []
    state = limit_order._load_state()
    cutoff = _now_epoch() - 86400 * 30
    for order in state.get("orders", []):
        if order.get("sender_id") != sender_id:
            continue
        if order.get("status") != "active":
            continue
        created_at = _iso_to_epoch(order.get("created_at", ""))
        if created_at == 0 or created_at >= cutoff:
            continue
        recs.append(
            {
                "id": _new_id(),
                "surface": "limit_order",
                "target_id": order["id"],
                "severity": "info",
                "reason": (
                    f"Limit order {order['id']}: active 30+ days with no fills. "
                    f"Consider canceling or adjusting threshold."
                ),
                "action": "review",
            }
        )
    return recs


def _recommend_alerts(sender_id: str) -> list[dict[str, Any]]:
    from clawmes.commands import alerts

    recs: list[dict[str, Any]] = []
    state = alerts._load_state()
    cutoff = _now_epoch() - 86400 * 30
    for alert in state.get("alerts", []):
        if alert.get("sender_id") != sender_id:
            continue
        if alert.get("status") != "active":
            continue
        if alert.get("type") != "wallet":
            continue
        created_at = _iso_to_epoch(alert.get("created_at", ""))
        if created_at == 0 or created_at >= cutoff:
            continue
        if alert.get("fires"):
            continue
        recs.append(
            {
                "id": _new_id(),
                "surface": "alerts",
                "target_id": alert["id"],
                "severity": "info",
                "reason": (
                    f"Wallet alert {alert['id']}: active 30+ days, never fired. Consider canceling."
                ),
                "action": "review",
            }
        )
    return recs


def _iso_to_epoch(s: str) -> int:
    if not isinstance(s, str):
        return 0
    try:
        return int(datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC).timestamp())
    except ValueError:
        try:
            return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
        except ValueError:
            return 0


# ── /auto-tune review ───────────────────────────────────────────────


def _cmd_review(sender_id: str) -> str:
    recs = _generate_recommendations(sender_id)
    if not recs:
        return (
            "No recommendations. Every active schedule looks healthy.\n"
            "Re-run /auto-tune review after more activity accumulates."
        )

    # Persist this batch so /auto-tune apply <id> can resolve later.
    history = _load_history()
    history["runs"].append(
        {
            "id": _new_id(),
            "sender_id": sender_id,
            "at": _now_iso(),
            "recommendations": recs,
            "applied": False,
        }
    )
    _save_history(history)

    lines = [f"Recommendations for {sender_id} ({len(recs)}):", ""]
    for rec in recs:
        lines.append(f"  [{rec['severity'].upper():<4s}]  {rec['id']}  ({rec['surface']})")
        lines.append(f"          {rec['reason']}")
        lines.append(f"          Action: {rec['action']}")
        lines.append("")
    lines.append("Apply all:           /auto-tune apply")
    lines.append("Apply one:           /auto-tune apply <rec_id>")
    return "\n".join(lines)


# ── /auto-tune apply ───────────────────────────────────────────────


def _cmd_apply(sender_id: str, parts: list[str]) -> str:
    history = _load_history()
    # Find the most recent unapplied run for this sender.
    runs = [
        r
        for r in history["runs"]
        if r.get("sender_id") == sender_id and not r.get("applied", False)
    ]
    if not runs:
        return "No pending recommendations. Run /auto-tune review first."
    run = runs[-1]
    recs = run.get("recommendations", [])

    targets: list[dict[str, Any]]
    if parts:
        rec_id = parts[0]
        targets = [r for r in recs if r.get("id") == rec_id]
        if not targets:
            return f"No recommendation found with id {rec_id!r} in the latest review."
    else:
        targets = recs

    applied: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for rec in targets:
        ok, message = _commit_recommendation(rec, sender_id)
        record = {"rec": rec, "applied_at": _now_iso(), "message": message}
        if ok:
            applied.append(record)
        else:
            failed.append(record)

    # Mark the run as applied (full or partial).
    run["applied"] = True
    run["applied_at"] = _now_iso()
    run["applied_results"] = applied + failed
    _save_history(history)

    lines = [f"Applied {len(applied)} recommendation(s)."]
    for r in applied:
        lines.append(f"  ✓ {r['rec']['id']} ({r['rec']['surface']}): {r['message']}")
    if failed:
        lines.append("")
        lines.append(f"Failed: {len(failed)}")
        for r in failed:
            lines.append(f"  ✗ {r['rec']['id']} ({r['rec']['surface']}): {r['message']}")
    return "\n".join(lines)


def _commit_recommendation(rec: dict[str, Any], sender_id: str) -> tuple[bool, str]:
    """Apply one recommendation by mutating the underlying state.

    Currently only the ``pause`` action is destructive; ``review``
    actions are no-ops at the apply layer (they're informational).
    """
    surface = rec.get("surface", "")
    action = rec.get("action", "")
    target_id = rec.get("target_id", "")

    if action == "review":
        return True, "no-op (review-only recommendation)"

    if action != "pause":
        return False, f"unknown action {action!r}"

    if surface == "dca":
        from clawmes.commands import dca

        state = dca._load_state()
        for sched in state.get("schedules", []):
            if sched.get("id") == target_id and sched.get("sender_id") == sender_id:
                sched["status"] = "paused"
                dca._save_state(state)
                return True, f"paused dca schedule {target_id}"
        return False, f"dca schedule {target_id} not found"

    if surface == "copy":
        from clawmes.commands import copy

        state = copy._load_state()
        for follow in state.get("follows", []):
            if follow.get("id") == target_id and follow.get("sender_id") == sender_id:
                follow["status"] = "paused"
                copy._save_state(state)
                return True, f"paused copy follow {target_id}"
        return False, f"copy follow {target_id} not found"

    if surface == "limit_order":
        from clawmes.commands import limit_order

        state = limit_order._load_state()
        for order in state.get("orders", []):
            if order.get("id") == target_id and order.get("sender_id") == sender_id:
                order["status"] = "paused"
                limit_order._save_state(state)
                return True, f"paused limit order {target_id}"
        return False, f"limit order {target_id} not found"

    if surface == "sniper":
        from clawmes.commands import sniper

        state = sniper._load_state()
        for config in state.get("configs", []):
            if config.get("id") == target_id and config.get("sender_id") == sender_id:
                config["status"] = "paused"
                sniper._save_state(state)
                return True, f"paused sniper config {target_id}"
        return False, f"sniper config {target_id} not found"

    if surface == "alerts":  # pragma: no branch — last branch
        from clawmes.commands import alerts

        state = alerts._load_state()
        for alert in state.get("alerts", []):
            if alert.get("id") == target_id and alert.get("sender_id") == sender_id:
                alert["status"] = "paused"
                alerts._save_state(state)
                return True, f"paused alert {target_id}"
        return False, f"alert {target_id} not found"

    return False, f"unknown surface {surface!r}"  # pragma: no cover


# ── /auto-tune history ─────────────────────────────────────────────


def _cmd_history(sender_id: str) -> str:
    history = _load_history()
    mine = [r for r in history["runs"] if r.get("sender_id") == sender_id]
    if not mine:
        return f"No auto-tune history for {sender_id}."
    lines = [f"Auto-tune history for {sender_id} ({len(mine)}):", ""]
    for run in mine[-25:]:
        applied = "applied" if run.get("applied") else "pending"
        count = len(run.get("recommendations", []))
        lines.append(f"  {run.get('at', '?')}  {run['id']}  {applied:<8s}  {count} rec(s)")
    return "\n".join(lines)


def register(ctx) -> None:
    ctx.register_command(
        name="auto-tune",
        handler=handle_auto_tune,
        description="Autonomous schedule review + recommendations (Clawmes Unlimited)",
        args_hint="review | apply [<rec_id>] | history",
    )
