"""``/launch`` slash command — guided token deploy on Clawnch.

Multi-turn flow that walks the user through deploying a token via the
Clawnch launchpad from chat. State is held per ``sender_id`` so a
single channel can have multiple deploys in progress concurrently.

Steps:

  1. ``/launch`` (no args) — show usage + start a new deploy slot.
  2. ``/launch name <token name>`` — set the name.
  3. ``/launch symbol <ticker>`` — set the symbol.
  4. ``/launch description <text>`` — optional one-liner.
  5. ``/launch bypass <0x...>`` — optional tx hash to skip the 24h
     cooldown. Falls back to free path if omitted.
  6. ``/launch confirm`` — execute the deploy.
  7. ``/launch cancel`` — clear the slot.
  8. ``/launch status`` — show the current draft.

Auth: requires ``CLAWNCH_API_KEY`` in env (Clawnch refuses unauth'd
deploys). The user-facing error from the underlying call tells the
user how to get a key (``/register_agent``).

Wallet: the active wallet mode must be connected — the captcha
challenge is signed via ``personal_sign``.
"""

from __future__ import annotations

import threading
from typing import Any

_log_state: dict[str, dict[str, Any]] = {}
_state_lock = threading.RLock()


def _record(name: str, args: str, result: str) -> None:
    """Best-effort recording into command_history. Matches the existing
    pattern from other clawmes commands."""
    try:
        from clawmes.services.command_history import record_command_call

        record_command_call(name, args, result)
    except Exception:  # noqa: BLE001
        pass


def _get_draft(sender_id: str) -> dict[str, Any]:
    with _state_lock:
        draft = _log_state.get(sender_id)
        if draft is None:
            draft = {}
            _log_state[sender_id] = draft
        return draft


def _clear_draft(sender_id: str) -> None:
    with _state_lock:
        _log_state.pop(sender_id, None)


def _resolve_sender(kwargs: dict[str, Any]) -> str:
    return str(kwargs.get("sender_id") or "default")


async def handle_launch(raw_args: str, **kwargs: Any) -> str:
    sender_id = _resolve_sender(kwargs)
    arg = (raw_args or "").strip()
    parts = arg.split(maxsplit=1)

    if not arg:
        out = _render_usage(_get_draft(sender_id))
        _record("launch", raw_args, out)
        return out

    action = parts[0].lower()
    rest = parts[1].strip() if len(parts) > 1 else ""

    if action == "status":
        out = _render_status(_get_draft(sender_id))
    elif action == "cancel":
        _clear_draft(sender_id)
        out = "Launch draft cleared."
    elif action == "name":
        draft = _get_draft(sender_id)
        if not rest:
            out = "Usage: /launch name <token name>"
        else:
            draft["name"] = rest
            out = f"Name set: {rest}. Next: /launch symbol <TICKER>"
    elif action == "symbol":
        draft = _get_draft(sender_id)
        if not rest:
            out = "Usage: /launch symbol <TICKER>"
        else:
            draft["symbol"] = rest.upper()
            out = (
                f"Symbol set: {draft['symbol']}. "
                "Next: /launch description <text> (optional) or "
                "/launch confirm."
            )
    elif action == "description":
        draft = _get_draft(sender_id)
        if not rest:
            out = "Usage: /launch description <text>"
        else:
            draft["description"] = rest
            out = "Description set. Next: /launch confirm."
    elif action == "bypass":
        draft = _get_draft(sender_id)
        if not rest:
            out = "Usage: /launch bypass <tx_hash>"
        else:
            draft["bypass_tx_hash"] = rest
            out = "Bypass tx recorded. Next: /launch confirm."
    elif action == "confirm":
        out = await _confirm(sender_id)
    else:
        out = (
            f"Unknown /launch arg {action!r}. Use:\n"
            "  /launch                       — show this help\n"
            "  /launch name <name>           — set token name\n"
            "  /launch symbol <TICKER>       — set ticker\n"
            "  /launch description <text>    — optional description\n"
            "  /launch bypass <tx_hash>      — skip 24h cooldown\n"
            "  /launch status                — show current draft\n"
            "  /launch confirm               — deploy\n"
            "  /launch cancel                — clear draft"
        )
    _record("launch", raw_args, out)
    return out


def _render_usage(draft: dict[str, Any]) -> str:
    lines = [
        "Launch a token on Clawnch (guided flow).",
        "",
        "  /launch name <token name>",
        "  /launch symbol <TICKER>",
        "  /launch description <text>     (optional)",
        "  /launch bypass <tx_hash>       (optional — skip 24h cooldown)",
        "  /launch confirm",
        "",
        "Requires CLAWNCH_API_KEY. Use /register_agent if you need a key.",
        "Active wallet must be connected — the deploy signs a captcha.",
    ]
    if draft:
        lines.append("")
        lines.append("Current draft:")
        for key, value in draft.items():
            lines.append(f"  {key}: {value}")
    return "\n".join(lines)


def _render_status(draft: dict[str, Any]) -> str:
    if not draft:
        return "No launch draft. Start with /launch name <token name>."
    lines = ["Launch draft:"]
    for key, value in draft.items():
        lines.append(f"  {key}: {value}")
    return "\n".join(lines)


async def _confirm(sender_id: str) -> str:
    draft = _get_draft(sender_id)
    name = draft.get("name")
    symbol = draft.get("symbol")
    if not name or not symbol:
        return (
            "Launch needs at minimum a name and a symbol. "
            "Run /launch name <…> and /launch symbol <TICKER> first."
        )

    token_params: dict[str, Any] = {"name": name, "symbol": symbol}
    if description := draft.get("description"):
        token_params["description"] = description

    bypass = draft.get("bypass_tx_hash")

    try:
        from clawmes.services.clawnch import ClawnchError, get_clawnch_service

        result = get_clawnch_service().deploy(
            token_params=token_params,
            bypass_tx_hash=bypass,
        )
    except ClawnchError as exc:
        msg = f"Launch failed ({exc.code}): {exc.message}"
        if exc.code == "no_credentials":
            msg += "\n\nRun /register_agent <name> <description> to get a key."
        elif exc.code == "rate_limited":
            from clawmes.services.clawnch import get_clawnch_service as _svc

            bypass_info = _svc().get_bypass_recipient()
            msg += (
                f"\n\nBypass: send {bypass_info['fee_eth']} ETH to "
                f"{bypass_info['recipient']} on Base, then "
                f"/launch bypass <tx_hash> and /launch confirm."
            )
        return msg
    except Exception as exc:  # noqa: BLE001
        return f"Launch failed: {exc}"

    _clear_draft(sender_id)
    tx_hash = result.get("txHash") or result.get("tx_hash")
    token_address = result.get("tokenAddress") or result.get("token_address")
    lines = ["Launched."]
    if token_address:
        lines.append(f"  Token: {token_address}")
    if tx_hash:
        lines.append(f"  Tx: {tx_hash}")
        lines.append(f"  Chart: https://dexscreener.com/base/{token_address or tx_hash}")
    return "\n".join(lines)


def register(ctx) -> None:
    ctx.register_command(
        name="launch",
        handler=handle_launch,
        description="Deploy a token on Clawnch via guided chat flow",
        args_hint="[name | symbol | description | bypass | status | confirm | cancel] <value>",
    )
