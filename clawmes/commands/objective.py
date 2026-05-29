"""``/objective`` — high-level goal tracking layered over automation.

A Clawmes Unlimited feature. Translates "I want X" into a persistent
goal that the agent watches against, surfaces in reports, and uses
to bias auto-tune recommendations.

The agent doesn't yet *autonomously create* new schedules to pursue
an objective — that's deferred to a future release. What it does in
v0.14.0:

  * Persists named goals with budget + horizon + free-text goal
  * Computes progress: sum of ETH spent across the user's automation
    surfaces since the objective was registered
  * Surfaces objectives in ``/report objectives``
  * (Hooks for ``/auto-tune`` to bias recommendations toward
    objectives, in the same release)

Surface:

  * ``/objective add <name> <goal> --budget <eth> [--horizon <interval>]``
  * ``/objective list``
  * ``/objective pause <id>`` / ``resume`` / ``cancel``
  * ``/objective progress <id>``

The goal text is free-form. It's stored as metadata, surfaced in
reports, and consumed (eventually) by an LLM-backed planner. For
v0.14.0 it's documentation — useful for the user to remember why
they set up a given collection of schedules.

Storage: ``${HERMES_HOME}/clawmes/objectives/objectives.json``.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ── interval grammar (mirror of dca) ────────────────────────────────


_INTERVAL_RE = re.compile(r"^\s*(\d+)\s*([mhdwMHDW])\s*$")
_INTERVAL_UNITS = {
    "m": 60,
    "h": 60 * 60,
    "d": 60 * 60 * 24,
    "w": 60 * 60 * 24 * 7,
}


def _parse_horizon(raw: str) -> int | None:
    m = _INTERVAL_RE.match(raw or "")
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2).lower()
    return n * _INTERVAL_UNITS[unit]


# ── state I/O ───────────────────────────────────────────────────────


def _objectives_path() -> Path:
    from clawmes.lib.paths import state_dir

    return state_dir("objectives") / "objectives.json"


def _load_state() -> dict[str, Any]:
    path = _objectives_path()
    if not path.exists():
        return {"objectives": []}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {"objectives": []}
    if not isinstance(data, dict) or not isinstance(data.get("objectives"), list):
        return {"objectives": []}
    return data


def _save_state(state: dict[str, Any]) -> None:
    path = _objectives_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now_epoch() -> int:
    return int(time.time())


def _new_id() -> str:
    return f"obj_{uuid.uuid4().hex[:10]}"


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
            next_tok = parts[i + 1] if i + 1 < len(parts) else None
            if next_tok is not None and not next_tok.startswith("--"):
                flags[name] = next_tok
                i += 2
            else:
                flags[name] = ""
                i += 1
        else:
            positional.append(tok)
            i += 1
    return positional, flags


# ── dispatch ────────────────────────────────────────────────────────


async def handle_objective(raw_args: str, *, sender_id: str = "default", **_kwargs: Any) -> str:
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
            out = _cmd_mutate(sender_id, rest, status="active", verb="resumed")
        elif sub in ("cancel", "rm", "remove"):
            out = _cmd_cancel(sender_id, rest)
        elif sub == "progress":
            out = _cmd_progress(sender_id, rest)
        else:
            out = f"Unknown subcommand: {sub!r}\n\n" + _render_usage()
    _record("objective", raw_args, out)
    return out


def _render_usage() -> str:
    return (
        "Objectives — high-level goal tracking. (Clawmes Unlimited tier.)\n"
        "\n"
        "  /objective add <name> <goal> --budget <eth> [--horizon <interval>]\n"
        "                            Register a new goal with a budget cap\n"
        "  /objective list           Show all active objectives + progress\n"
        "  /objective pause <id>     Suspend (still tracked, not counted in budget)\n"
        "  /objective resume <id>    Re-arm\n"
        "  /objective cancel <id>    Remove entirely\n"
        "  /objective progress <id>  Detailed progress on one objective\n"
        "\n"
        "Example:\n"
        '  /objective add q4-clawnch "accumulate 100M CLAWNCH by EOY" --budget 1.5\n'
        "\n"
        "Objectives are documentation today — they record your intent and\n"
        "surface progress in /report. Future releases will let the agent\n"
        "autonomously create / adjust schedules to pursue an objective."
    )


# ── /objective add ─────────────────────────────────────────────────


def _cmd_add(sender_id: str, parts: list[str]) -> str:
    # UNLIMITED-tier gate.
    from clawmes.services.token_gate import Tier, check_tier_or_error

    gate_err = check_tier_or_error(Tier.UNLIMITED, feature="/objective")
    if gate_err:
        return gate_err

    pos, flags = _split_flags(parts)
    if len(pos) < 2:
        return "Usage: /objective add <name> <goal> --budget <eth> [--horizon <interval>]"
    name = pos[0]
    # The "goal" is the rest of the positional args joined back — lets users
    # write multi-word goals without quoting. ``len(pos) >= 2`` is enforced
    # by the usage check above, so ``goal`` is never empty here.
    goal = " ".join(pos[1:]).strip()

    if "budget" not in flags:
        return "Missing --budget <eth>."
    try:
        budget_eth = float(flags["budget"])
    except ValueError:
        return f"--budget must be a number (got {flags['budget']!r})."
    if budget_eth <= 0:
        return f"--budget must be positive (got {budget_eth})."

    horizon_seconds: int | None = None
    if "horizon" in flags:
        horizon_seconds = _parse_horizon(flags["horizon"])
        if horizon_seconds is None:
            return f"--horizon must be like 1d / 1w / 30d (got {flags['horizon']!r})."

    state = _load_state()
    obj_id = _new_id()
    objective = {
        "id": obj_id,
        "sender_id": sender_id,
        "name": name,
        "goal": goal,
        "budget_eth": budget_eth,
        "horizon_seconds": horizon_seconds,
        "status": "active",
        "created_at": _now_iso(),
        "created_at_epoch": _now_epoch(),
    }
    state["objectives"].append(objective)
    _save_state(state)

    horizon_line = f"  Horizon:  {flags['horizon']}\n" if horizon_seconds is not None else ""
    return (
        f"Objective added: {obj_id}\n"
        f"  Name:     {name}\n"
        f"  Goal:     {goal}\n"
        f"  Budget:   {budget_eth} ETH\n"
        + horizon_line
        + "\n"
        + "Progress is tracked from now forward. Use /report objectives to see\n"
        + "all your goals + spend at any time."
    )


# ── /objective list / mutate / cancel ──────────────────────────────


def _cmd_list(sender_id: str) -> str:
    state = _load_state()
    mine = [o for o in state["objectives"] if o.get("sender_id") == sender_id]
    if not mine:
        return "No objectives. Register one with /objective add <name> <goal> --budget <eth>."
    lines = [f"Objectives for {sender_id} ({len(mine)}):", ""]
    for obj in mine:
        progress = _compute_progress(obj, sender_id)
        budget = float(obj.get("budget_eth", 0.0))
        spent = progress["eth_spent"]
        pct = (spent / budget * 100.0) if budget > 0 else 0.0
        lines.append(
            f"  {obj['id']}  {obj.get('status'):<8s}"
            f"  {obj.get('name', '?')}"
            f"  ({spent:.6f}/{budget:.6f} ETH, {pct:.1f}%)"
        )
    return "\n".join(lines)


def _cmd_mutate(sender_id: str, parts: list[str], *, status: str, verb: str) -> str:
    if not parts:
        return f"Usage: /objective {verb.removesuffix('d')} <id>"
    oid = parts[0]
    state = _load_state()
    for obj in state["objectives"]:
        if obj.get("id") == oid and obj.get("sender_id") == sender_id:
            obj["status"] = status
            _save_state(state)
            return f"Objective {oid} {verb}."
    return f"No objective found with id {oid!r}."


def _cmd_cancel(sender_id: str, parts: list[str]) -> str:
    if not parts:
        return "Usage: /objective cancel <id>"
    oid = parts[0]
    state = _load_state()
    before = len(state["objectives"])
    state["objectives"] = [
        o
        for o in state["objectives"]
        if not (o.get("id") == oid and o.get("sender_id") == sender_id)
    ]
    if len(state["objectives"]) == before:
        return f"No objective found with id {oid!r}."
    _save_state(state)
    return f"Cancelled objective {oid}."


# ── /objective progress ────────────────────────────────────────────


def _cmd_progress(sender_id: str, parts: list[str]) -> str:
    if not parts:
        return "Usage: /objective progress <id>"
    oid = parts[0]
    state = _load_state()
    obj = next(
        (o for o in state["objectives"] if o.get("id") == oid and o.get("sender_id") == sender_id),
        None,
    )
    if obj is None:
        return f"No objective found with id {oid!r}."
    progress = _compute_progress(obj, sender_id)
    budget = float(obj.get("budget_eth", 0.0))
    spent = progress["eth_spent"]
    pct = (spent / budget * 100.0) if budget > 0 else 0.0
    lines = [
        f"Progress on {oid}: {obj.get('name', '?')}",
        f"  Goal:           {obj.get('goal', '?')}",
        f"  Status:         {obj.get('status', '?')}",
        f"  Budget:         {budget:.6f} ETH",
        f"  Spent:          {spent:.6f} ETH ({pct:.1f}%)",
        f"  Started:        {obj.get('created_at', '?')}",
        "",
        "BREAKDOWN BY SOURCE",
        f"  /dca            {progress['dca_eth']:.6f} ETH",
        f"  /copy           {progress['copy_eth']:.6f} ETH",
    ]
    if obj.get("horizon_seconds") is not None:
        elapsed = _now_epoch() - int(obj.get("created_at_epoch", _now_epoch()))
        horizon = int(obj.get("horizon_seconds") or 0)
        elapsed_pct = (elapsed / horizon * 100.0) if horizon > 0 else 0.0
        lines.append(f"  Horizon used:   {elapsed_pct:.1f}% (elapsed {elapsed}s of {horizon}s)")
    return "\n".join(lines)


# ── progress computation ───────────────────────────────────────────


def _compute_progress(obj: dict[str, Any], sender_id: str) -> dict[str, Any]:
    """Sum ETH spent across automation surfaces since the objective started.

    Anchor: ``created_at_epoch``. Anything older isn't counted. This
    makes objectives forward-looking — registering a new one doesn't
    sweep up your historical activity.
    """
    from clawmes.commands import copy, dca

    anchor = int(obj.get("created_at_epoch", 0))
    dca_eth = 0.0
    state = dca._load_state()
    for sched in state.get("schedules", []):
        if sched.get("sender_id") != sender_id:
            continue
        for ex in sched.get("executions", []):
            if (ex.get("result") or {}).get("status") != "ok":
                continue
            ts = _iso_to_epoch(ex.get("at", ""))
            if ts < anchor:
                continue
            dca_eth += float(sched.get("eth_amount", 0.0))

    copy_eth = 0.0
    cstate = copy._load_state()
    for follow in cstate.get("follows", []):
        if follow.get("sender_id") != sender_id:
            continue
        for ex in follow.get("executions", []):
            if (ex.get("result") or {}).get("status") != "ok":
                continue
            ts = _iso_to_epoch(ex.get("at", ""))
            if ts < anchor:
                continue
            copy_eth += float(ex.get("eth_amount", 0.0))

    return {
        "dca_eth": dca_eth,
        "copy_eth": copy_eth,
        "eth_spent": dca_eth + copy_eth,
    }


def _iso_to_epoch(s: str) -> int:
    """Best-effort ISO → epoch. Returns 0 on failure."""
    if not isinstance(s, str):
        return 0
    try:
        return int(datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC).timestamp())
    except ValueError:
        try:
            return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
        except ValueError:
            return 0


def register(ctx) -> None:
    ctx.register_command(
        name="objective",
        handler=handle_objective,
        description="High-level goal tracking layered over automation (Clawmes Unlimited)",
        args_hint="add <name> <goal> --budget <eth> | list | pause | resume | cancel | progress",
    )
