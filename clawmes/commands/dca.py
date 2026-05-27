"""``/dca`` — dollar-cost-average recurring buys.

Recurring ETH-funded buys against a target token. Schedules live in
``${HERMES_HOME}/clawmes/dca/schedules.json`` so they survive process
restarts. Execution happens on ``/dca tick`` — a hook that's safe to
call from Hermes' cron loop (every minute is fine; tick is idempotent
and only runs schedules whose ``next_run_at`` has passed).

Surface:

  * ``/dca add <token> <eth_amount> <interval>``  schedule a recurring buy
  * ``/dca list``                                  show all your schedules
  * ``/dca pause <id>``                            suspend without losing state
  * ``/dca resume <id>``                           re-arm
  * ``/dca cancel <id>``                           remove
  * ``/dca tick``                                  execute any due schedules
  * ``/dca history <id>``                          past executions

Interval grammar: ``1m``, ``30m``, ``1h``, ``4h``, ``1d``, ``1w``.

Each tick:
  * Walks all active schedules whose ``next_run_at <= now``
  * Calls ``defi_swap`` with the configured ``token`` + ``eth_amount``
  * Records the tx hash (or error) on the schedule's history
  * Advances ``next_run_at`` by ``interval_seconds``

Wallet behavior: uses the active wallet at tick time. If no wallet is
connected when a tick fires, the schedule's execution is logged as
``no_wallet`` and ``next_run_at`` still advances (we don't infinitely
retry — the next interval gets a fresh shot).
"""

from __future__ import annotations

import json
import re
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ── interval parsing ────────────────────────────────────────────────


_INTERVAL_RE = re.compile(r"^\s*(\d+)\s*([mhdwMHDW])\s*$")
_INTERVAL_UNITS = {
    "m": 60,
    "h": 60 * 60,
    "d": 60 * 60 * 24,
    "w": 60 * 60 * 24 * 7,
}
_MIN_INTERVAL_SECONDS = 60  # 1m floor; sub-minute DCA is gas-suicide
_MAX_INTERVAL_SECONDS = 60 * 60 * 24 * 365  # 1 year ceiling


def _parse_interval(raw: str) -> int | None:
    """Return the interval as seconds, or ``None`` on parse error.

    Accepts ``5m``, ``1h``, ``6h``, ``1d``, ``1w``. Rejects sub-minute
    intervals (which would burn gas faster than you'd earn anything)
    and intervals over 1 year (which is functionally "set and forget"
    territory where a one-shot buy makes more sense).
    """
    m = _INTERVAL_RE.match(raw or "")
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2).lower()
    seconds = n * _INTERVAL_UNITS[unit]
    if seconds < _MIN_INTERVAL_SECONDS or seconds > _MAX_INTERVAL_SECONDS:
        return None
    return seconds


def _format_interval(seconds: int) -> str:
    """Format seconds back into a human interval string."""
    if seconds % _INTERVAL_UNITS["w"] == 0:
        n = seconds // _INTERVAL_UNITS["w"]
        return f"{n}w"
    if seconds % _INTERVAL_UNITS["d"] == 0:
        n = seconds // _INTERVAL_UNITS["d"]
        return f"{n}d"
    if seconds % _INTERVAL_UNITS["h"] == 0:
        n = seconds // _INTERVAL_UNITS["h"]
        return f"{n}h"
    n = seconds // _INTERVAL_UNITS["m"]
    return f"{n}m"


# ── state I/O ───────────────────────────────────────────────────────


def _schedules_path() -> Path:
    from clawmes.lib.paths import state_dir

    return state_dir("dca") / "schedules.json"


def _load_state() -> dict[str, Any]:
    path = _schedules_path()
    if not path.exists():
        return {"schedules": []}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {"schedules": []}
    if not isinstance(data, dict) or not isinstance(data.get("schedules"), list):
        return {"schedules": []}
    return data


def _save_state(state: dict[str, Any]) -> None:
    path = _schedules_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now_epoch() -> int:
    return int(time.time())


def _new_id() -> str:
    return f"dca_{uuid.uuid4().hex[:10]}"


# ── command surface ─────────────────────────────────────────────────


def _record(name: str, args: str, result: str) -> None:
    try:
        from clawmes.services.command_history import record_command_call

        record_command_call(name, args, result)
    except Exception:  # noqa: BLE001
        pass


async def handle_dca(raw_args: str, *, sender_id: str = "default", **_kwargs: Any) -> str:
    raw = (raw_args or "").strip()
    if not raw:
        out = _render_usage()
    else:
        parts = raw.split()
        sub = parts[0].lower()
        rest = parts[1:]
        if sub == "add":
            out = _cmd_add(sender_id, rest)
        elif sub == "list" or sub == "ls":
            out = _cmd_list(sender_id)
        elif sub == "pause":
            out = _cmd_pause(sender_id, rest)
        elif sub == "resume":
            out = _cmd_resume(sender_id, rest)
        elif sub == "cancel" or sub == "rm" or sub == "remove":
            out = _cmd_cancel(sender_id, rest)
        elif sub == "tick":
            out = await _cmd_tick()
        elif sub == "history":
            out = _cmd_history(sender_id, rest)
        else:
            out = f"Unknown subcommand: {sub!r}\n\n" + _render_usage()
    _record("dca", raw_args, out)
    return out


def _render_usage() -> str:
    return (
        "Dollar-cost average — recurring ETH-funded buys on a target token.\n"
        "\n"
        "  /dca add <token> <eth_amount> <interval>\n"
        "                       Schedule a recurring buy. Interval grammar:\n"
        "                       1m, 30m, 1h, 4h, 1d, 1w  (1m floor, 1y ceiling)\n"
        "  /dca list            Show all your schedules + next run time\n"
        "  /dca pause <id>      Suspend a schedule without removing it\n"
        "  /dca resume <id>     Re-arm a paused schedule\n"
        "  /dca cancel <id>     Remove a schedule entirely\n"
        "  /dca history <id>    Past executions for a schedule\n"
        "  /dca tick            Execute any due schedules (cron-driven)\n"
        "\n"
        "Example:\n"
        "  /dca add 0xa1F7…747be 0.001 1h   buy $3.50 of CLAWNCH every hour"
    )


# ── /dca add ────────────────────────────────────────────────────────


def _cmd_add(sender_id: str, parts: list[str]) -> str:
    if len(parts) < 3:
        return (
            "Usage: /dca add <token> <eth_amount> <interval>\n"
            "Example: /dca add 0xa1F7…747be 0.001 1h"
        )
    token, eth_amount_raw, interval_raw = parts[0], parts[1], parts[2]

    # Validate token: must be a 0x address. We don't resolve symbols here
    # because the DCA loop runs without interactive context, and we want
    # the user to pin the exact contract.
    if not (token.startswith("0x") and len(token) == 42):
        return f"Token must be a 0x… address (got {token!r})."
    try:
        eth_amount = float(eth_amount_raw)
    except ValueError:
        return f"eth_amount must be a number (got {eth_amount_raw!r})."
    if eth_amount <= 0:
        return f"eth_amount must be positive (got {eth_amount})."

    seconds = _parse_interval(interval_raw)
    if seconds is None:
        return (
            f"Could not parse interval {interval_raw!r}. "
            "Try 5m / 1h / 4h / 1d / 1w (1m floor, 1y ceiling)."
        )

    state = _load_state()
    sched_id = _new_id()
    now = _now_epoch()
    schedule = {
        "id": sched_id,
        "sender_id": sender_id,
        "token": token.lower(),
        "eth_amount": eth_amount,
        "interval_seconds": seconds,
        "next_run_epoch": now + seconds,
        "status": "active",
        "created_at": _now_iso(),
        "executions": [],
    }
    state["schedules"].append(schedule)
    _save_state(state)

    return (
        f"Schedule added: {sched_id}\n"
        f"  Token:    {token}\n"
        f"  Amount:   {eth_amount} ETH per buy\n"
        f"  Interval: {_format_interval(seconds)}\n"
        f"  Next:     ~{_format_interval(seconds)} from now\n"
        "\n"
        "Tick runs via /dca tick (call manually) or via Hermes cron when\n"
        "configured. Use /dca list to see all schedules."
    )


# ── /dca list ───────────────────────────────────────────────────────


def _cmd_list(sender_id: str) -> str:
    state = _load_state()
    mine = [s for s in state["schedules"] if s.get("sender_id") == sender_id]
    if not mine:
        return "No DCA schedules. Add one with /dca add <token> <amount> <interval>."

    now = _now_epoch()
    lines = [f"DCA schedules for {sender_id} ({len(mine)}):", ""]
    for sched in mine:
        delta = sched.get("next_run_epoch", 0) - now
        when = (
            f"in {_format_relative(delta)}" if delta > 0 else f"{_format_relative(-delta)} overdue"
        )
        run_count = len(sched.get("executions", []))
        lines.append(
            f"  {sched['id']}  {sched.get('status'):<8s}"
            f"  {sched['eth_amount']} ETH × {_format_interval(sched['interval_seconds'])}"
            f"  → {_short(sched['token'])}"
            f"  ({run_count} runs, next {when})"
        )
    return "\n".join(lines)


# ── /dca pause / resume / cancel ────────────────────────────────────


def _cmd_pause(sender_id: str, parts: list[str]) -> str:
    return _mutate(sender_id, parts, status="paused", verb="paused")


def _cmd_resume(sender_id: str, parts: list[str]) -> str:
    return _mutate(sender_id, parts, status="active", verb="resumed")


def _cmd_cancel(sender_id: str, parts: list[str]) -> str:
    if not parts:
        return "Usage: /dca cancel <id>"
    sched_id = parts[0]
    state = _load_state()
    before = len(state["schedules"])
    state["schedules"] = [
        s
        for s in state["schedules"]
        if not (s.get("id") == sched_id and s.get("sender_id") == sender_id)
    ]
    if len(state["schedules"]) == before:
        return f"No schedule found with id {sched_id!r}."
    _save_state(state)
    return f"Cancelled schedule {sched_id}."


def _mutate(sender_id: str, parts: list[str], *, status: str, verb: str) -> str:
    if not parts:
        return f"Usage: /dca {verb.removesuffix('d')} <id>"
    sched_id = parts[0]
    state = _load_state()
    found = False
    for sched in state["schedules"]:
        if sched.get("id") == sched_id and sched.get("sender_id") == sender_id:
            sched["status"] = status
            found = True
            break
    if not found:
        return f"No schedule found with id {sched_id!r}."
    _save_state(state)
    return f"Schedule {sched_id} {verb}."


# ── /dca tick ───────────────────────────────────────────────────────


async def _cmd_tick() -> str:
    """Execute any active schedule whose ``next_run_epoch`` has passed.

    Cron-safe: if nothing is due, returns immediately with no side
    effects. If a tx submission fails (no wallet, RPC error, slippage,
    etc.), the failure is recorded and ``next_run_epoch`` still
    advances — so transient failures don't cascade into a flood of
    retries.
    """
    state = _load_state()
    now = _now_epoch()
    due = [
        s
        for s in state["schedules"]
        if s.get("status") == "active" and s.get("next_run_epoch", 0) <= now
    ]
    if not due:
        return "No DCA schedules due."

    lines = [f"Executing {len(due)} due schedule(s)..."]
    for sched in due:
        result = await _execute(sched)
        sched["executions"].append(
            {"at": _now_iso(), "result": result, "tx_hash": result.get("tx_hash", "")}
        )
        sched["next_run_epoch"] = now + sched["interval_seconds"]
        lines.append(f"  {sched['id']}  {result.get('status')}  {result.get('detail', '')}")
    _save_state(state)
    return "\n".join(lines)


async def _execute(sched: dict[str, Any]) -> dict[str, Any]:
    """Submit one swap for ``sched``. Returns a result dict for history."""
    from clawmes.services.wallet import get_wallet_state

    state = get_wallet_state()
    if not state.connected:
        return {"status": "no_wallet", "detail": "no wallet connected", "tx_hash": ""}

    try:
        from clawmes.tools.defi_swap import defi_swap

        raw = defi_swap(
            {
                "action": "swap",
                "sell_token": "ETH",
                "buy_token": sched["token"],
                "sell_amount": str(sched["eth_amount"]),
            }
        )
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "detail": str(exc), "tx_hash": ""}

    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return {"status": "error", "detail": f"bad swap response: {raw}", "tx_hash": ""}
    if payload.get("isError"):
        msg = payload.get("content", [{}])[0].get("text", "swap failed")
        return {"status": "error", "detail": msg, "tx_hash": ""}

    details = payload.get("details") or {}
    tx_hash = details.get("tx_hash") or details.get("txHash") or ""
    return {"status": "ok", "detail": f"tx {tx_hash[:14]}…", "tx_hash": tx_hash}


# ── /dca history ────────────────────────────────────────────────────


def _cmd_history(sender_id: str, parts: list[str]) -> str:
    if not parts:
        return "Usage: /dca history <id>"
    sched_id = parts[0]
    state = _load_state()
    sched = next(
        (
            s
            for s in state["schedules"]
            if s.get("id") == sched_id and s.get("sender_id") == sender_id
        ),
        None,
    )
    if not sched:
        return f"No schedule found with id {sched_id!r}."
    runs = sched.get("executions", [])
    if not runs:
        return f"Schedule {sched_id}: no executions yet."
    lines = [f"Executions for {sched_id} ({len(runs)}):", ""]
    for run in runs:
        result = run.get("result") or {}
        status = result.get("status", "?")
        tx_hash = run.get("tx_hash", "")
        when = run.get("at", "")
        suffix = f"  tx {tx_hash[:14]}…" if tx_hash else ""
        lines.append(f"  {when}  {status}{suffix}")
    return "\n".join(lines)


# ── small helpers ───────────────────────────────────────────────────


def _short(addr: str) -> str:
    if not isinstance(addr, str) or len(addr) <= 12:
        return str(addr)
    return f"{addr[:6]}…{addr[-4:]}"


def _format_relative(seconds: int) -> str:
    """Format a positive duration in seconds as a short human string."""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


def register(ctx) -> None:
    ctx.register_command(
        name="dca",
        handler=handle_dca,
        description="Schedule recurring ETH-funded buys (dollar-cost average)",
        args_hint="add <token> <eth> <interval> | list | pause | resume | cancel | tick | history",
    )
