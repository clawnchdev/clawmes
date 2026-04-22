"""Price router service.

Fans out price queries across configured backend services. v0.1.0 ships
with CoinGecko only; future commits add DexScreener (preferred for
on-chain DEX prices), Chainlink (oracle-grade), and DeFiLlama
(yield-context). The router does the symbol resolution (``ETH`` →
``ethereum`` for CoinGecko) and the failover.

Public surface:
  * :func:`get_price_service` — singleton accessor
  * :meth:`PriceService.get_price` — single-token spot
  * :meth:`PriceService.get_prices` — multi-token spot
"""

from __future__ import annotations

import threading

from clawmes.lib.logger import logger_for
from clawmes.services._base import Service
from clawmes.services.coingecko import get_coingecko_service

_log = logger_for("services.price")


# Symbol → CoinGecko ID. Curated, common subset; extend in config or via a
# pluggable resolver service later. Lowercase keys.
SYMBOL_TO_CG_ID: dict[str, str] = {
    "eth": "ethereum",
    "weth": "weth",
    "btc": "bitcoin",
    "wbtc": "wrapped-bitcoin",
    "matic": "matic-network",
    "usdc": "usd-coin",
    "usdt": "tether",
    "dai": "dai",
    "arb": "arbitrum",
    "op": "optimism",
    "base": "base",
    "sol": "solana",
    "ldo": "lido-dao",
    "rpl": "rocket-pool",
    "uni": "uniswap",
    "aave": "aave",
    "comp": "compound-governance-token",
    "crv": "curve-dao-token",
    "mkr": "maker",
    "snx": "havven",
    "yfi": "yearn-finance",
    "pendle": "pendle",
    "link": "chainlink",
}


def resolve_symbol(symbol: str) -> str:
    """Map a common ticker to its CoinGecko ID; pass through if unknown."""
    return SYMBOL_TO_CG_ID.get(symbol.strip().lower(), symbol.strip().lower())


class PriceService(Service):
    id = "clawmes.price"

    def __init__(self) -> None:
        self._lock = threading.RLock()

    def start(self) -> None:
        # CoinGecko service is started independently — no extra work here
        _log.info("price router started (backend: coingecko)")

    def stop(self) -> None:
        pass

    def get_price(self, symbol_or_id: str, vs_currency: str = "usd") -> float | None:
        token_id = resolve_symbol(symbol_or_id)
        return get_coingecko_service().get_price(token_id, vs_currency)

    def get_prices(
        self,
        symbols_or_ids: list[str],
        vs_currency: str = "usd",
    ) -> dict[str, float]:
        """Multi-token. Returns a dict keyed by the **caller-supplied** symbol,
        not the resolved CoinGecko ID, so callers don't have to round-trip
        the resolution.
        """
        if not symbols_or_ids:
            return {}

        # Resolve each symbol → CG id, but remember the mapping so we can
        # rekey the response back to the caller's symbols.
        rekey: dict[str, str] = {}  # cg_id -> caller symbol
        cg_ids: list[str] = []
        for sym in symbols_or_ids:
            cg_id = resolve_symbol(sym)
            rekey[cg_id] = sym
            cg_ids.append(cg_id)

        prices = get_coingecko_service().get_prices(cg_ids, vs_currency)

        out: dict[str, float] = {}
        for cg_id, price in prices.items():
            caller_sym = rekey.get(cg_id, cg_id)
            out[caller_sym] = price
        return out


_instance: PriceService | None = None


def get_price_service() -> PriceService:
    global _instance
    if _instance is None:
        _instance = PriceService()
    return _instance
