"""DexScreener — public token + pair search.

Thin wrapper over the DexScreener HTTP API (no auth required) used to:

  * resolve a user-typed symbol (``MNEME``) to an on-chain token
    address on a specific chain (``find_token``), and
  * list the top pairs on a chain by 24h volume (``top_pairs``) — the
    backbone of ``/trending --all``.

We intentionally don't build a stateful Service class for DexScreener
because there's no lifecycle, no auth, and no shared connection
state — every read is a one-shot HTTP GET. Lives in ``lib/`` (helper)
instead of ``services/`` (lifecycle-bound).

Endpoints used:

  * ``GET /latest/dex/search?q=<query>`` — returns pairs across all
    chains ranked by 24h volume. Free-form (matches token symbol,
    name, or contract). The first Base pair (by default volume sort)
    is usually the canonical liquidity venue.
  * ``GET /latest/dex/tokens/<address>`` — returns all pairs for a
    given token address. We use this when the caller already has an
    address and just wants the canonical pair / market data.

Response shape (relevant fields):

    {
      "schemaVersion": "1.0.0",
      "pairs": [
        {
          "chainId": "base",
          "dexId": "uniswap",
          "url": "https://dexscreener.com/...",
          "pairAddress": "0x...",
          "baseToken": {"address": "0x...", "name": "...", "symbol": "..."},
          "quoteToken": {...},
          "priceUsd": "0.0000132",
          "fdv": 1300000,
          "marketCap": 1300000,
          "volume": {"h24": 55000, ...},
          "txns": {"h24": {"buys": 42, "sells": 38}, ...},
          ...
        },
        ...
      ]
    }

Errors: all helpers return empty lists / ``None`` on upstream
failures rather than raising — surface commands render "no results"
rather than tracebacks. Callers that need failure visibility can
inspect ``last_error()``.
"""

from __future__ import annotations

import threading
from typing import Any

from clawmes.lib.http import http_get

_BASE_URL = "https://api.dexscreener.com"
_DEFAULT_CHAIN = "base"
_DEFAULT_TIMEOUT = 15.0

# Last upstream error stored for optional ``last_error()`` inspection by
# callers that want to surface a diagnostic. We don't raise on errors so
# that the command surface stays "no results" friendly.
_last_error: str | None = None
_err_lock = threading.RLock()


def last_error() -> str | None:
    """Return the most recent upstream error message, or ``None``."""
    with _err_lock:
        return _last_error


def _set_error(msg: str | None) -> None:
    global _last_error
    with _err_lock:
        _last_error = msg


def _request(path: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """One-shot GET. Returns parsed JSON or ``None`` on any failure."""
    url = _BASE_URL + path
    try:
        body = http_get(url, params=params or {}, timeout=_DEFAULT_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 — record + swallow
        _set_error(f"{type(exc).__name__}: {exc}")
        return None
    _set_error(None)
    return body if isinstance(body, dict) else None


def search(query: str) -> list[dict[str, Any]]:
    """Raw cross-chain search. Returns the ``pairs`` array (may be []).

    Use ``find_token`` / ``top_pairs`` instead unless you need the raw
    multi-chain pair list.
    """
    if not query or not query.strip():
        return []
    body = _request("/latest/dex/search", {"q": query.strip()})
    if not body:
        return []
    pairs = body.get("pairs") or []
    return [p for p in pairs if isinstance(p, dict)]


def find_token(
    symbol_or_address: str,
    *,
    chain: str = _DEFAULT_CHAIN,
) -> dict[str, Any] | None:
    """Resolve a symbol or address to the canonical pair on ``chain``.

    Selection rule:

      * If ``symbol_or_address`` looks like an ``0x``-prefixed address
        (40 hex chars), query ``/latest/dex/tokens/<address>`` directly
        and return the highest-volume Base pair.
      * Otherwise treat as a symbol — do a cross-chain search and pick
        the first pair on ``chain`` whose ``baseToken.symbol`` matches
        case-insensitively. If no exact symbol match, fall back to the
        first pair on the chain (volume-sorted upstream).

    Returns ``None`` when nothing matches.
    """
    if not symbol_or_address or not symbol_or_address.strip():
        return None
    q = symbol_or_address.strip()
    if _looks_like_address(q):
        body = _request(f"/latest/dex/tokens/{q}")
        if not body:
            return None
        pairs = body.get("pairs") or []
        chain_pairs = [p for p in pairs if isinstance(p, dict) and p.get("chainId") == chain]
        return chain_pairs[0] if chain_pairs else None

    # Symbol search — cross-chain.
    pairs = search(q)
    chain_pairs = [p for p in pairs if p.get("chainId") == chain]
    if not chain_pairs:
        return None
    upper = q.upper()
    # Prefer exact symbol matches on baseToken to disambiguate (e.g. avoid
    # picking a quoteToken=USDC pair when the user wanted to buy USDC).
    for p in chain_pairs:
        base = p.get("baseToken") or {}
        if str(base.get("symbol", "")).upper() == upper:
            return p
    return chain_pairs[0]


def top_pairs(
    *,
    chain: str = _DEFAULT_CHAIN,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Top pairs on ``chain`` by 24h volume.

    DexScreener doesn't expose a "trending by chain" endpoint, but the
    search endpoint returns volume-sorted pairs across all chains. We
    seed the search with the chain id (``"base"``) and filter, which
    yields the same shape as a top-by-volume list.

    For a more curated set, callers should fall back to the on-chain
    Clawnch tokens endpoint (``--clawnch``) which is volume-sorted by
    the launchpad backend.
    """
    if limit <= 0:
        return []
    pairs = search(chain)
    chain_pairs = [p for p in pairs if p.get("chainId") == chain]
    return chain_pairs[:limit]


def _looks_like_address(value: str) -> bool:
    """Heuristic: ``0x`` + 40 hex chars."""
    if not value.startswith("0x"):
        return False
    body = value[2:]
    if len(body) != 40:
        return False
    return all(c in "0123456789abcdefABCDEF" for c in body)


def format_pair_summary(pair: dict[str, Any]) -> str:
    """Human-friendly one-liner for a pair.

    Example:
        ``MNEME  $0.0000132  mc $1.3M  vol24h $55k  → 0x3FcD…7b07``

    Used by ``/trending`` and ``/buy`` for the confirmation render.
    """
    base = pair.get("baseToken") or {}
    symbol = base.get("symbol") or "?"
    addr = base.get("address") or "?"
    price_usd = pair.get("priceUsd")
    mc = pair.get("marketCap") or pair.get("fdv")
    vol24 = ((pair.get("volume") or {}).get("h24")) or 0
    parts = [symbol]
    if price_usd is not None:
        parts.append(f"${price_usd}")
    if mc:
        parts.append(f"mc {_compact_usd(mc)}")
    if vol24:
        parts.append(f"vol24h {_compact_usd(vol24)}")
    parts.append(f"→ {_short_addr(addr)}")
    return "  ".join(parts)


def _compact_usd(n: float | int | str) -> str:
    """Format a USD figure compactly: 1.3k, 55k, 1.3M, 2.4B."""
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


def _short_addr(addr: str) -> str:
    """Truncate ``0xabc…def`` for display."""
    if not addr or len(addr) < 10:
        return addr
    return f"{addr[:6]}…{addr[-4:]}"
