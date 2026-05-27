"""``/leaderboard`` — top tokens + top launchers on Clawnch.

Three views in one command:

  * ``/leaderboard`` (default) — top 10 tokens on the Clawnch index
    by 24h volume, with live price / mcap / 24h change.
  * ``/leaderboard launchers`` — top 10 agents by number of launches
    in the past 24h. Drives the shame/celebrate loop for active
    deployers.
  * ``/leaderboard burners`` — top wallets by total $CLAWNCH burned
    in the past 7d (when the burn-payment gate is enforced on cron
    paths, this is essentially "top spenders" — the people pumping
    the token the hardest).

All three are public read-only views — no wallet required. They hit
the existing ``/api/tokens`` + ``/api/launches`` endpoints and
aggregate client-side. No new server-side endpoints needed; we use
what we already index.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from clawmes.lib.http import http_get

_CLAWNCH_API_BASE = "https://www.clawn.ch"
_DEFAULT_LIMIT = 10
_MAX_LIMIT = 25


def _record(name: str, args: str, result: str) -> None:
    """Best-effort recording into command_history."""
    try:
        from clawmes.services.command_history import record_command_call

        record_command_call(name, args, result)
    except Exception:  # noqa: BLE001
        pass


def _parse_args(raw: str) -> tuple[str, int]:
    """Return ``(view, limit)``.

    ``view`` is one of ``"tokens"`` (default), ``"launchers"``, ``"burners"``.
    Unknown args are silently ignored (so the command never crashes on
    a typo).
    """
    view = "tokens"
    limit = _DEFAULT_LIMIT
    for tok in (raw or "").split():
        if tok in ("launchers", "deployers"):
            view = "launchers"
        elif tok in ("burners", "burn"):
            view = "burners"
        elif tok in ("tokens", "top"):
            view = "tokens"
        elif tok.isdigit():
            limit = max(1, min(_MAX_LIMIT, int(tok)))
    return view, limit


async def handle_leaderboard(raw_args: str, **_kwargs: Any) -> str:
    view, limit = _parse_args(raw_args or "")
    if view == "launchers":
        out = _render_launchers(limit)
    elif view == "burners":
        out = _render_burners(limit)
    else:
        out = _render_tokens(limit)
    _record("leaderboard", raw_args, out)
    return out


# ── top tokens by 24h volume ────────────────────────────────────────


def _render_tokens(limit: int) -> str:
    try:
        body = http_get(
            f"{_CLAWNCH_API_BASE}/api/tokens",
            params={"sort": "volume", "prices": "1", "limit": str(limit)},
            timeout=15.0,
        )
    except Exception as exc:  # noqa: BLE001
        return f"Could not fetch token leaderboard: {exc}"

    tokens = _extract_list(body, ("tokens", "data", "results"))
    if not tokens:
        return "No tokens found in the index. Try /leaderboard launchers."

    lines = [f"Top {len(tokens)} Clawnch tokens by 24h volume:", ""]
    for i, tok in enumerate(tokens, start=1):
        lines.append(f"  {i:2d}. {_format_token(tok)}")
    lines.append("")
    lines.append(
        "More views: /leaderboard launchers — top agents · "
        "/leaderboard burners — top $CLAWNCH burners"
    )
    return "\n".join(lines)


def _format_token(tok: dict[str, Any]) -> str:
    """Single-line token rendering for the tokens leaderboard."""
    symbol = tok.get("symbol") or tok.get("ticker") or "?"
    name = tok.get("name") or ""
    price = tok.get("priceUsd") or tok.get("price")
    mc = tok.get("marketCap") or tok.get("fdv")
    vol = tok.get("volume24h") or tok.get("volume24hUsd") or tok.get("volume") or 0
    chg = tok.get("priceChange24h")
    parts = [symbol]
    if name and name != symbol:
        parts.append(f"({name.strip()})")
    if price is not None:
        parts.append(f"${price}")
    if mc:
        parts.append(f"mc {_compact_usd(mc)}")
    if vol:
        parts.append(f"vol {_compact_usd(vol)}")
    if chg is not None:
        sign = "+" if chg >= 0 else ""
        parts.append(f"{sign}{chg:.1f}%")
    return "  ".join(parts)


# ── top launchers by 24h launch count ───────────────────────────────


def _render_launchers(limit: int) -> str:
    try:
        body = http_get(
            f"{_CLAWNCH_API_BASE}/api/launches",
            params={"limit": "200"},
            timeout=15.0,
        )
    except Exception as exc:  # noqa: BLE001
        return f"Could not fetch launches: {exc}"

    launches = _extract_list(body, ("launches", "data", "results"))
    if not launches:
        return "No launches found in the window. Try /leaderboard tokens."

    # Aggregate by agentName. Treat per-source separately by appending
    # source tag so 4claw agents don't collide with clawmes agents.
    counter: Counter[str] = Counter()
    for lau in launches:
        agent = (lau.get("agentName") or lau.get("agent") or "unknown").strip()
        source = lau.get("source") or "unknown"
        key = f"{agent} ({source})"
        counter[key] += 1

    top = counter.most_common(limit)
    lines = [f"Top {len(top)} launchers in the recent window:", ""]
    for i, (agent, count) in enumerate(top, start=1):
        lines.append(f"  {i:2d}. {count:4d} launches  {agent}")
    lines.append("")
    lines.append(
        "More views: /leaderboard tokens — top by volume · "
        "/leaderboard burners — top $CLAWNCH burners"
    )
    return "\n".join(lines)


# ── top burners by $CLAWNCH burned ─────────────────────────────────


def _render_burners(limit: int) -> str:
    """Top wallets by $CLAWNCH burned via the launchpad's burn-payment gate.

    Currently a stub because the on-chain indexing of burn-payment txs
    (keyed by sender wallet) isn't yet exposed via the public API. The
    burn data exists on-chain — we'd need to add an aggregator endpoint
    to ``/api/burns/leaderboard`` or similar — but that's a follow-up.

    For now this surface points at the public burn-tracking page so
    users can see the data, and we ship the command shape so the next
    iteration just fills in the data.
    """
    return (
        "Burner leaderboard — coming soon.\n"
        "\n"
        "Live burn tracking will surface here once the on-chain aggregator\n"
        "ships. In the meantime, the burn address is public and every\n"
        "$CLAWNCH burn transaction is visible on-chain:\n"
        "\n"
        "  Burn address: 0x000000000000000000000000000000000000dEaD\n"
        "  Token:        0xa1F72459dfA10BAD200Ac160eCd78C6b77a747be\n"
        "  Filter:       https://basescan.org/token/0xa1F72459dfA10BAD200Ac160eCd78C6b77a747be"
        "?a=0x000000000000000000000000000000000000dEaD\n"
        "\n"
        "More views: /leaderboard tokens · /leaderboard launchers"
    )


# ── small helpers ───────────────────────────────────────────────────


def _extract_list(body: Any, keys: tuple[str, ...]) -> list[dict[str, Any]]:
    """Tolerate API shape drift: pull a list from any of the known keys."""
    if isinstance(body, list):
        return [x for x in body if isinstance(x, dict)]
    if isinstance(body, dict):
        for key in keys:
            inner = body.get(key)
            if isinstance(inner, list):
                return [x for x in inner if isinstance(x, dict)]
    return []


def _compact_usd(n: float | int | str) -> str:
    """Compact USD format: 42 → $42.00, 1500 → $1.5k, 2.5M → $2.50M, 7.3B → $7.30B."""
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


def register(ctx) -> None:
    ctx.register_command(
        name="leaderboard",
        handler=handle_leaderboard,
        description="Top tokens / launchers / burners on Clawnch",
        args_hint="[tokens | launchers | burners] [limit]",
    )
