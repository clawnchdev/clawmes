"""CoinGecko HTTP client.

Free-tier compatible. Calls ``api.coingecko.com/api/v3/simple/price`` with
optional ``COINGECKO_API_KEY`` (Pro tier — adds the ``x-cg-pro-api-key``
header and bumps rate limits).

Caching: in-memory TTL cache keyed on ``(token_id, vs_currency)``.
Default TTL 30s — short enough that price-trigger evaluations stay
responsive, long enough that bursty tool calls don't spam the API.

Token IDs: CoinGecko uses lowercase IDs (``"ethereum"``, ``"bitcoin"``,
``"usd-coin"``). The router in :mod:`clawmes.services.price` does the
``ETH`` → ``ethereum`` resolution; this client takes whatever the caller
hands it.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass

from clawmes.lib.http import http_get
from clawmes.lib.logger import logger_for
from clawmes.services._base import Service

_log = logger_for("services.coingecko")

_BASE_URL = "https://api.coingecko.com/api/v3"
_DEFAULT_TTL_SECONDS = 30


@dataclass(frozen=True)
class _CacheEntry:
    value: float
    expires_at: float


class CoinGeckoService(Service):
    id = "clawmes.coingecko"

    def __init__(self, *, ttl_seconds: int = _DEFAULT_TTL_SECONDS) -> None:
        self._lock = threading.RLock()
        self._cache: dict[tuple[str, str], _CacheEntry] = {}
        self._ttl_seconds = ttl_seconds
        self._api_key: str | None = None

    def start(self) -> None:
        # Pull from env at start; cached for service lifetime so a key
        # rotation needs a process restart (matches Hermes' env model).
        self._api_key = os.environ.get("COINGECKO_API_KEY") or None
        _log.info(
            "coingecko service started (auth=%s, ttl=%ds)",
            "pro" if self._api_key else "free",
            self._ttl_seconds,
        )

    def stop(self) -> None:
        with self._lock:
            self._cache.clear()

    def get_price(self, token_id: str, vs_currency: str = "usd") -> float | None:
        """Single-token spot price. Returns None on miss / error."""
        prices = self.get_prices([token_id], vs_currency)
        return prices.get(token_id)

    def get_prices(
        self,
        token_ids: list[str],
        vs_currency: str = "usd",
    ) -> dict[str, float]:
        """Multi-token spot prices. Returns a dict keyed by token_id.

        Tokens missing from the response (typo, delisted) are simply
        absent from the returned dict — callers should not assume every
        requested ID is present.
        """
        if not token_ids:
            return {}

        vs_currency = vs_currency.lower()
        now = time.monotonic()

        # Partition into cache-hit and miss
        hits: dict[str, float] = {}
        misses: list[str] = []
        with self._lock:
            for tid in token_ids:
                key = (tid.lower(), vs_currency)
                entry = self._cache.get(key)
                if entry and entry.expires_at > now:
                    hits[tid] = entry.value
                else:
                    misses.append(tid)

        if not misses:
            return hits

        try:
            fetched = self._fetch(misses, vs_currency)
        except Exception:  # noqa: BLE001 — defensive; price is read-only, never break tools
            _log.exception("coingecko fetch failed for %s", misses)
            return hits

        # Merge + cache
        with self._lock:
            for tid, price in fetched.items():
                self._cache[(tid.lower(), vs_currency)] = _CacheEntry(
                    value=price, expires_at=now + self._ttl_seconds
                )
        hits.update(fetched)
        return hits

    def get_market_chart(
        self, token_id: str, *, vs_currency: str = "usd", days: int = 30
    ) -> dict[str, list[list[float]]]:
        """Historical OHLC-like data: prices, market_caps, total_volumes.

        Returns the raw CoinGecko payload — three arrays of
        ``[timestamp_ms, value]`` pairs. ``days`` controls granularity:
        1 → 5-min, 2-90 → hourly, 91+ → daily. Used by ``analytics``
        for technical indicators.
        """
        url = f"{_BASE_URL}/coins/{token_id.lower()}/market_chart"
        params = {
            "vs_currency": vs_currency.lower(),
            "days": str(days),
        }
        headers: dict[str, str] = {}
        if self._api_key:
            headers["x-cg-pro-api-key"] = self._api_key
        result = http_get(url, params=params, headers=headers, timeout=15.0)
        if not isinstance(result, dict):
            return {"prices": [], "market_caps": [], "total_volumes": []}
        return result

    def _fetch(self, token_ids: list[str], vs_currency: str) -> dict[str, float]:
        url = f"{_BASE_URL}/simple/price"
        params = {
            "ids": ",".join(t.lower() for t in token_ids),
            "vs_currencies": vs_currency,
        }
        headers: dict[str, str] = {}
        if self._api_key:
            headers["x-cg-pro-api-key"] = self._api_key

        data = http_get(url, params=params, headers=headers, timeout=15.0)
        if not isinstance(data, dict):
            _log.warning("coingecko returned non-dict: %r", type(data))
            return {}

        out: dict[str, float] = {}
        for tid, prices in data.items():
            if not isinstance(prices, dict):
                continue
            value = prices.get(vs_currency)
            if isinstance(value, (int, float)):
                out[tid] = float(value)
        return out


_instance: CoinGeckoService | None = None


def get_coingecko_service() -> CoinGeckoService:
    global _instance
    if _instance is None:
        _instance = CoinGeckoService()
    return _instance
