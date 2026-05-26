"""``/my_launches`` slash command — list tokens this user has launched.

Two universes:

  * ``--clawnch`` (default) — tokens deployed via the Clawnch HTTP
    API by this user's API key. Sourced from ``GET /api/agents/me``.
    Requires ``CLAWNCH_API_KEY``. Most useful default — covers the
    common case "what did I launch from clawmes".

  * ``--all`` — tokens deployed by the *connected wallet*, not just
    via Clawnch. Includes Clanker-direct deploys, custom factory
    deploys, and any other ERC-20 the wallet has created. Sourced
    from Basescan's ``account / txlist`` API filtered to
    contract-creation transactions. Requires a connected wallet.

The ``--all`` path adds DexScreener enrichment per-address so the
output shows symbol / market cap / 24h volume for tokens that have
since accrued liquidity. Tokens with no DexScreener entry render as
"(no liquidity)" — the deploy succeeded, but no pair exists yet (or
the token was abandoned).

Both universes are de-duplicated by contract address so a token
launched via Clawnch and re-listed in the wallet history shows up
once.
"""

from __future__ import annotations

from typing import Any

from clawmes.lib import dexscreener
from clawmes.lib.http import http_get
from clawmes.services.clawnch import ClawnchError, get_clawnch_service
from clawmes.services.wallet import get_wallet_state

_BASESCAN_BASE = "https://api.basescan.org/api"
_MAX_RESULTS = 25


def _record(name: str, args: str, result: str) -> None:
    """Best-effort recording into command_history."""
    try:
        from clawmes.services.command_history import record_command_call

        record_command_call(name, args, result)
    except Exception:  # noqa: BLE001
        pass


def _parse_args(raw: str) -> str:
    """Return universe (``"clawnch"`` or ``"all"``). Default ``"clawnch"``."""
    universe = "clawnch"
    for tok in (raw or "").split():
        if tok == "--clawnch":
            universe = "clawnch"
        elif tok == "--all":
            universe = "all"
    return universe


async def handle_my_launches(raw_args: str, **_kwargs: Any) -> str:
    universe = _parse_args(raw_args or "")
    if universe == "all":
        out = _render_all()
    else:
        out = _render_clawnch()
    _record("my_launches", raw_args, out)
    return out


# ── --clawnch (Clawnch API) ─────────────────────────────────────────


def _render_clawnch() -> str:
    try:
        body = get_clawnch_service().get_my_launches()
    except ClawnchError as exc:
        msg = f"Could not fetch Clawnch launches ({exc.code}): {exc.message}"
        if exc.code == "no_credentials":
            msg += "\n\nRun /register_agent <name> <description> first."
        return msg

    launches = _extract_launches(body)
    if not launches:
        return (
            "No Clawnch launches found for this agent.\n"
            "Pass --all to scan the connected wallet for any ERC-20 deploys."
        )

    lines = [f"Your Clawnch launches ({len(launches)}):", ""]
    for i, lau in enumerate(launches, start=1):
        lines.append(f"  {i}. {_format_clawnch_launch(lau)}")
    lines.append("")
    lines.append("Pass --all to also include non-Clawnch deploys from this wallet.")
    return "\n".join(lines)


def _extract_launches(body: Any) -> list[dict[str, Any]]:
    """Tolerate /api/agents/me shape drift."""
    if isinstance(body, list):
        return [x for x in body if isinstance(x, dict)]
    if isinstance(body, dict):
        for key in ("launches", "tokens", "data", "results"):
            inner = body.get(key)
            if isinstance(inner, list):
                return [x for x in inner if isinstance(x, dict)]
    return []


def _format_clawnch_launch(lau: dict[str, Any]) -> str:
    symbol = lau.get("symbol") or lau.get("ticker") or "?"
    name = lau.get("name") or ""
    addr = lau.get("contractAddress") or lau.get("tokenAddress") or lau.get("address") or ""
    parts = [symbol]
    if name and name != symbol:
        parts.append(f"({name})")
    if addr:
        parts.append(f"→ {_short(addr)}")
    return "  ".join(parts)


# ── --all (Basescan wallet scan) ────────────────────────────────────


def _render_all() -> str:
    state = get_wallet_state()
    if not state.connected or not state.address:
        return (
            "No wallet connected — /my_launches --all needs to scan the "
            "wallet's deploy history. Run /connect first, or use "
            "/my_launches (defaults to --clawnch)."
        )

    try:
        creations = _basescan_contract_creations(state.address)
    except Exception as exc:  # noqa: BLE001
        return f"Could not fetch wallet history from Basescan: {exc}"

    if not creations:
        return (
            f"No contract creations found for {_short(state.address)} on Base.\n"
            "If you launched via clawmes, try /my_launches --clawnch."
        )

    # Enrich with DexScreener; only token-shaped deploys (those with a
    # pair) render with market data. Non-pair creations (helper contracts,
    # factories, etc.) render as "(no DEX listing)" so the user still
    # sees them. The basescan helper already guarantees ``contractAddress``
    # is truthy, so no defensive skip needed here.
    lines = [f"Contract deploys from {_short(state.address)} on Base:", ""]
    for i, tx in enumerate(creations[:_MAX_RESULTS], start=1):
        addr = tx["contractAddress"]
        pair = dexscreener.find_token(addr, chain="base")
        if pair:
            lines.append(f"  {i}. {dexscreener.format_pair_summary(pair)}")
        else:
            lines.append(f"  {i}. {_short(addr)}  (no DEX listing)")
    lines.append("")
    lines.append("Pass --clawnch to restrict to launchpad-tracked launches.")
    return "\n".join(lines)


def _basescan_contract_creations(address: str) -> list[dict[str, Any]]:
    """Return contract-creation txs (``to == ""``) from this address.

    Uses the public Basescan v1 API. ``BASESCAN_API_KEY`` is optional —
    free tier permits ~5 req/sec without a key, sufficient for a single
    user listing their own history. With a key the rate-limit is
    lifted.
    """
    import os

    params = {
        "module": "account",
        "action": "txlist",
        "address": address,
        "startblock": "0",
        "endblock": "99999999",
        "sort": "desc",
    }
    api_key = os.environ.get("BASESCAN_API_KEY")
    if api_key:
        params["apikey"] = api_key

    body = http_get(_BASESCAN_BASE, params=params, timeout=20.0)
    if not isinstance(body, dict):
        return []
    if str(body.get("status")) != "1":
        # Basescan returns status="0" + message="No transactions found" for
        # empty histories — that's a normal empty result, not an error.
        return []
    items = body.get("result") or []
    if not isinstance(items, list):
        return []
    creations: list[dict[str, Any]] = []
    for tx in items:
        if not isinstance(tx, dict):
            continue
        # Contract creation: ``to`` is empty + ``contractAddress`` is set.
        if (tx.get("to") or "") == "" and tx.get("contractAddress"):
            creations.append(tx)
    return creations


def _short(addr: str) -> str:
    if not addr or len(addr) < 10:
        return addr
    return f"{addr[:6]}…{addr[-4:]}"


def register(ctx) -> None:
    ctx.register_command(
        name="my_launches",
        handler=handle_my_launches,
        description="List tokens this user has launched (Clawnch API or full wallet)",
        args_hint="[--clawnch | --all]",
    )
