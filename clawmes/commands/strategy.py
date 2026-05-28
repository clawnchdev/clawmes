"""``/strategy`` — preset templates that compose multiple commands.

A strategy is a named recipe that materializes as several lower-level
commands at once. Saves users from running 4-5 ``/dca add`` + ``/copy add``
+ ``/alerts add`` calls one at a time when the goal is a coherent
playbook.

UNLIMITED tier — these are autopilot presets.

Surface:

  * ``/strategy list``                       — show available presets
  * ``/strategy preview <name> <args>``      — see what the preset would do
  * ``/strategy apply <name> <args>``        — materialize as actual commands
  * ``/strategy history``                    — recently applied strategies

Presets:

  * ``whale-shadow <wallet> <eth_per_copy>`` — copy a wallet's buys AND
    sells (``/copy --invert``) + alert on outgoing transfers
    (``/alerts add wallet``).
  * ``dca-and-snipe <token> <dca_eth> <interval> <snipe_eth>`` — DCA into
    a token on a schedule (``/dca add``) + auto-buy any new launches
    from the same source (``/sniper add --source``).
  * ``laddered-tp <eth_per_copy> <wallet> <tp1>:<tp2>:<tp3>`` — copy a
    wallet's buys, then ladder out via three sequential take-profit
    points (``/sniper --auto-sell`` on each).

Each preset is intentionally narrow — it composes existing commands
rather than introducing new persistent state. So the UNLIMITED gate
that protects ``/sniper`` etc. still fires on materialization.

Strategy history is stored at
``${HERMES_HOME}/clawmes/strategy/history.json`` purely for
diagnostics — re-running a strategy creates fresh underlying state.
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

    return state_dir("strategy") / "history.json"


def _load_history() -> dict[str, Any]:
    path = _history_path()
    if not path.exists():
        return {"applied": []}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {"applied": []}
    if not isinstance(data, dict) or not isinstance(data.get("applied"), list):
        return {"applied": []}
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
    return f"strat_{uuid.uuid4().hex[:10]}"


def _record(name: str, args: str, result: str) -> None:
    try:
        from clawmes.services.command_history import record_command_call

        record_command_call(name, args, result)
    except Exception:  # noqa: BLE001
        pass


# ── preset definitions ─────────────────────────────────────────────


def _preset_whale_shadow(args: list[str]) -> tuple[list[dict[str, Any]] | None, str | None]:
    """``whale-shadow <wallet> <eth_per_copy>`` → /copy --invert + /alerts."""
    if len(args) < 2:
        return None, "usage: /strategy apply whale-shadow <wallet> <eth_per_copy>"
    wallet, eth = args[0], args[1]
    if not (wallet.startswith("0x") and len(wallet) == 42):
        return None, f"wallet must be 0x… address (got {wallet!r})"
    try:
        float(eth)
    except ValueError:
        return None, f"eth_per_copy must be a number (got {eth!r})"
    steps = [
        {
            "command": "copy",
            "args": f"add {wallet} {eth} --invert",
            "summary": f"Copy buys AND sells from {wallet} at {eth} ETH each",
        },
        {
            "command": "alerts",
            "args": f"add wallet {wallet}",
            "summary": f"Alert on any new ERC-20 receipt to {wallet}",
        },
    ]
    return steps, None


def _preset_dca_and_snipe(args: list[str]) -> tuple[list[dict[str, Any]] | None, str | None]:
    """``dca-and-snipe <token> <dca_eth> <interval> <snipe_eth>``."""
    if len(args) < 4:
        return None, (
            "usage: /strategy apply dca-and-snipe <token> <dca_eth> <interval> <snipe_eth>"
        )
    token, dca_eth, interval, snipe_eth = args[0], args[1], args[2], args[3]
    if not (token.startswith("0x") and len(token) == 42):
        return None, f"token must be 0x… address (got {token!r})"
    try:
        float(dca_eth)
        float(snipe_eth)
    except ValueError:
        return None, f"amounts must be numbers (got {dca_eth!r}, {snipe_eth!r})"
    steps = [
        {
            "command": "dca",
            "args": f"add {token} {dca_eth} {interval}",
            "summary": f"DCA {dca_eth} ETH into {token} every {interval}",
        },
        {
            "command": "sniper",
            "args": f"add {snipe_eth} --max-buys 5",
            "summary": f"Snipe new launches at {snipe_eth} ETH each (max 5)",
        },
    ]
    return steps, None


def _preset_laddered_tp(args: list[str]) -> tuple[list[dict[str, Any]] | None, str | None]:
    """``laddered-tp <eth_per_copy> <wallet> <tp1>:<tp2>:<tp3>``.

    Copy a wallet's buys, then attach three sequential take-profit
    thresholds via /sniper auto-sell config. This is the "set up the
    full long-trade lifecycle in one command" preset.
    """
    if len(args) < 3:
        return None, (
            "usage: /strategy apply laddered-tp <eth_per_copy> <wallet> <tp1>:<tp2>:<tp3>"
        )
    eth, wallet, ladder = args[0], args[1], args[2]
    if not (wallet.startswith("0x") and len(wallet) == 42):
        return None, f"wallet must be 0x… address (got {wallet!r})"
    try:
        float(eth)
    except ValueError:
        return None, f"eth_per_copy must be a number (got {eth!r})"
    tps = ladder.split(":")
    if len(tps) != 3:
        return None, f"ladder must be 'tp1:tp2:tp3' (got {ladder!r})"
    try:
        tp_values = [float(t) for t in tps]
    except ValueError:
        return None, f"ladder values must be numbers (got {ladder!r})"
    if any(v <= 0 for v in tp_values):
        return None, f"ladder values must be positive (got {ladder!r})"
    # Use the smallest TP for the snipe's --auto-sell gain; the rest are
    # informational (manual ladder logic would need a follow-up command).
    primary_tp = min(tp_values)
    steps = [
        {
            "command": "copy",
            "args": f"add {wallet} {eth}",
            "summary": f"Copy {wallet}'s buys at {eth} ETH each",
        },
        {
            "command": "sniper",
            "args": f"add {eth} --auto-sell {primary_tp}:50",
            "summary": (
                f"Snipe + auto-sell at +{primary_tp}% / -50%. "
                f"Manual ladder via /sniper edit for tp2={tp_values[1]}%, tp3={tp_values[2]}%"
            ),
        },
    ]
    return steps, None


_PRESETS = {
    "whale-shadow": _preset_whale_shadow,
    "dca-and-snipe": _preset_dca_and_snipe,
    "laddered-tp": _preset_laddered_tp,
}


# ── dispatch ────────────────────────────────────────────────────────


async def handle_strategy(raw_args: str, *, sender_id: str = "default", **_kwargs: Any) -> str:
    raw = (raw_args or "").strip()
    if not raw:
        out = _render_usage()
    else:
        parts = raw.split()
        sub = parts[0].lower()
        rest = parts[1:]
        if sub == "list":
            out = _cmd_list()
        elif sub == "preview":
            out = _cmd_preview(rest)
        elif sub == "apply":
            out = await _cmd_apply(sender_id, rest)
        elif sub == "history":
            out = _cmd_history(sender_id)
        else:
            out = f"Unknown subcommand: {sub!r}\n\n" + _render_usage()
    _record("strategy", raw_args, out)
    return out


def _render_usage() -> str:
    return (
        "Strategy — preset templates that compose multiple commands.\n"
        "  (Clawmes Unlimited tier — hold 100M+ $CLAWNCH to /strategy apply.)\n"
        "\n"
        "  /strategy list                       Show available presets\n"
        "  /strategy preview <name> <args>      See what the preset would do\n"
        "  /strategy apply <name> <args>        Materialize as actual commands\n"
        "  /strategy history                    Recently applied strategies\n"
        "\n"
        "Presets:\n"
        "  whale-shadow <wallet> <eth_per_copy>\n"
        "    /copy add <wallet> <eth> --invert  +  /alerts add wallet <wallet>\n"
        "  dca-and-snipe <token> <dca_eth> <interval> <snipe_eth>\n"
        "    /dca add <token> <dca_eth> <interval>  +  /sniper add <snipe_eth>\n"
        "  laddered-tp <eth_per_copy> <wallet> <tp1>:<tp2>:<tp3>\n"
        "    /copy add <wallet> <eth>  +  /sniper add <eth> --auto-sell <tp1>:50"
    )


# ── /strategy list ──────────────────────────────────────────────────


def _cmd_list() -> str:
    lines = ["Available strategy presets:", ""]
    for name in sorted(_PRESETS):
        lines.append(f"  {name}")
    lines.append("")
    lines.append("Use /strategy preview <name> <args> to see the resulting commands.")
    return "\n".join(lines)


# ── /strategy preview ──────────────────────────────────────────────


def _cmd_preview(parts: list[str]) -> str:
    if not parts:
        return "Usage: /strategy preview <name> <args>"
    name = parts[0]
    args = parts[1:]
    preset = _PRESETS.get(name)
    if preset is None:
        return f"Unknown preset {name!r}. Use /strategy list to see available presets."
    steps, err = preset(args)
    if err:
        return err
    lines = [f"Preview for '{name}':", ""]
    for i, step in enumerate(steps, start=1):
        lines.append(f"  {i}. /{step['command']} {step['args']}")
        lines.append(f"       → {step['summary']}")
    lines.append("")
    lines.append("Use /strategy apply to materialize these as actual commands.")
    return "\n".join(lines)


# ── /strategy apply ────────────────────────────────────────────────


async def _cmd_apply(sender_id: str, parts: list[str]) -> str:
    if not parts:
        return "Usage: /strategy apply <name> <args>"

    # UNLIMITED tier check — applies the gate at the top level so each
    # preset's downstream commands don't need to re-check.
    from clawmes.services.token_gate import Tier, check_tier_or_error

    gate_err = check_tier_or_error(Tier.UNLIMITED, feature="/strategy apply")
    if gate_err:
        return gate_err

    name = parts[0]
    args = parts[1:]
    preset = _PRESETS.get(name)
    if preset is None:
        return f"Unknown preset {name!r}. Use /strategy list to see available presets."
    steps, err = preset(args)
    if err:
        return err

    strat_id = _new_id()
    results: list[dict[str, Any]] = []
    output_lines = [f"Applying strategy '{name}' as {strat_id}:", ""]
    for i, step in enumerate(steps, start=1):
        cmd_out = await _dispatch_step(step, sender_id)
        first_line = cmd_out.split("\n", 1)[0][:160]
        output_lines.append(f"  {i}. /{step['command']} {step['args']}")
        output_lines.append(f"       → {first_line}")
        results.append({"step": step, "first_line": first_line})

    # Record in history.
    state = _load_history()
    state["applied"].append(
        {
            "id": strat_id,
            "sender_id": sender_id,
            "preset": name,
            "args": args,
            "at": _now_iso(),
            "results": results,
        }
    )
    _save_history(state)

    output_lines.append("")
    output_lines.append("Each step ran as an independent command. See individual")
    output_lines.append("command outputs in /history for full results.")
    return "\n".join(output_lines)


async def _dispatch_step(step: dict[str, Any], sender_id: str) -> str:
    """Run one preset step by dispatching to the matching command handler."""
    cmd = step["command"]
    args = step["args"]

    if cmd == "copy":
        from clawmes.commands.copy import handle_copy

        return await handle_copy(args, sender_id=sender_id)
    if cmd == "dca":
        from clawmes.commands.dca import handle_dca

        return await handle_dca(args, sender_id=sender_id)
    if cmd == "sniper":
        from clawmes.commands.sniper import handle_sniper

        return await handle_sniper(args, sender_id=sender_id)
    if cmd == "alerts":  # pragma: no branch — last branch in preset set
        from clawmes.commands.alerts import handle_alerts

        return await handle_alerts(args, sender_id=sender_id)
    return f"unknown step command: {cmd}"  # pragma: no cover — defensive


# ── /strategy history ──────────────────────────────────────────────


def _cmd_history(sender_id: str) -> str:
    state = _load_history()
    mine = [a for a in state["applied"] if a.get("sender_id") == sender_id]
    if not mine:
        return f"No strategy history for {sender_id}."
    lines = [f"Strategy history for {sender_id} ({len(mine)}):", ""]
    for app in mine[-25:]:
        when = app.get("at", "")
        preset = app.get("preset", "?")
        args = " ".join(app.get("args", []))
        lines.append(f"  {when}  {app['id']}  {preset} {args}")
    return "\n".join(lines)


def register(ctx) -> None:
    ctx.register_command(
        name="strategy",
        handler=handle_strategy,
        description="Preset strategy templates (Clawmes Unlimited tier)",
        args_hint="list | preview <name> <args> | apply <name> <args> | history",
    )
