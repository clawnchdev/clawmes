"""Token-decimals lookup with cache.

Calls ``decimals()`` on an ERC-20 contract and caches the result for
the lifetime of the process. The decimals value never changes after
contract deployment, so a single fetch per (chain_id, address) is
sufficient.

For the small set of tokens we use repeatedly (USDC, WETH, etc.) we
seed the cache with known values so a fresh install can render
balances immediately without an RPC round-trip.
"""

from __future__ import annotations

import threading

from clawmes.lib.abi import decode_uint8, encode_decimals_call
from clawmes.lib.logger import logger_for
from clawmes.services._base import Service
from clawmes.services.rpc import RpcError, get_rpc_service

_log = logger_for("services.token_decimals")


# Curated seed cache — contract addresses normalized lowercase.
# Format: (chain_id, address) -> decimals.
_SEED: dict[tuple[int, str], int] = {
    # USDC
    (1, "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"): 6,
    (8453, "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"): 6,
    (42161, "0xaf88d065e77c8cc2239327c5edb3a432268e5831"): 6,
    (10, "0x0b2c639c533813f4aa9d7837caf62653d097ff85"): 6,
    (137, "0x3c499c542cef5e3811e1192ce70d8cc03d5c3359"): 6,
    # USDT
    (1, "0xdac17f958d2ee523a2206206994597c13d831ec7"): 6,
    (42161, "0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9"): 6,
    # WETH
    (1, "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"): 18,
    (8453, "0x4200000000000000000000000000000000000006"): 18,
    (42161, "0x82af49447d8a07e3bd95bd0d56f35241523fbab1"): 18,
    (10, "0x4200000000000000000000000000000000000006"): 18,
    # DAI
    (1, "0x6b175474e89094c44da98b954eedeac495271d0f"): 18,
    (8453, "0x50c5725949a6f0c72e6c4a641f24049a917db0cb"): 18,
}


class TokenDecimalsService(Service):
    id = "clawmes.token_decimals"

    def __init__(self) -> None:
        self._lock = threading.RLock()
        # Pre-populate seeds at construction so callers that bypass
        # the lifecycle (tests, ad-hoc scripts) still get accurate
        # decimals for the curated set without an RPC round-trip.
        self._cache: dict[tuple[int, str], int] = dict(_SEED)

    def start(self) -> None:
        _log.info("token_decimals service started (%d seed entries)", len(self._cache))

    def stop(self) -> None:
        with self._lock:
            self._cache.clear()

    def get(self, address: str, chain_id: int) -> int:
        """Return the decimals for an ERC-20 token.

        Cached after the first lookup. Falls back to ``18`` (the
        most common default) if the on-chain ``decimals()`` call
        fails — better to render a possibly-wrong balance than to
        crash a balance summary.
        """
        key = (chain_id, address.lower())
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                return cached

        try:
            raw = get_rpc_service().eth_call(
                to=address,
                data=encode_decimals_call(),
                chain_id=chain_id,
            )
            decimals = decode_uint8(raw)
        except (RpcError, ValueError) as exc:
            _log.warning(
                "decimals() lookup failed for %s on chain %d (%s); falling back to 18",
                address,
                chain_id,
                exc,
            )
            decimals = 18

        with self._lock:
            self._cache[key] = decimals
        return decimals


_instance: TokenDecimalsService | None = None


def get_token_decimals_service() -> TokenDecimalsService:
    global _instance
    if _instance is None:
        _instance = TokenDecimalsService()
    return _instance
