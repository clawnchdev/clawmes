"""LiFi cross-chain bridge aggregator client.

LiFi (li.quest) aggregates 30+ bridge providers (Stargate, Hop,
Connext, Across, etc.) and routes cross-chain swaps through the
cheapest / fastest path. Used by Jumper, Rabby, and most modern
EVM wallets for cross-chain UX.

Endpoints we consume:

  * ``POST /v1/quote``  — get a bridge route. Returns calldata
    bound to the requested ``fromAddress`` plus expected output,
    fees, and tool list.
  * ``GET /v1/status``  — track a bridge transaction across the
    source and destination chain transitions.
  * ``GET /v1/connections`` — list supported chain pairs (for the
    routes action's "what's possible" UX).

API key: optional via ``LIFI_API_KEY`` env. Free tier supports ~30
req/min; production traffic should obtain a key from li.quest.
"""

from __future__ import annotations

import os
import threading
from typing import Any

from clawmes.lib.http import http_get
from clawmes.lib.logger import logger_for
from clawmes.services._base import Service

_log = logger_for("services.lifi")

_BASE_URL = "https://li.quest"


class LifiError(RuntimeError):
    """Raised on LiFi API failures with a typed code.

    ``code`` classification:
      * ``no_route`` — LiFi can't find a path for the requested pair.
      * ``unsupported`` — chain or token not in LiFi's universe.
      * ``rate_limited`` — HTTP 429.
      * ``api_error`` — generic upstream failure.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class LifiService(Service):
    id = "clawmes.lifi"

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._api_key: str | None = None

    def start(self) -> None:
        with self._lock:
            self._api_key = os.environ.get("LIFI_API_KEY")
        _log.info(
            "lifi service started (auth=%s)",
            "key" if self._api_key else "free-tier",
        )

    def stop(self) -> None:
        with self._lock:
            self._api_key = None

    def get_quote(
        self,
        *,
        from_chain: int,
        to_chain: int,
        from_token: str,
        to_token: str,
        from_amount: int,
        from_address: str,
        to_address: str | None = None,
        slippage: float = 0.005,
    ) -> dict[str, Any]:
        """Get a bridge route from LiFi.

        ``slippage`` is the fractional tolerance (0.005 = 0.5%).
        ``to_address`` defaults to ``from_address``. The returned
        dict carries the canonical LiFi route shape: ``transactionRequest``
        (calldata + value + gas), ``estimate`` (output amount + fees),
        ``includedSteps`` (the actual bridge tools used), and ``id``.
        """
        params: dict[str, Any] = {
            "fromChain": str(from_chain),
            "toChain": str(to_chain),
            "fromToken": from_token,
            "toToken": to_token,
            "fromAmount": str(from_amount),
            "fromAddress": from_address,
            "slippage": str(slippage),
        }
        if to_address is not None:
            params["toAddress"] = to_address

        return self._call_get("/v1/quote", params)

    def get_status(self, *, tx_hash: str, bridge: str | None = None) -> dict[str, Any]:
        """Track a bridge tx through source + destination confirmation.

        ``bridge`` is optional — LiFi can usually derive it from the
        tx hash, but supplying it (e.g. ``stargate``, ``across``)
        speeds up the lookup.
        """
        params: dict[str, Any] = {"txHash": tx_hash}
        if bridge is not None:
            params["bridge"] = bridge
        return self._call_get("/v1/status", params)

    def get_connections(
        self, *, from_chain: int | None = None, to_chain: int | None = None
    ) -> dict[str, Any]:
        """List supported chain → chain pairs.

        Pass ``from_chain`` / ``to_chain`` to filter; without filters,
        returns the full matrix (large). Used by ``bridge.routes``.
        """
        params: dict[str, Any] = {}
        if from_chain is not None:
            params["fromChain"] = str(from_chain)
        if to_chain is not None:
            params["toChain"] = str(to_chain)
        return self._call_get("/v1/connections", params)

    # --- internals ---

    def _headers(self) -> dict[str, str]:
        with self._lock:
            api_key = self._api_key
        h = {"Accept": "application/json"}
        if api_key:
            h["x-lifi-api-key"] = api_key
        return h

    def _call_get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        url = _BASE_URL + path
        try:
            response = http_get(url, params=params, headers=self._headers(), timeout=20.0)
        except Exception as exc:  # noqa: BLE001 — classify below
            return self._classify_failure(exc)

        return self._validate_response(response)

    def _classify_failure(self, exc: Exception) -> dict[str, Any]:
        msg = str(exc).lower()
        if "429" in msg or "rate" in msg:
            raise LifiError("rate_limited", str(exc)) from exc
        raise LifiError("api_error", f"LiFi request failed: {exc}") from exc

    def _validate_response(self, response: Any) -> dict[str, Any]:
        if not isinstance(response, dict):
            raise LifiError(
                "api_error",
                f"LiFi returned non-dict response: {type(response).__name__}",
            )
        # LiFi error envelope
        if "message" in response and "code" in response and "type" in response:
            error_msg = str(response.get("message", ""))
            lower = error_msg.lower()
            if "no route" in lower or "no available route" in lower:
                code = "no_route"
            elif "unsupported" in lower or "not supported" in lower:
                code = "unsupported"
            else:
                code = "api_error"
            raise LifiError(code, error_msg)
        return response


_instance: LifiService | None = None


def get_lifi_service() -> LifiService:
    global _instance
    if _instance is None:
        _instance = LifiService()
    return _instance
