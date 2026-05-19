"""BV-7X autonomous Bitcoin signal oracle client.

BV-7X is a clawnch-ecosystem project — the ``$BV7X`` token was
launched on the Clawnch launchpad. The agent publishes daily BTC
predictions (signal at 21:35 UTC), creates EAS attestations on Base,
operates a Polymarket wager bot via Bankr, and exposes a public REST
API + A2A + MCP surface.

This service wraps every **public** (unauthenticated) endpoint
clawmes users might want. Token-gated endpoints (``/oracle``,
``/oracle/premium``, ``/copy-trade/{next,history}``) require the
caller to hold ``$BV7X`` and complete a wallet-signature verify
flow at ``/api/bv7x/oracle/verify`` — that verify flow's exact
shape isn't publicly documented yet, so wiring it lives in a
follow-up PR. Users who want those endpoints today can call them
directly with their own session token via ``BV7X_API_KEY``
(forwarded as a ``Bearer`` Authorization header).

Public endpoints exposed:

  * Market data
    - ``GET /api/btc-price`` — BTC price + 24h change.
    - ``GET /api/fear-greed`` — Bitcoin Fear & Greed Index.
    - ``GET /api/etf-flows`` — Bitcoin ETF flow data (7d + 30d).
    - ``GET /api/bv7x/regime`` — market regime classification.
    - ``GET /api/bv7x/openclaw/signal`` — signal metadata (direction
      gated for non-holders; everything else is free).
  * Track record
    - ``GET /api/bv7x/scorecard?horizon=N`` — prediction history,
      accuracy, streak.
  * On-chain attestation oracle
    - ``GET /api/bv7x/onchain-oracle/latest`` — latest attestation.
    - ``GET /api/bv7x/onchain-oracle/history`` — paginated history.
    - ``GET /api/bv7x/onchain-oracle/stats`` — aggregate stats.
    - ``GET /api/bv7x/onchain-oracle/verify/{uid}`` — verify by UID.
  * Agent / A2A / commerce
    - ``GET /api/bv7x/agent/identity`` — ERC-8004 identity.
    - ``GET /api/bv7x/agent/reputation`` — agent reputation score.
    - ``GET /api/bv7x/a2a/discover`` — A2A skill card.
    - ``GET /api/bv7x/a2a/tasks/{id}`` — A2A task status.
    - ``GET /api/bv7x/commerce/offerings`` — commerce offerings list.
    - ``GET /api/bv7x/copy-trade/status`` — copy-trade service status.

Premium / gated calls (auto-Bearer when ``BV7X_API_KEY`` is set):

  * ``GET /api/bv7x/oracle`` — full signal (500M ``$BV7X``).
  * ``GET /api/bv7x/oracle/premium`` — full breakdown (1B ``$BV7X``).
  * ``GET /api/bv7x/copy-trade/next`` — next trade intent (1B ``$BV7X``).
  * ``GET /api/bv7x/copy-trade/history`` — trade history (1B ``$BV7X``).

A 60-second cache keeps the regime / scorecard / identity reads cheap
across multiple tool calls in the same agent turn.
"""

from __future__ import annotations

import os
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
      * ``rate_limited``    — HTTP 429.
      * ``not_found``       — HTTP 404 (e.g. unknown attestation UID).
      * ``token_gated``     — endpoint requires $BV7X holdings + the
        wallet-verify flow. Set ``BV7X_API_KEY`` once you have a
        session token from ``/api/bv7x/oracle/verify``.
      * ``no_credentials``  — token-gated endpoint called without
        ``BV7X_API_KEY`` set.
      * ``api_error``       — generic upstream failure.
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
        self._api_key: str | None = None

    def start(self) -> None:
        with self._lock:
            self._api_key = os.environ.get("BV7X_API_KEY") or None
        _log.info(
            "bv7x service started (auth=%s)",
            "key" if self._api_key else "public-only",
        )

    def stop(self) -> None:
        with self._lock:
            self._cache.clear()
            self._api_key = None

    # --- public market data ---------------------------------------------

    def get_btc_price(self) -> dict[str, Any]:
        return self._cached_call("/api/btc-price")

    def get_fear_greed(self) -> dict[str, Any]:
        return self._cached_call("/api/fear-greed")

    def get_etf_flows(self) -> dict[str, Any]:
        return self._cached_call("/api/etf-flows")

    def get_regime(self) -> dict[str, Any]:
        return self._cached_call("/api/bv7x/regime")

    def get_signal_metadata(self, horizon: str = "7d") -> dict[str, Any]:
        """Public signal metadata. The ``signal`` field is GATED unless
        an ``BV7X_API_KEY`` is set; the rest (price, fear_greed,
        etf_flow_7d, model_version) is always free.
        """
        return self._cached_call(f"/api/bv7x/openclaw/signal?horizon={horizon}")

    # --- track record ----------------------------------------------------

    def get_scorecard(self, horizon: int = 7) -> dict[str, Any]:
        return self._cached_call(f"/api/bv7x/scorecard?horizon={horizon}")

    # --- on-chain attestation oracle ------------------------------------

    def get_onchain_latest(self) -> dict[str, Any]:
        return self._cached_call("/api/bv7x/onchain-oracle/latest")

    def get_onchain_history(self, limit: int = 10) -> dict[str, Any]:
        return self._cached_call(f"/api/bv7x/onchain-oracle/history?limit={limit}")

    def get_onchain_stats(self) -> dict[str, Any]:
        return self._cached_call("/api/bv7x/onchain-oracle/stats")

    def verify_onchain_attestation(self, uid: str) -> dict[str, Any]:
        """Verify an attestation by 32-byte UID via BV-7X's verifier.
        Independent of clawmes' own ``eas_attestation`` tool which goes
        directly to the EAS contract on Base.
        """
        return self._call(f"/api/bv7x/onchain-oracle/verify/{uid}")

    # --- agent + A2A + commerce ----------------------------------------

    def get_agent_identity(self) -> dict[str, Any]:
        return self._cached_call("/api/bv7x/agent/identity")

    def get_agent_reputation(self) -> dict[str, Any]:
        return self._cached_call("/api/bv7x/agent/reputation")

    def discover_a2a(self) -> dict[str, Any]:
        return self._cached_call("/api/bv7x/a2a/discover")

    def get_a2a_task(self, task_id: str) -> dict[str, Any]:
        return self._call(f"/api/bv7x/a2a/tasks/{task_id}")

    def get_commerce_offerings(self) -> dict[str, Any]:
        return self._cached_call("/api/bv7x/commerce/offerings")

    def get_copy_trade_status(self) -> dict[str, Any]:
        return self._cached_call("/api/bv7x/copy-trade/status")

    # --- token-gated (auto-Bearer when BV7X_API_KEY set) ----------------

    def get_oracle(self) -> dict[str, Any]:
        return self._call("/api/bv7x/oracle", require_auth=True)

    def get_oracle_premium(self) -> dict[str, Any]:
        return self._call("/api/bv7x/oracle/premium", require_auth=True)

    def get_copy_trade_next(self) -> dict[str, Any]:
        return self._call("/api/bv7x/copy-trade/next", require_auth=True)

    def get_copy_trade_history(self) -> dict[str, Any]:
        return self._call("/api/bv7x/copy-trade/history", require_auth=True)

    # --- cache control --------------------------------------------------

    def clear_cache(self) -> None:
        with self._lock:
            self._cache.clear()

    def has_api_key(self) -> bool:
        with self._lock:
            return self._api_key is not None

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

    def _call(self, path: str, *, require_auth: bool = False) -> dict[str, Any]:
        with self._lock:
            api_key = self._api_key

        if require_auth and not api_key:
            raise BV7XError(
                "no_credentials",
                f"{path} requires $BV7X holdings + a session token. Set "
                "BV7X_API_KEY in ~/.hermes/.env after completing the "
                "wallet-verify flow at https://bv7x.ai/terminal#developer.",
            )

        url = _BASE_URL + path
        headers: dict[str, str] = {}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        try:
            response = http_get(url, headers=headers or None, timeout=15.0)
        except Exception as exc:  # noqa: BLE001 — classify below
            msg = str(exc).lower()
            if "429" in msg or "rate" in msg:
                raise BV7XError("rate_limited", str(exc)) from exc
            if "404" in msg or "not found" in msg:
                raise BV7XError("not_found", str(exc)) from exc
            if "401" in msg or "unauthor" in msg or "authentication" in msg:
                raise BV7XError(
                    "token_gated",
                    f"{path} returned 401 — BV7X_API_KEY may be invalid or "
                    "expired. Re-run the wallet-verify flow.",
                ) from exc
            if "402" in msg or "payment required" in msg or "token gate" in msg:
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
