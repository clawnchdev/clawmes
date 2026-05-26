"""``/buy`` slash command — buy a token with ETH via 0x.

Two-step flow (mirrors ``/launch``): first call previews a quote and
stores a draft, second call (``/buy confirm``) executes the swap.
This avoids accidental fat-finger trades — the user always sees the
expected output amount + route before signing.

Universes (token resolution):

  * ``--all`` (default) — symbol resolution via DexScreener. Widest
    discovery, returns the highest-volume pair on Base for the symbol.
    The user is responsible for verifying the resolved address — the
    confirm step prints it before signing.

  * ``--clawnch`` — same DexScreener resolution but the resolved
    address is additionally verified via Clawnch's
    ``GET /api/launches?address=<addr>``. Rejects tokens that weren't
    deployed via the launchpad. Useful when the user wants to scope
    to vetted-by-Clawnch tokens only.

Addresses (``0x...``) bypass universe resolution and are accepted
verbatim — the caller has already done the lookup.

The actual swap goes through the ``defi_swap`` tool which uses 0x's
Permit2 endpoint (single-sig swap, no separate ``approve`` tx). The
slash command is a thin sender over that tool.
"""

from __future__ import annotations

import json
import threading
from typing import Any

from clawmes.lib import dexscreener
from clawmes.services.wallet import get_wallet_state

_draft_state: dict[str, dict[str, Any]] = {}
_state_lock = threading.RLock()


# ── per-sender state helpers ────────────────────────────────────────


def _record(name: str, args: str, result: str) -> None:
    try:
        from clawmes.services.command_history import record_command_call

        record_command_call(name, args, result)
    except Exception:  # noqa: BLE001
        pass


def _get_draft(sender_id: str) -> dict[str, Any] | None:
    with _state_lock:
        return _draft_state.get(sender_id)


def _set_draft(sender_id: str, draft: dict[str, Any]) -> None:
    with _state_lock:
        _draft_state[sender_id] = draft


def _clear_draft(sender_id: str) -> None:
    with _state_lock:
        _draft_state.pop(sender_id, None)


def _resolve_sender(kwargs: dict[str, Any]) -> str:
    return str(kwargs.get("sender_id") or "default")


# ── arg parsing ─────────────────────────────────────────────────────


def _parse_flags(tokens: list[str]) -> tuple[list[str], str]:
    """Split ``tokens`` into ``(positional, universe)``.

    ``universe`` is ``"clawnch"`` if ``--clawnch`` was passed, else
    ``"all"`` (the default — broader). Unknown flags are dropped
    silently rather than failing the parse.
    """
    universe = "all"
    positional: list[str] = []
    for tok in tokens:
        if tok == "--clawnch":
            universe = "clawnch"
        elif tok == "--all":
            universe = "all"
        elif tok.startswith("--"):
            # Unknown flag — ignore so future-flag forward-compat doesn't
            # break older clients.
            continue
        else:
            positional.append(tok)
    return positional, universe


# ── main handler ────────────────────────────────────────────────────


async def handle_buy(raw_args: str, **kwargs: Any) -> str:
    sender_id = _resolve_sender(kwargs)
    arg = (raw_args or "").strip()
    if not arg:
        out = _render_usage(_get_draft(sender_id))
        _record("buy", raw_args, out)
        return out

    parts = arg.split()
    first = parts[0].lower()

    if first == "confirm":
        out = await _confirm(sender_id)
    elif first == "cancel":
        _clear_draft(sender_id)
        out = "Buy draft cleared."
    elif first == "status":
        out = _render_status(_get_draft(sender_id))
    else:
        out = await _quote(sender_id, parts)

    _record("buy", raw_args, out)
    return out


def _render_usage(draft: dict[str, Any] | None) -> str:
    lines = [
        "Buy a token with ETH on Base via the 0x aggregator.",
        "",
        "Usage:",
        "  /buy <token> <eth_amount> [--clawnch | --all]",
        "  /buy confirm        — execute the most recent quote",
        "  /buy cancel         — clear the draft",
        "  /buy status         — show the current draft",
        "",
        "<token> can be a symbol (resolved via DexScreener) or a 0x address.",
        "Default universe is --all; pass --clawnch to restrict to launchpad-deployed tokens.",
        "",
        "A wallet must be connected (/connect or /connect_local).",
    ]
    if draft:
        lines.append("")
        lines.append("Current draft:")
        lines.extend(_format_draft_lines(draft))
    return "\n".join(lines)


def _render_status(draft: dict[str, Any] | None) -> str:
    if not draft:
        return "No buy draft. Start with /buy <token> <eth_amount>."
    lines = ["Buy draft:"]
    lines.extend(_format_draft_lines(draft))
    return "\n".join(lines)


def _format_draft_lines(draft: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for key, value in draft.items():
        out.append(f"  {key}: {value}")
    return out


# ── quote path ──────────────────────────────────────────────────────


async def _quote(sender_id: str, parts: list[str]) -> str:
    positional, universe = _parse_flags(parts)
    if len(positional) < 2:
        return "Usage: /buy <token> <eth_amount> [--clawnch | --all]"

    token_in, amount_raw = positional[0], positional[1]
    try:
        amount = float(amount_raw)
        if amount <= 0:
            raise ValueError
    except ValueError:
        return f"Invalid ETH amount: {amount_raw!r}. Must be a positive number."

    state = get_wallet_state()
    if not state.connected or not state.address:
        return "No wallet connected. Run /connect or /connect_local first."

    resolved = _resolve_token(token_in, universe)
    if isinstance(resolved, str):
        # error message
        return resolved
    addr, label = resolved

    # Quote via defi_swap. The tool returns a JSON envelope; we unwrap.
    from clawmes.tools.defi_swap import defi_swap

    raw = defi_swap(
        {
            "action": "quote",
            "sell_token": "ETH",
            "buy_token": addr,
            "sell_amount": str(amount),
        }
    )
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return f"Quote failed (bad response): {raw}"
    if payload.get("isError"):
        msg = payload.get("content", [{}])[0].get("text", "quote failed")
        return f"Quote failed: {msg}"

    details = payload.get("details") or {}
    expected_out = (
        details.get("buy_amount") or details.get("buyAmount") or details.get("output_amount") or "?"
    )
    price = details.get("price") or details.get("guaranteedPrice") or ""
    route = details.get("route") or details.get("sources") or ""

    draft = {
        "token": label,
        "buy_token": addr,
        "sell_eth": str(amount),
        "expected_out": str(expected_out),
        "universe": universe,
        "price": str(price) if price else "",
    }
    _set_draft(sender_id, draft)

    # Best-effort Clawnch attribution — look the token up in the
    # launchpad index. Doesn't affect the quote; just adds context so
    # the user knows whether they're buying something Clawnch tracks.
    # Failures (HTTP issues, address not in the index) silently skip
    # the attribution line.
    attribution = _lookup_clawnch_attribution(addr)

    lines = [
        f"Quote: {amount} ETH → {expected_out} {label}",
        f"  Address: {addr}",
        f"  Universe: {universe}",
    ]
    if price:
        lines.append(f"  Price: {price}")
    if route:
        lines.append(f"  Route: {route}")
    if attribution:
        lines.append(f"  {attribution}")
    lines.append("")
    lines.append("Run /buy confirm to execute, or /buy cancel to discard.")
    return "\n".join(lines)


def _lookup_clawnch_attribution(address: str) -> str | None:
    """Return a one-line Clawnch attribution string, or None if not found.

    Hits ``/api/launches?address=`` (the same endpoint /buy --clawnch
    uses to verify). Renders source + agent + age when available so the
    user can tell at a glance "yes, this is a Clawnch-launched token,
    deployed N hours ago by source X."

    Best-effort — never raises, never blocks the quote.
    """
    try:
        from clawmes.services.clawnch import ClawnchError, get_clawnch_service

        body = get_clawnch_service().get_launch(address)
    except ClawnchError:
        return None
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(body, dict):
        return None
    if body.get("error") or body.get("success") is False:
        return None
    launch = body.get("launch") if "launch" in body else body
    if not isinstance(launch, dict):
        return None
    source = launch.get("source") or "clawnch"
    agent = launch.get("agentName") or launch.get("agent") or ""
    launched_at = launch.get("launchedAt") or ""

    parts = [f"Clawnch: source {source}"]
    if agent:
        parts.append(f"agent {agent}")
    if launched_at:
        parts.append(launched_at)
    return " · ".join(parts)


def _resolve_token(token_in: str, universe: str) -> tuple[str, str] | str:
    """Resolve ``token_in`` to ``(address, display_label)`` or an error.

    Address inputs (``0x...``) are returned verbatim. Symbol inputs go
    through DexScreener; if ``universe == "clawnch"`` the resolved
    address is additionally verified against the Clawnch launches
    endpoint.

    Returns an error string when resolution fails — the caller surfaces
    it directly to the user.
    """
    q = token_in.strip()
    if _looks_like_address(q):
        return (q, _short(q))

    pair = dexscreener.find_token(q, chain="base")
    if not pair:
        return f"No Base pair found for {q!r} on DexScreener."
    base = pair.get("baseToken") or {}
    addr = base.get("address") or ""
    sym = base.get("symbol") or q
    if not addr:
        return f"DexScreener match for {q!r} had no token address."

    if universe == "clawnch":
        ok, reason = _is_clawnch_launched(addr)
        if not ok:
            return (
                f"{sym} ({_short(addr)}) is not a Clawnch-launched token "
                f"({reason}). Drop --clawnch or pick another token."
            )

    return (addr, sym)


def _is_clawnch_launched(address: str) -> tuple[bool, str]:
    """True iff ``/api/launches?address=`` returns a record for ``address``."""
    from clawmes.services.clawnch import ClawnchError, get_clawnch_service

    try:
        body = get_clawnch_service().get_launch(address)
    except ClawnchError as exc:
        return (False, exc.code)
    except Exception as exc:  # noqa: BLE001
        return (False, f"lookup error: {exc}")
    # Empty / null body → not found.
    if not body:
        return (False, "not_found")
    if isinstance(body, dict):
        # Backend returns 200 + {"error": "..."} for missing rows in some
        # branches; treat any explicit error field as "not Clawnch".
        if body.get("error"):
            return (False, "not_found")
        return (True, "ok")
    return (True, "ok")


# ── confirm path ────────────────────────────────────────────────────


async def _confirm(sender_id: str) -> str:
    draft = _get_draft(sender_id)
    if not draft:
        return "No buy draft. Run /buy <token> <eth_amount> first."

    state = get_wallet_state()
    if not state.connected or not state.address:
        return "No wallet connected. Run /connect or /connect_local first."

    from clawmes.tools.defi_swap import defi_swap

    raw = defi_swap(
        {
            "action": "swap",
            "sell_token": "ETH",
            "buy_token": draft["buy_token"],
            "sell_amount": draft["sell_eth"],
        }
    )
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return f"Swap failed (bad response): {raw}"
    if payload.get("isError"):
        msg = payload.get("content", [{}])[0].get("text", "swap failed")
        return f"Swap failed: {msg}"

    details = payload.get("details") or {}
    tx_hash = details.get("tx_hash") or details.get("txHash") or ""
    _clear_draft(sender_id)
    lines = [f"Buy submitted: {draft['sell_eth']} ETH → {draft['token']}"]
    if tx_hash:
        lines.append(f"  Tx: {tx_hash}")
        lines.append(f"  Basescan: https://basescan.org/tx/{tx_hash}")
    return "\n".join(lines)


# ── small helpers ───────────────────────────────────────────────────


def _looks_like_address(value: str) -> bool:
    if not value.startswith("0x"):
        return False
    body = value[2:]
    if len(body) != 40:
        return False
    return all(c in "0123456789abcdefABCDEF" for c in body)


def _short(addr: str) -> str:
    if not addr or len(addr) < 10:
        return addr
    return f"{addr[:6]}…{addr[-4:]}"


def register(ctx) -> None:
    ctx.register_command(
        name="buy",
        handler=handle_buy,
        description="Buy a token with ETH on Base via 0x (quote → confirm)",
        args_hint="<token> <eth_amount> [--clawnch | --all] | confirm | cancel | status",
    )
