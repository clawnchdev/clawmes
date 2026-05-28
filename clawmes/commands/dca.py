"""``/dca`` — dollar-cost-average recurring buys.

Recurring ETH-funded buys against a target token. Schedules live in
``${HERMES_HOME}/clawmes/dca/schedules.json`` so they survive process
restarts. Execution happens automatically — the
``clawmes.services.dca_scheduler.DcaSchedulerService`` ticks on the
registry cadence (60s by default, configurable via Hermes cron) and
dispatches due schedules. The manual ``/dca tick`` subcommand is
preserved for testing + edge-case use.

Surface:

  * ``/dca add <token> <eth_amount> <interval> [--slippage bps]
    [--daily-cap eth] [--max-total eth] [--max-failures n]``
                                              schedule a recurring buy
  * ``/dca list``                              show all your schedules
  * ``/dca pause <id>``                        suspend without losing state
  * ``/dca resume <id>``                       re-arm
  * ``/dca cancel <id>``                       remove entirely
  * ``/dca edit <id> <field> <value>``         change one field
  * ``/dca skip <id>``                         advance next_run without running
  * ``/dca dry-run <id>``                      preview swap output, no submit
  * ``/dca tick``                              manually execute any due
  * ``/dca status``                            global summary (all senders)
  * ``/dca history <id>``                      past executions

Interval grammar: ``1m``, ``30m``, ``1h``, ``4h``, ``1d``, ``1w``
(1m floor, 1y ceiling).

Safeguards (all optional; default to permissive):
  * ``slippage_bps`` — passed to ``defi_swap`` (default 100 = 1%).
  * ``daily_cap_eth`` — auto-skip if executing would exceed N ETH spent
    in the past 24h. Default: unlimited.
  * ``max_eth_total`` — auto-pause when lifetime spend hits this cap.
    Default: unlimited.
  * ``max_consecutive_failures`` — auto-pause after N failed runs in
    a row. Default: 3.

Each tick:
  * Walks all active schedules whose ``next_run_epoch <= now``.
  * For each due schedule, checks safeguards before submitting; if a
    cap or failure-streak fires, the schedule is paused and the result
    recorded.
  * Otherwise calls ``defi_swap`` with the configured ``token`` +
    ``eth_amount`` + ``slippage_bps``.
  * Records the tx hash (or error) on the schedule's history.
  * Advances ``next_run_epoch`` by ``interval_seconds`` regardless of
    outcome (so transient errors don't cascade into retry storms).

Wallet behavior: uses the active wallet at tick time. If no wallet is
connected when a tick fires, the schedule's execution is logged as
``no_wallet`` and ``next_run_epoch`` still advances.
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
        elif sub == "edit":
            out = _cmd_edit(sender_id, rest)
        elif sub == "skip":
            out = _cmd_skip(sender_id, rest)
        elif sub in ("dry-run", "dryrun"):
            out = _cmd_dry_run(sender_id, rest)
        elif sub == "status":
            out = _cmd_status(sender_id)
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
        "      [--slippage <bps>] [--daily-cap <eth>]\n"
        "      [--max-total <eth>] [--max-failures <n>]\n"
        "                          Schedule a recurring buy. Interval grammar:\n"
        "                          1m, 30m, 1h, 4h, 1d, 1w (1m floor, 1y ceiling)\n"
        "  /dca list               Show your schedules + next run time\n"
        "  /dca pause <id>         Suspend a schedule without removing it\n"
        "  /dca resume <id>        Re-arm a paused schedule\n"
        "  /dca cancel <id>        Remove a schedule entirely\n"
        "  /dca edit <id> <field> <value>\n"
        "                          Change one field on a schedule\n"
        "  /dca skip <id>          Advance next run, no execution this round\n"
        "  /dca dry-run <id>       Preview swap output, no submission\n"
        "  /dca status             Global summary + service health\n"
        "  /dca history <id>       Past executions for a schedule\n"
        "  /dca tick               Manually execute due schedules\n"
        "\n"
        "Example:\n"
        "  /dca add 0xa1F7…747be 0.001 1h --slippage 50 --daily-cap 0.05\n"
        "    buy 0.001 ETH of CLAWNCH every hour, 0.5% slippage,\n"
        "    skip if 24h spend would exceed 0.05 ETH"
    )


# ── /dca add ────────────────────────────────────────────────────────


_DEFAULT_SLIPPAGE_BPS = 100  # 1% — generous default; tighten per-schedule
_DEFAULT_MAX_FAILURES = 3  # auto-pause after this many failures in a row


def _split_flags(parts: list[str]) -> tuple[list[str], dict[str, str]]:
    """Split a token stream into positional args and ``--flag value`` pairs.

    Permissive: unknown flags pass through to caller. Each ``--flag``
    consumes the next token as its value; bare flags (no following
    value) attach an empty string and the caller decides.
    """
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


def _cmd_add(sender_id: str, parts: list[str]) -> str:
    pos, flags = _split_flags(parts)
    if len(pos) < 3:
        return (
            "Usage: /dca add <token> <eth_amount> <interval>\n"
            "  [--slippage <bps>] [--daily-cap <eth>]\n"
            "  [--max-total <eth>] [--max-failures <n>]\n"
            "Example: /dca add 0xa1F7…747be 0.001 1h --slippage 50 --daily-cap 0.05"
        )

    # Free-tier cap: limit active schedules per sender. Holders bypass.
    from clawmes.services.token_gate import Tier, check_cap_or_error, check_tier_or_error

    state_check = _load_state()
    active_mine = sum(
        1
        for s in state_check["schedules"]
        if s.get("sender_id") == sender_id and s.get("status") == "active"
    )
    cap_err = check_cap_or_error("dca", active_count=active_mine, feature="DCA schedule")
    if cap_err:
        return cap_err

    # Safeguard flags are a HOLDER-tier feature. The gate runs only when
    # the user actually passed a safeguard flag — free-tier add without
    # flags is fine.
    safeguard_flags = {"slippage", "daily-cap", "max-total", "max-failures"}
    used_safeguards = safeguard_flags & set(flags)
    if used_safeguards:
        tier_err = check_tier_or_error(
            Tier.HOLDER,
            feature=f"/dca safeguard flags ({', '.join(sorted(used_safeguards))})",
        )
        if tier_err:
            return tier_err

    token, eth_amount_raw, interval_raw = pos[0], pos[1], pos[2]

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

    # Safeguard flag parsing — each is independently optional and
    # validated; an invalid value rejects the whole add so the user
    # doesn't end up with a half-configured schedule.
    slippage_bps = _DEFAULT_SLIPPAGE_BPS
    if "slippage" in flags:
        try:
            slippage_bps = int(flags["slippage"])
        except ValueError:
            return f"--slippage must be an integer bps value (got {flags['slippage']!r})."
        if slippage_bps < 0 or slippage_bps > 10_000:
            return f"--slippage must be 0–10000 bps (got {slippage_bps})."

    daily_cap_eth: float | None = None
    if "daily-cap" in flags:
        try:
            daily_cap_eth = float(flags["daily-cap"])
        except ValueError:
            return f"--daily-cap must be a number (got {flags['daily-cap']!r})."
        if daily_cap_eth <= 0:
            return f"--daily-cap must be positive (got {daily_cap_eth})."

    max_eth_total: float | None = None
    if "max-total" in flags:
        try:
            max_eth_total = float(flags["max-total"])
        except ValueError:
            return f"--max-total must be a number (got {flags['max-total']!r})."
        if max_eth_total <= 0:
            return f"--max-total must be positive (got {max_eth_total})."

    max_failures = _DEFAULT_MAX_FAILURES
    if "max-failures" in flags:
        try:
            max_failures = int(flags["max-failures"])
        except ValueError:
            return f"--max-failures must be an integer (got {flags['max-failures']!r})."
        if max_failures < 1:
            return f"--max-failures must be >= 1 (got {max_failures})."

    # Conditional execution: only fire if ``conditional`` evaluates to True.
    # UNLIMITED-tier feature. Grammar in :func:`_parse_conditional`.
    conditional: dict[str, Any] | None = None
    if "conditional" in flags:
        from clawmes.services.token_gate import Tier, check_tier_or_error

        gate_err = check_tier_or_error(Tier.UNLIMITED, feature="/dca --conditional gates")
        if gate_err:
            return gate_err
        parsed, err = _parse_conditional(flags["conditional"])
        if err:
            return f"--conditional parse error: {err}"
        conditional = parsed

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
        # Safeguards. ``None`` here means "no cap"; the executor reads
        # these via ``.get(key)`` so older schedules without them work.
        "slippage_bps": slippage_bps,
        "daily_cap_eth": daily_cap_eth,
        "max_eth_total": max_eth_total,
        "max_consecutive_failures": max_failures,
        "total_eth_spent": 0.0,
        "conditional": conditional,
    }
    state["schedules"].append(schedule)
    _save_state(state)

    conditional_line = (
        f"  Conditional: {_describe_conditional(conditional)}\n" if conditional else ""
    )
    return (
        f"Schedule added: {sched_id}\n"
        f"  Token:       {token}\n"
        f"  Amount:      {eth_amount} ETH per buy\n"
        f"  Interval:    {_format_interval(seconds)}\n"
        f"  Slippage:    {slippage_bps} bps\n"
        f"  Daily cap:   {daily_cap_eth if daily_cap_eth is not None else 'none'}\n"
        f"  Total cap:   {max_eth_total if max_eth_total is not None else 'none'}\n"
        f"  Max fails:   {max_failures}\n"
        + conditional_line
        + f"  Next run:    ~{_format_interval(seconds)} from now\n"
        "\n"
        "The DCA scheduler service ticks automatically (every ~60s).\n"
        "Use /dca list to see all schedules, or /dca dry-run <id> to\n"
        "preview the swap without submitting."
    )


def _parse_conditional(raw: str) -> tuple[dict[str, Any] | None, str | None]:
    """Parse a conditional expression. Returns ``(parsed, error_msg)``.

    Grammar:

      ``price_above:<token>:<usd>``  — fire only when ``price(<token>) > <usd>``
      ``price_below:<token>:<usd>``  — fire only when ``price(<token>) < <usd>``

    Future grammar (deferred): time windows, AND/OR composition,
    on-chain conditions (wallet balance thresholds, block height, etc).
    """
    parts = raw.split(":")
    if len(parts) != 3:
        return None, (
            f"expected 'price_above:<token>:<usd>' or 'price_below:<token>:<usd>' (got {raw!r})"
        )
    op, token, usd_raw = parts[0], parts[1], parts[2]
    if op not in ("price_above", "price_below"):
        return None, f"unknown operator {op!r} (use price_above or price_below)"
    try:
        threshold_usd = float(usd_raw)
    except ValueError:
        return None, f"usd threshold must be a number (got {usd_raw!r})"
    if threshold_usd <= 0:
        return None, f"usd threshold must be positive (got {threshold_usd})"
    return {"op": op, "token": token, "threshold_usd": threshold_usd}, None


def _describe_conditional(cond: dict[str, Any]) -> str:
    op = cond.get("op", "")
    symbol = "above" if op == "price_above" else "below"
    return f"price({cond.get('token', '?')}) {symbol} ${cond.get('threshold_usd', '?')}"


def _conditional_satisfied(cond: dict[str, Any]) -> bool:
    """Evaluate a conditional. Returns ``False`` if the price read fails."""
    from clawmes.tools.defi_price import defi_price

    try:
        raw = defi_price({"action": "quote", "symbol": cond["token"], "quote_currency": "USD"})
    except Exception:  # noqa: BLE001
        return False
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return False
    if payload.get("isError"):
        return False
    details = payload.get("details") or {}
    price = details.get("price_usd") or details.get("price")
    try:
        price = float(price)
    except (TypeError, ValueError):
        return False
    threshold = float(cond["threshold_usd"])
    if cond["op"] == "price_above":
        return price > threshold
    if cond["op"] == "price_below":
        return price < threshold
    return False


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


# ── /dca edit / skip / dry-run / status ────────────────────────────


_EDITABLE_FIELDS = {
    "token",
    "eth_amount",
    "interval",
    "slippage_bps",
    "daily_cap_eth",
    "max_eth_total",
    "max_consecutive_failures",
}


def _cmd_edit(sender_id: str, parts: list[str]) -> str:
    """``/dca edit <id> <field> <value>`` — change one schedule property in place."""
    if len(parts) < 3:
        return (
            f"Usage: /dca edit <id> <field> <value>\nFields: {', '.join(sorted(_EDITABLE_FIELDS))}"
        )
    sched_id, field, value = parts[0], parts[1], parts[2]
    if field not in _EDITABLE_FIELDS:
        return f"Unknown field {field!r}. Editable: {', '.join(sorted(_EDITABLE_FIELDS))}"

    state = _load_state()
    sched = _find_sched(state, sched_id, sender_id)
    if sched is None:
        return f"No schedule found with id {sched_id!r}."

    # Per-field validation, mirroring _cmd_add's rules.
    if field == "token":
        if not (value.startswith("0x") and len(value) == 42):
            return f"token must be a 0x… address (got {value!r})."
        sched["token"] = value.lower()
    elif field == "eth_amount":
        try:
            v = float(value)
        except ValueError:
            return f"eth_amount must be a number (got {value!r})."
        if v <= 0:
            return f"eth_amount must be positive (got {v})."
        sched["eth_amount"] = v
    elif field == "interval":
        secs = _parse_interval(value)
        if secs is None:
            return f"Could not parse interval {value!r} (1m floor, 1y ceiling)."
        sched["interval_seconds"] = secs
    elif field == "slippage_bps":
        try:
            v = int(value)
        except ValueError:
            return f"slippage_bps must be an integer (got {value!r})."
        if v < 0 or v > 10_000:
            return f"slippage_bps must be 0–10000 (got {v})."
        sched["slippage_bps"] = v
    elif field == "daily_cap_eth":
        if value.lower() == "none":
            sched["daily_cap_eth"] = None
        else:
            try:
                v = float(value)
            except ValueError:
                return f"daily_cap_eth must be a number or 'none' (got {value!r})."
            if v <= 0:
                return f"daily_cap_eth must be positive (got {v})."
            sched["daily_cap_eth"] = v
    elif field == "max_eth_total":
        if value.lower() == "none":
            sched["max_eth_total"] = None
        else:
            try:
                v = float(value)
            except ValueError:
                return f"max_eth_total must be a number or 'none' (got {value!r})."
            if v <= 0:
                return f"max_eth_total must be positive (got {v})."
            sched["max_eth_total"] = v
    else:  # max_consecutive_failures — last remaining field
        try:
            v = int(value)
        except ValueError:
            return f"max_consecutive_failures must be an integer (got {value!r})."
        if v < 1:
            return f"max_consecutive_failures must be >= 1 (got {v})."
        sched["max_consecutive_failures"] = v

    _save_state(state)
    return f"Schedule {sched_id}: {field} = {value}."


def _cmd_skip(sender_id: str, parts: list[str]) -> str:
    """``/dca skip <id>`` — bump next_run_epoch by interval without running."""
    if not parts:
        return "Usage: /dca skip <id>"
    sched_id = parts[0]
    state = _load_state()
    sched = _find_sched(state, sched_id, sender_id)
    if sched is None:
        return f"No schedule found with id {sched_id!r}."
    sched["next_run_epoch"] = _now_epoch() + sched["interval_seconds"]
    _save_state(state)
    return (
        f"Skipped next run for {sched_id}. "
        f"Next attempt in {_format_interval(sched['interval_seconds'])}."
    )


def _cmd_dry_run(sender_id: str, parts: list[str]) -> str:
    """``/dca dry-run <id>`` — quote the swap without submitting."""
    if not parts:
        return "Usage: /dca dry-run <id>"
    sched_id = parts[0]
    state = _load_state()
    sched = _find_sched(state, sched_id, sender_id)
    if sched is None:
        return f"No schedule found with id {sched_id!r}."

    try:
        from clawmes.tools.defi_swap import defi_swap

        raw = defi_swap(
            {
                "action": "quote",
                "sell_token": "ETH",
                "buy_token": sched["token"],
                "sell_amount": str(sched["eth_amount"]),
                "slippage_bps": int(sched.get("slippage_bps") or _DEFAULT_SLIPPAGE_BPS),
            }
        )
    except Exception as exc:  # noqa: BLE001
        return f"Dry-run failed: {exc}"

    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return f"Dry-run failed (bad swap response): {raw}"
    if payload.get("isError"):
        msg = payload.get("content", [{}])[0].get("text", "quote failed")
        return f"Dry-run quote failed: {msg}"

    details = payload.get("details") or {}
    buy_amount = details.get("buy_amount") or details.get("buyAmount") or details.get("amount_out")
    return (
        f"Dry-run for {sched_id}:\n"
        f"  Sell: {sched['eth_amount']} ETH\n"
        f"  Buy:  {buy_amount} {_short(sched['token'])}\n"
        f"  Slippage: {sched.get('slippage_bps', _DEFAULT_SLIPPAGE_BPS)} bps\n"
        "  (No transaction submitted.)"
    )


def _cmd_status(_sender_id: str) -> str:
    """``/dca status`` — global summary across all senders.

    Sender-scoped UX everywhere else uses ``sender_id`` to filter; this
    command is intentionally global so operators can see whether the
    scheduler is healthy across the whole install.
    """
    state = _load_state()
    schedules = state.get("schedules", [])
    if not schedules:
        return "No DCA schedules exist. The scheduler service is idle."

    by_status: dict[str, int] = {}
    total_spent = 0.0
    total_runs = 0
    failures = 0
    for s in schedules:
        by_status[s.get("status", "?")] = by_status.get(s.get("status", "?"), 0) + 1
        total_spent += float(s.get("total_eth_spent", 0.0))
        runs = s.get("executions", [])
        total_runs += len(runs)
        for r in runs:
            status = (r.get("result") or {}).get("status", "")
            if status in ("error", "no_wallet", "daily_capped", "total_capped"):
                failures += 1

    lines = [
        f"DCA scheduler status ({len(schedules)} schedule(s)):",
        "",
        f"  By status:    {', '.join(f'{k}={v}' for k, v in sorted(by_status.items()))}",
        f"  Total runs:   {total_runs}",
        f"  Failures:     {failures}",
        f"  ETH spent:    {total_spent:.6f}",
    ]

    # Health snapshot of the cron-driver service.
    try:
        from clawmes.services.dca_scheduler import get_dca_scheduler_service

        svc = get_dca_scheduler_service()
        h = svc.health()
        lines.append(
            f"  Service:      {h.get('status')} (ticks={h.get('ticks')}, "
            f"total_fired={h.get('total_runs')})"
        )
    except Exception:  # noqa: BLE001 — service may not be started in tests
        pass

    return "\n".join(lines)


def _find_sched(state: dict[str, Any], sched_id: str, sender_id: str) -> dict[str, Any] | None:
    """Return the matching schedule or ``None`` (used by edit/skip/dry-run)."""
    for s in state["schedules"]:
        if s.get("id") == sched_id and s.get("sender_id") == sender_id:
            return s
    return None


# ── /dca tick ───────────────────────────────────────────────────────


async def _cmd_tick() -> str:
    """Manual ``/dca tick`` entrypoint. Delegates to the sync runner."""
    count, lines = _run_due_with_lines()
    if count == 0:
        return "No DCA schedules due."
    return "\n".join([f"Executing {count} due schedule(s)..."] + lines)


def _run_due_sync() -> int:
    """Service entrypoint — execute all due schedules. Returns count fired."""
    count, _ = _run_due_with_lines()
    return count


def _run_due_with_lines() -> tuple[int, list[str]]:
    """Shared core: walk due schedules, execute, return ``(count, lines)``."""
    state = _load_state()
    now = _now_epoch()
    due = [
        s
        for s in state["schedules"]
        if s.get("status") == "active" and s.get("next_run_epoch", 0) <= now
    ]
    if not due:
        return 0, []

    lines: list[str] = []
    for sched in due:
        # If the schedule has a conditional, evaluate it FIRST. A
        # blocking conditional advances next_run_epoch but skips
        # execution (the schedule keeps its cadence so the next
        # interval re-checks).
        conditional = sched.get("conditional")
        if conditional and not _conditional_satisfied(conditional):
            sched["executions"].append(
                {
                    "at": _now_iso(),
                    "result": {
                        "status": "conditional_blocked",
                        "detail": _describe_conditional(conditional),
                    },
                    "tx_hash": "",
                }
            )
            sched["next_run_epoch"] = now + sched["interval_seconds"]
            lines.append(
                f"  {sched['id']}  conditional_blocked  {_describe_conditional(conditional)}"
            )
            continue

        result = _execute_sync(sched)
        sched["executions"].append(
            {"at": _now_iso(), "result": result, "tx_hash": result.get("tx_hash", "")}
        )
        sched["next_run_epoch"] = now + sched["interval_seconds"]

        # If the result was an actual swap (status == "ok"), accumulate
        # spend for the lifetime + daily caps. Skipped/errored runs don't
        # contribute to the spend total.
        if result.get("status") == "ok":
            sched["total_eth_spent"] = float(sched.get("total_eth_spent", 0.0)) + float(
                sched["eth_amount"]
            )

        # Auto-pause on consecutive failure streak.
        _maybe_auto_pause(sched)

        lines.append(f"  {sched['id']}  {result.get('status')}  {result.get('detail', '')}")
    _save_state(state)
    return len(due), lines


def _maybe_auto_pause(sched: dict[str, Any]) -> None:
    """Pause the schedule if its tail of executions has ``max`` failures in a row."""
    max_fails = int(sched.get("max_consecutive_failures") or _DEFAULT_MAX_FAILURES)
    runs = sched.get("executions", [])
    if len(runs) < max_fails:
        return
    tail = runs[-max_fails:]
    failure_states = {"error", "no_wallet", "daily_capped", "total_capped"}
    if all((r.get("result") or {}).get("status") in failure_states for r in tail):
        sched["status"] = "paused"


def _spend_in_last_24h(sched: dict[str, Any]) -> float:
    """Sum eth_amount across successful executions in the past 24h."""
    runs = sched.get("executions", [])
    cutoff = _now_epoch() - 86400
    total = 0.0
    eth_amount = float(sched.get("eth_amount", 0.0))
    for run in runs:
        if (run.get("result") or {}).get("status") != "ok":
            continue
        # Parse ISO timestamp back to epoch — best-effort; if it fails
        # we skip the run (defensive against malformed history files).
        at = run.get("at", "")
        try:
            dt = datetime.strptime(at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
            ts = int(dt.timestamp())
        except (ValueError, TypeError):
            continue
        if ts >= cutoff:
            total += eth_amount
    return total


def _execute_sync(sched: dict[str, Any]) -> dict[str, Any]:
    """Submit one swap for ``sched``. Synchronous so the service tick can call it.

    Safeguard order matters: lifetime cap > daily cap > wallet > swap.
    A schedule that's hit its lifetime cap should never even probe the
    wallet (in case the wallet's gone offline — we'd surface the wrong
    failure type).
    """
    eth_amount = float(sched.get("eth_amount", 0.0))

    # Lifetime cap.
    max_total = sched.get("max_eth_total")
    if max_total is not None:
        spent = float(sched.get("total_eth_spent", 0.0))
        if spent + eth_amount > float(max_total):
            return {
                "status": "total_capped",
                "detail": f"would exceed max-total {max_total} ETH (spent {spent})",
                "tx_hash": "",
            }

    # Daily cap.
    daily_cap = sched.get("daily_cap_eth")
    if daily_cap is not None:
        spent_24h = _spend_in_last_24h(sched)
        if spent_24h + eth_amount > float(daily_cap):
            return {
                "status": "daily_capped",
                "detail": f"would exceed daily-cap {daily_cap} ETH (spent {spent_24h:.6f} in 24h)",
                "tx_hash": "",
            }

    # Wallet must be live.
    from clawmes.services.wallet import get_wallet_state

    wstate = get_wallet_state()
    if not wstate.connected:
        return {"status": "no_wallet", "detail": "no wallet connected", "tx_hash": ""}

    # Submit the swap.
    try:
        from clawmes.tools.defi_swap import defi_swap

        raw = defi_swap(
            {
                "action": "swap",
                "sell_token": "ETH",
                "buy_token": sched["token"],
                "sell_amount": str(eth_amount),
                "slippage_bps": int(sched.get("slippage_bps") or _DEFAULT_SLIPPAGE_BPS),
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
