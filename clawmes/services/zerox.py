"""0x Swap API v2 client.

The 0x API is the canonical aggregator for Ethereum-family swaps —
queries dozens of liquidity sources (Uniswap V2/V3/V4, Balancer,
Curve, Sushiswap, etc.) and returns the best route. Used by hundreds
of wallets including MetaMask, Coinbase Wallet, and Rainbow.

Endpoints we consume:

  * ``GET /swap/v1/price`` — read-only price + gas estimate
    (no allowance commitment, no permit2 signature).
  * ``GET /swap/v1/quote`` — full quote with calldata + permit2 EIP-712
    payload. The user signs the permit2 and we broadcast the swap.

API key: required for production traffic. ``ZEROX_API_KEY`` env var.
The free tier allows ~30 req/min unauthenticated; rate-limit errors
surface with code ``rate_limited``.

Why 0x over 1inch/LiFi: 0x has the cleanest permit2 integration,
which is also clawmes' default approval mechanism. 1inch and LiFi
land in subsequent commits as alternative backends with the same
quote shape (route comparison via ``defi_swap.route``).
"""

from __future__ import annotations

import os
import threading
from typing import Any

from clawmes.lib.http import http_get
from clawmes.lib.logger import logger_for
from clawmes.services._base import Service

_log = logger_for("services.zerox")

# Per-chain 0x API base URLs. 0x serves a single API surface across all
# supported chains; chain selection is by the ``chainId`` query param.
_BASE_URL = "https://api.0x.org"

# Chains 0x supports. Keep in sync with chain_ids() — adding a chain
# here without RPC support means quotes work but swaps would fail.
_SUPPORTED_CHAIN_IDS: frozenset[int] = frozenset({1, 8453, 42161, 10, 137})


class ZeroxError(RuntimeError):
    """Raised on 0x API failures.

    ``code`` classification:
      * ``no_credentials`` — ZEROX_API_KEY not set (production traffic
        only; free tier still functions but rate-limits aggressively).
      * ``unsupported_chain`` — chain not in 0x's supported set.
      * ``rate_limited`` — HTTP 429.
      * ``insufficient_liquidity`` — the requested route has no path.
      * ``api_error`` — generic upstream failure.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ZeroxService(Service):
    id = "clawmes.zerox"

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._api_key: str | None = None

    def start(self) -> None:
        with self._lock:
            self._api_key = os.environ.get("ZEROX_API_KEY")
        _log.info(
            "0x service started (auth=%s)",
            "key" if self._api_key else "free-tier",
        )

    def stop(self) -> None:
        with self._lock:
            self._api_key = None

    def supports_chain(self, chain_id: int) -> bool:
        return chain_id in _SUPPORTED_CHAIN_IDS

    def get_price(
        self,
        *,
        chain_id: int,
        sell_token: str,
        buy_token: str,
        sell_amount: int | None = None,
        buy_amount: int | None = None,
        taker: str | None = None,
        slippage_bps: int = 100,
    ) -> dict[str, Any]:
        """Read-only price query — no allowance commitment.

        Exactly one of ``sell_amount`` / ``buy_amount`` must be provided.
        ``slippage_bps`` is integer basis points (100 = 1%).

        Returns the raw 0x price response. Useful for ``defi_swap.quote``
        action where we want a price-only preview without committing
        the user to a specific permit2 signature.
        """
        if not self.supports_chain(chain_id):
            raise ZeroxError("unsupported_chain", f"0x does not support chain {chain_id}")
        if (sell_amount is None) == (buy_amount is None):
            raise ZeroxError(
                "api_error",
                "exactly one of sell_amount / buy_amount must be provided",
            )

        params: dict[str, Any] = {
            "chainId": str(chain_id),
            "sellToken": sell_token,
            "buyToken": buy_token,
            "slippageBps": str(slippage_bps),
        }
        if sell_amount is not None:
            params["sellAmount"] = str(sell_amount)
        else:
            params["buyAmount"] = str(buy_amount)
        if taker is not None:
            params["taker"] = taker

        return self._call("/swap/permit2/price", params)

    def get_quote(
        self,
        *,
        chain_id: int,
        sell_token: str,
        buy_token: str,
        taker: str,
        sell_amount: int | None = None,
        buy_amount: int | None = None,
        slippage_bps: int = 100,
    ) -> dict[str, Any]:
        """Full swap quote with calldata + permit2 EIP-712 payload.

        Unlike ``get_price``, this commits the user to a specific
        permit2 signature. Should only be called when the user has
        confirmed they want to execute the swap.

        ``taker`` (the wallet's address) is required because the
        permit2 nonce + EIP-712 domain are bound to the taker.
        """
        if not self.supports_chain(chain_id):
            raise ZeroxError("unsupported_chain", f"0x does not support chain {chain_id}")
        if (sell_amount is None) == (buy_amount is None):
            raise ZeroxError(
                "api_error",
                "exactly one of sell_amount / buy_amount must be provided",
            )

        params: dict[str, Any] = {
            "chainId": str(chain_id),
            "sellToken": sell_token,
            "buyToken": buy_token,
            "taker": taker,
            "slippageBps": str(slippage_bps),
        }
        if sell_amount is not None:
            params["sellAmount"] = str(sell_amount)
        else:
            params["buyAmount"] = str(buy_amount)

        return self._call("/swap/permit2/quote", params)

    def _call(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            api_key = self._api_key

        headers = {"0x-version": "v2"}
        if api_key:
            headers["0x-api-key"] = api_key

        url = _BASE_URL + path
        try:
            response = http_get(url, params=params, headers=headers, timeout=20.0)
        except Exception as exc:  # noqa: BLE001 — classify below
            msg = str(exc).lower()
            if "429" in msg or "rate" in msg:
                raise ZeroxError("rate_limited", str(exc)) from exc
            raise ZeroxError("api_error", f"0x request failed: {exc}") from exc

        if not isinstance(response, dict):
            raise ZeroxError(
                "api_error",
                f"0x returned non-dict response: {type(response).__name__}",
            )
        # 0x error envelope: {"name": ..., "reason": ...} or {"validationErrors": [...]}
        if "name" in response and "reason" in response:
            reason = str(response.get("reason", ""))
            code = "insufficient_liquidity" if "no liquidity" in reason.lower() else "api_error"
            raise ZeroxError(code, f"0x {response['name']}: {reason}")
        return response


_instance: ZeroxService | None = None


def get_zerox_service() -> ZeroxService:
    global _instance
    if _instance is None:
        _instance = ZeroxService()
    return _instance
