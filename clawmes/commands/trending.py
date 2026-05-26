"""``/trending`` slash command — discover hot tokens.

Two universes:

  * ``--clawnch`` — top tokens launched via the Clawnch launchpad,
    sorted by the launchpad's own 24h volume ranking. Sourced from
    ``GET /api/tokens?sort=volume&prices=1&limit=N`` on the Clawnch
    backend (no auth required).

  * ``--all`` (default) — top tokens on Base by 24h volume, sourced
    from DexScreener. Broader universe — includes Clanker-direct
    deploys, legacy launches, and tokens that pre-date Clawnch.

The default is ``--all`` so newcomers see the widest discovery
surface. Operators who only care about the launchpad cohort can
pass ``--clawnch`` to restrict.

No wallet required, no auth required, read-only.
"""

from __future__ import annotations

from typing import Any

from clawmes.lib import dexscreener
from clawmes.lib.http import http_get

_DEFAULT_LIMIT = 10
_MAX_LIMIT = 25
_CLAWNCH_API_BASE = "https://clawn.ch"


def _record(name: str, args: str, result: str) -> None:
    """Best-effort recording into command_history."""
    try:
        from clawmes.services.command_history import record_command_call

        record_command_call(name, args, result)
    except Exception:  # noqa: BLE001
        pass


def _parse_args(raw: str) -> tuple[str, int]:
    """Return ``(universe, limit)``.

    ``universe`` is one of ``"clawnch"`` / ``"all"``. ``limit`` is
    clamped to ``[1, _MAX_LIMIT]``. Unknown bare args (not a flag, not
    a number) are silently ignored — the command should never crash
    on user typos.
    """
    universe = "all"
    limit = _DEFAULT_LIMIT
    # ``.split()`` with no arg already drops empties — no defensive
    # empty-token guard needed.
    for tok in (raw or "").split():
        if tok == "--clawnch":
            universe = "clawnch"
        elif tok == "--all":
            universe = "all"
        elif tok.isdigit():
            limit = max(1, min(_MAX_LIMIT, int(tok)))
    return universe, limit


async def handle_trending(raw_args: str, **_kwargs: Any) -> str:
    universe, limit = _parse_args(raw_args or "")
    if universe == "clawnch":
        out = _render_clawnch(limit)
    else:
        out = _render_all(limit)
    _record("trending", raw_args, out)
    return out


def _render_clawnch(limit: int) -> str:
    """Render the top-N Clawnch tokens by 24h volume."""
    try:
        body = http_get(
            f"{_CLAWNCH_API_BASE}/api/tokens",
            params={"sort": "volume", "prices": "1", "limit": str(limit)},
            timeout=15.0,
        )
    except Exception as exc:  # noqa: BLE001 — surface as user-facing
        return f"Could not fetch Clawnch trending: {exc}"

    tokens = _extract_clawnch_tokens(body)
    if not tokens:
        return "No Clawnch tokens found. Try /trending --all for the wider pool."

    lines = [f"Top {len(tokens)} Clawnch tokens by 24h volume:", ""]
    for i, tok in enumerate(tokens, start=1):
        lines.append(f"  {i}. {_format_clawnch_token(tok)}")
    lines.append("")
    lines.append("Pass --all for the wider Base universe (DexScreener).")
    return "\n".join(lines)


def _extract_clawnch_tokens(body: Any) -> list[dict[str, Any]]:
    """Pull the token list out of /api/tokens, tolerating shape drift."""
    if isinstance(body, list):
        return [t for t in body if isinstance(t, dict)]
    if isinstance(body, dict):
        for key in ("tokens", "data", "results"):
            inner = body.get(key)
            if isinstance(inner, list):
                return [t for t in inner if isinstance(t, dict)]
    return []


def _format_clawnch_token(tok: dict[str, Any]) -> str:
    """One-line render for a Clawnch token."""
    symbol = tok.get("symbol") or tok.get("ticker") or "?"
    name = tok.get("name") or ""
    addr = tok.get("contractAddress") or tok.get("address") or ""
    vol24 = tok.get("volume24h") or tok.get("volumeUSD") or tok.get("volume") or 0
    price = tok.get("priceUsd") or tok.get("price") or None
    mc = tok.get("marketCap") or tok.get("fdv") or None
    parts = [symbol]
    if name and name != symbol:
        parts.append(f"({name})")
    if price is not None:
        parts.append(f"${price}")
    if mc:
        parts.append(f"mc {_compact(mc)}")
    if vol24:
        parts.append(f"vol24h {_compact(vol24)}")
    if addr:
        parts.append(f"→ {_short(addr)}")
    return "  ".join(parts)


def _render_all(limit: int) -> str:
    """Render the top-N tokens on Base by 24h volume."""
    pairs = dexscreener.top_pairs(chain="base", limit=limit)
    if not pairs:
        err = dexscreener.last_error()
        suffix = f" ({err})" if err else ""
        return f"No Base trending pairs returned by DexScreener{suffix}."

    lines = [f"Top {len(pairs)} Base tokens by 24h volume (DexScreener):", ""]
    for i, p in enumerate(pairs, start=1):
        lines.append(f"  {i}. {dexscreener.format_pair_summary(p)}")
    lines.append("")
    lines.append("Pass --clawnch to restrict to launchpad-deployed tokens.")
    return "\n".join(lines)


def _compact(n: float | int | str) -> str:
    """Compact USD: 1.3k / 55k / 1.3M / 2.4B."""
    try:
        v = float(n)
    except (TypeError, ValueError):
        return f"${n}"
    if v >= 1_000_000_000:
        return f"${v / 1_000_000_000:.2f}B"
    if v >= 1_000_000:
        return f"${v / 1_000_000:.2f}M"
    if v >= 1_000:
        return f"${v / 1_000:.1f}k"
    return f"${v:.2f}"


def _short(addr: str) -> str:
    if not addr or len(addr) < 10:
        return addr
    return f"{addr[:6]}…{addr[-4:]}"


def register(ctx) -> None:
    ctx.register_command(
        name="trending",
        handler=handle_trending,
        description="Top tokens by 24h volume on Base (or just Clawnch launches)",
        args_hint="[--clawnch | --all] [limit]",
    )
