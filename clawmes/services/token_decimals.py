"""Token-decimals lookup with cache.

Calls ``decimals()`` on an ERC-20 contract and caches the result for
the lifetime of the process. The decimals value never changes after
contract deployment, so a single fetch per (chain_id, address) is
sufficient.

For the small set of tokens we use repeatedly (USDC, WETH, etc.) we
seed the cache with known values so a fresh install can render
balances immediately without an RPC round-trip.

**Two access methods, with very different failure semantics:**

* :meth:`TokenDecimalsService.get` — fall-back path. Returns ``18``
  if the on-chain call fails. Use this for read-only display
  (balance summaries, price lookups). A wrong-by-default value is
  acceptable for human-readable output and converges to correct on
  the next successful lookup.
* :meth:`TokenDecimalsService.get_strict` — fail-loud path. Raises
  :class:`TokenDecimalsError` if the call fails. Use this for any
  path that converts a human amount to base units before signing
  (transfer, swap, approve, etc.). A silent fallback to 18 here
  would, for a 6-decimal token like USDC, multiply the user's
  intended amount by 10^12.
"""

from __future__ import annotations

import threading

from clawmes.lib.abi import decode_uint8, encode_decimals_call
from clawmes.lib.logger import logger_for
from clawmes.services._base import Service
from clawmes.services.rpc import RpcError, get_rpc_service

_log = logger_for("services.token_decimals")


class TokenDecimalsError(RuntimeError):
    """Raised by :meth:`TokenDecimalsService.get_strict` when the
    on-chain ``decimals()`` call fails and no cached value is available.

    Carries the original cause so the caller can surface a useful
    diagnostic to the user.
    """

    def __init__(self, address: str, chain_id: int, cause: Exception) -> None:
        super().__init__(f"could not determine decimals for {address} on chain {chain_id}: {cause}")
        self.address = address
        self.chain_id = chain_id
        self.cause = cause


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
        # Two-tier cache:
        #   _cache       — verified values (from RPC or seed). Strict
        #                  reads trust these.
        #   _fallback    — last-resort 18 from a failed lookup. Loose
        #                  reads return these to avoid hammering the
        #                  RPC on every balance render. Strict reads
        #                  IGNORE these and re-issue the call (with a
        #                  chance to surface the underlying RPC error
        #                  to the user).
        # Seeds are verified by definition.
        self._cache: dict[tuple[int, str], int] = dict(_SEED)
        self._fallback: dict[tuple[int, str], int] = {}

    def start(self) -> None:
        _log.info("token_decimals service started (%d seed entries)", len(self._cache))

    def stop(self) -> None:
        with self._lock:
            self._cache.clear()
            self._fallback.clear()

    def get(self, address: str, chain_id: int) -> int:
        """Return the decimals for an ERC-20 token, with fallback.

        Returns the verified value if cached/fetchable. Falls back to
        ``18`` (the most common default) on RPC failure — appropriate
        for read-only display where a possibly-wrong rendering is
        preferable to crashing the whole balance summary.

        Fallback values are stored separately from verified values:
        :meth:`get_strict` ignores them and re-tries the RPC.

        **Do not use on a send path.** A 6-decimal token like USDC
        with this method silently multiplies the amount by 10^12 if
        the lookup fails. Use :meth:`get_strict` for any conversion
        that feeds into a signed transaction.
        """
        key = (chain_id, address.lower())
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                return cached
            fallback_cached = self._fallback.get(key)
        if fallback_cached is not None:
            return fallback_cached

        try:
            return self._fetch_and_cache(address, chain_id)
        except TokenDecimalsError as exc:
            _log.warning(
                "decimals() lookup failed for %s on chain %d (%s); loose-path falling back to 18",
                address,
                chain_id,
                exc.cause,
            )
            with self._lock:
                self._fallback[key] = 18
            return 18

    def peek(self, address: str, chain_id: int) -> int | None:
        """Return decimals if cached/seeded; ``None`` otherwise. Never RPC.

        Designed for callers (like the policy gate) that need a fast,
        non-blocking lookup. Returns:

          * The verified value if seeded or previously fetched.
          * The fallback value (``18``) if the loose path has cached
            one — same as :meth:`get` would return without re-fetching.
          * ``None`` if the token is completely unknown.

        The caller decides what to do with ``None`` — typically "skip
        the quantitative gate, since I can't verify the value at risk."
        """
        key = (chain_id, address.lower())
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                return cached
            return self._fallback.get(key)

    def get_strict(self, address: str, chain_id: int) -> int:
        """Return the decimals for an ERC-20 token, or raise.

        Returns a verified cached value (seed or prior successful RPC).
        Raises :class:`TokenDecimalsError` if no verified value exists
        and the RPC fails — the caller MUST handle this rather than
        fall back to a default, because the value feeds into
        ``to_base_units(amount, decimals)`` and a wrong decimals there
        can multiply or divide the actual amount by powers of 10^12
        silently.

        Fallback values from a previous loose ``get`` call are NOT
        trusted — strict re-issues the RPC to give the user a fresh
        error message if the network's still flaky.
        """
        key = (chain_id, address.lower())
        with self._lock:
            cached = self._cache.get(key)
        if cached is not None:
            return cached

        return self._fetch_and_cache(address, chain_id)

    def _fetch_and_cache(self, address: str, chain_id: int) -> int:
        """Issue ``decimals()`` and store the result in the verified cache.

        Raises :class:`TokenDecimalsError` (the caller decides whether
        to fall back). On success, also evicts any stale fallback
        entry so future loose reads see the verified value.
        """
        try:
            raw = get_rpc_service().eth_call(
                to=address,
                data=encode_decimals_call(),
                chain_id=chain_id,
            )
            decimals = decode_uint8(raw)
        except (RpcError, ValueError) as exc:
            raise TokenDecimalsError(address, chain_id, exc) from exc

        key = (chain_id, address.lower())
        with self._lock:
            self._cache[key] = decimals
            self._fallback.pop(key, None)
        return decimals


_instance: TokenDecimalsService | None = None


def get_token_decimals_service() -> TokenDecimalsService:
    global _instance
    if _instance is None:
        _instance = TokenDecimalsService()
    return _instance
