"""BV-7X public REST oracle client.

BV-7X is an autonomous Bitcoin signal oracle that publishes daily
predictions (signal at 21:35 UTC), creates EAS attestations on Base,
and operates a Polymarket wager bot. We wrap their public REST
endpoints — the token-gated premium endpoints (Oracle, Copy-Trade)
are intentionally NOT exposed here. Users who hold ``$BV7X`` and want
those can hit the endpoints directly with their own credentials.

Public endpoints covered:

  * ``GET /api/bv7x/regime`` — current market regime + thresholds.
  * ``GET /api/bv7x/agent/identity`` — ERC-8004 agent identity + reputation.
  * ``GET /api/bv7x/a2a/discover`` — A2A discovery card (skill list).

Why a service vs. just inlining HTTP calls: the bv7x oracle publishes
daily — a service lets us cache the regime and identity results across
multiple tool calls in the same agent turn without hammering the
upstream. Cache TTL defaults to 60s.

Skip:
  * ``/oracle`` / ``/oracle/premium`` / ``/copy-trade/*`` — token-gated.
    We refuse to bake clawmes dependencies on third-party token
    holdings.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from clawmes.lib.http import http_get
from clawmes.lib.logger import logger_for
from clawmes.services._base import Service

_log = logger_for("services.bv7x")

_BASE_URL = "https://bv7x.ai"
_DEFAULT_TTL_SECONDS = 60


class BV7XError(RuntimeError):
    """Raised on BV-7X API failures.

    ``code`` classification:
      * ``rate_limited`` — HTTP 429.
      * ``not_found``    — HTTP 404 (e.g. unknown attestation UID).
      * ``token_gated``  — endpoint requires $BV7X holdings; we don't
        attempt these but surface a clear error if a caller tries.
      * ``api_error``    — generic upstream failure.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class BV7XService(Service):
    id = "clawmes.bv7x"

    def __init__(self, *, ttl_seconds: int = _DEFAULT_TTL_SECONDS) -> None:
        self._lock = threading.RLock()
        self._ttl = ttl_seconds
        self._cache: dict[str, tuple[float, Any]] = {}

    def start(self) -> None:
        pass

    def stop(self) -> None:
        with self._lock:
            self._cache.clear()

    def get_regime(self) -> dict[str, Any]:
        """Return current BV-7X regime classification + thresholds."""
        return self._cached_call("/api/bv7x/regime")

    def get_agent_identity(self) -> dict[str, Any]:
        """Return BV-7X's ERC-8004 agent identity + reputation."""
        return self._cached_call("/api/bv7x/agent/identity")

    def discover_a2a(self) -> dict[str, Any]:
        """Return BV-7X's A2A discovery card (skill list)."""
        return self._cached_call("/api/bv7x/a2a/discover")

    def clear_cache(self) -> None:
        with self._lock:
            self._cache.clear()

    # --- internal --------------------------------------------------------

    def _cached_call(self, path: str) -> dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            entry = self._cache.get(path)
            if entry is not None:
                ts, value = entry
                if now - ts < self._ttl:
                    return value

        value = self._call(path)
        with self._lock:
            self._cache[path] = (now, value)
        return value

    def _call(self, path: str) -> dict[str, Any]:
        url = _BASE_URL + path
        try:
            response = http_get(url, timeout=15.0)
        except Exception as exc:  # noqa: BLE001 — classify below
            msg = str(exc).lower()
            if "429" in msg or "rate" in msg:
                raise BV7XError("rate_limited", str(exc)) from exc
            if "404" in msg or "not found" in msg:
                raise BV7XError("not_found", str(exc)) from exc
            if "402" in msg or "payment required" in msg or "token" in msg:
                raise BV7XError("token_gated", str(exc)) from exc
            raise BV7XError("api_error", f"bv7x request failed: {exc}") from exc

        if not isinstance(response, dict):
            raise BV7XError(
                "api_error",
                f"bv7x returned non-dict response: {type(response).__name__}",
            )
        return response


_instance: BV7XService | None = None


def get_bv7x_service() -> BV7XService:
    global _instance
    if _instance is None:
        _instance = BV7XService()
    return _instance
