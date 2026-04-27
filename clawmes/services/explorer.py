"""Block-explorer service — Etherscan-family HTTP client.

Both Basescan and Etherscan use the same Etherscan API contract. They
differ only in base URL and which chain they serve. This service
maintains a per-chain endpoint table and routes requests through
``clawmes.lib.http``.

API key requirement: free tier supports up to ~5 req/sec without a
key, but the responses include rate-limit warnings. With a key (set
via ``BASESCAN_API_KEY`` / ``ETHERSCAN_API_KEY`` env), the service
upgrades to the paid limits. Missing keys are tolerated — calls go
through without one and rely on the free tier.

Methods we expose at v0.1.0:

  * :meth:`get_tx_status`        — transaction receipt status
  * :meth:`get_tx_by_hash`       — full transaction details
  * :meth:`get_address_info`     — native balance + tx count
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass

from clawmes.lib.http import http_get
from clawmes.lib.logger import logger_for
from clawmes.services._base import Service

_log = logger_for("services.explorer")


@dataclass(frozen=True)
class _ExplorerConfig:
    chain_id: int
    name: str
    base_url: str
    key_env: str


# Curated set — matches the chains we have RPC endpoints for.
_EXPLORERS: dict[int, _ExplorerConfig] = {
    1: _ExplorerConfig(
        chain_id=1,
        name="Etherscan",
        base_url="https://api.etherscan.io/api",
        key_env="ETHERSCAN_API_KEY",
    ),
    8453: _ExplorerConfig(
        chain_id=8453,
        name="Basescan",
        base_url="https://api.basescan.org/api",
        key_env="BASESCAN_API_KEY",
    ),
    42161: _ExplorerConfig(
        chain_id=42161,
        name="Arbiscan",
        base_url="https://api.arbiscan.io/api",
        key_env="ARBISCAN_API_KEY",
    ),
    10: _ExplorerConfig(
        chain_id=10,
        name="Optimistic Etherscan",
        base_url="https://api.optimistic.etherscan.io/api",
        key_env="OPTIMISM_ETHERSCAN_API_KEY",
    ),
    137: _ExplorerConfig(
        chain_id=137,
        name="Polygonscan",
        base_url="https://api.polygonscan.com/api",
        key_env="POLYGONSCAN_API_KEY",
    ),
}


class ExplorerError(RuntimeError):
    """Raised when an explorer API returns a non-success result."""


class ExplorerService(Service):
    id = "clawmes.explorer"

    def __init__(self) -> None:
        self._lock = threading.RLock()

    def start(self) -> None:
        _log.info(
            "explorer service started; chains supported: %s",
            sorted(_EXPLORERS.keys()),
        )

    def stop(self) -> None:
        pass

    def supports_chain(self, chain_id: int) -> bool:
        return chain_id in _EXPLORERS

    def explorer_name(self, chain_id: int) -> str:
        if chain_id not in _EXPLORERS:
            raise ExplorerError(f"no explorer configured for chain {chain_id}")
        return _EXPLORERS[chain_id].name

    # --- public methods ---

    def get_tx_status(self, tx_hash: str, chain_id: int) -> dict:
        """Return ``{"status": "1"|"0", "errDescription": "..."}`` etc."""
        return self._call(chain_id, module="transaction", action="getstatus", txhash=tx_hash)

    def get_tx_receipt_status(self, tx_hash: str, chain_id: int) -> dict:
        """Return ``{"status": "1"|"0"}`` for the tx receipt."""
        return self._call(
            chain_id, module="transaction", action="gettxreceiptstatus", txhash=tx_hash
        )

    def get_address_balance(self, address: str, chain_id: int) -> int:
        """Native-token balance in wei (per the explorer's view; the RPC
        is more authoritative but slower to settle on some chains)."""
        result = self._call(
            chain_id, module="account", action="balance", address=address, tag="latest"
        )
        return int(result) if isinstance(result, (str, int)) else 0

    def get_address_tx_count(self, address: str, chain_id: int) -> int:
        """Number of transactions sent FROM the address."""
        result = self._call(
            chain_id,
            module="proxy",
            action="eth_getTransactionCount",
            address=address,
            tag="latest",
        )
        if isinstance(result, str):
            return int(result, 16)
        return int(result) if isinstance(result, int) else 0

    # --- internals ---

    def _call(self, chain_id: int, **params) -> object:
        if chain_id not in _EXPLORERS:
            raise ExplorerError(f"no explorer configured for chain {chain_id}")

        cfg = _EXPLORERS[chain_id]
        api_key = os.environ.get(cfg.key_env)
        full_params = dict(params)
        if api_key:
            full_params["apikey"] = api_key

        response = http_get(cfg.base_url, params=full_params, timeout=15.0)

        if not isinstance(response, dict):
            raise ExplorerError(f"non-dict response from {cfg.name}: {type(response).__name__}")

        # Etherscan API returns {"status":"1","message":"OK","result":...}
        # for module=account; module=proxy returns {"jsonrpc":...,"result":...}.
        # Handle both shapes.
        if "result" in response:
            status = response.get("status")
            if status == "0":
                # Error path
                err = response.get("message") or response.get("result") or "unknown"
                raise ExplorerError(f"{cfg.name} error: {err}")
            return response["result"]
        # Some endpoints (proxy) return non-standard shape; pass through.
        return response


_instance: ExplorerService | None = None


def get_explorer_service() -> ExplorerService:
    global _instance
    if _instance is None:
        _instance = ExplorerService()
    return _instance
