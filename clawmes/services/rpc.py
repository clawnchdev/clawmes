"""JSON-RPC client for EVM chains.

A thin wrapper around ``clawmes.lib.http`` that speaks the
``eth_*`` JSON-RPC subset clawmes needs for read-only tools at this
milestone:

  * ``eth_blockNumber``
  * ``eth_getBalance``
  * ``eth_call``
  * ``eth_chainId`` — used by ``hermes clawmes doctor`` to verify a
    configured RPC actually serves the chain it claims

RPC endpoint URLs come from ``clawmes.rpc`` config (one URL per chain
id) with env-var substitution. See :mod:`clawmes.lib.chains` for the
canonical chain registry.

The service is read-only (no signing, no tx submission). Write paths
go through the wallet bridges at v0.1.0+.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from typing import Any

from clawmes.lib.http import http_post
from clawmes.lib.logger import logger_for
from clawmes.services._base import Service

_log = logger_for("services.rpc")


# Default endpoints — public RPCs that don't require API keys. Users
# should override via clawmes.rpc.<chain_id> in config.yaml or via
# CLAWMES_RPC_<CHAIN_ID> env vars for production traffic.
_DEFAULT_ENDPOINTS: dict[int, str] = {
    1: "https://eth-mainnet.g.alchemy.com/v2/demo",
    8453: "https://base-mainnet.g.alchemy.com/v2/demo",
    42161: "https://arb-mainnet.g.alchemy.com/v2/demo",
    10: "https://opt-mainnet.g.alchemy.com/v2/demo",
    137: "https://polygon-mainnet.g.alchemy.com/v2/demo",
}


class RpcError(RuntimeError):
    """Raised when a JSON-RPC call returns an `error` field or fails."""

    def __init__(self, code: int, message: str, *, method: str = "") -> None:
        super().__init__(f"{method or 'rpc'} failed: {code} {message}")
        self.code = code
        self.message = message
        self.method = method


@dataclass(frozen=True)
class _Endpoint:
    chain_id: int
    url: str


class RpcService(Service):
    """Read-only EVM RPC dispatcher.

    Endpoint selection: per-chain URL from
    ``clawmes.rpc.<chain_id>`` (config) or
    ``CLAWMES_RPC_<CHAIN_ID>`` (env), falling back to the public
    Alchemy demo endpoints. The demo endpoints have aggressive rate
    limits — production users should configure their own.
    """

    id = "clawmes.rpc"

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._endpoints: dict[int, _Endpoint] = {}
        self._next_request_id: int = 1

    def start(self) -> None:
        with self._lock:
            self._endpoints = self._discover_endpoints()
            _log.info(
                "rpc service started; endpoints configured for chains %s",
                sorted(self._endpoints.keys()),
            )

    def stop(self) -> None:
        with self._lock:
            self._endpoints.clear()

    def has_endpoint(self, chain_id: int) -> bool:
        with self._lock:
            return chain_id in self._endpoints

    def configured_chain_ids(self) -> list[int]:
        with self._lock:
            return sorted(self._endpoints.keys())

    # --- public RPC methods ---

    def block_number(self, chain_id: int) -> int:
        result = self._call(chain_id, "eth_blockNumber", [])
        return int(result, 16) if isinstance(result, str) else int(result)

    def get_balance(self, address: str, chain_id: int) -> int:
        """Return native-token balance in wei."""
        result = self._call(chain_id, "eth_getBalance", [address, "latest"])
        return int(result, 16) if isinstance(result, str) else int(result)

    def eth_call(
        self,
        *,
        to: str,
        data: str,
        chain_id: int,
        block: str = "latest",
    ) -> str:
        """Issue ``eth_call`` and return the raw hex result."""
        params = [{"to": to, "data": data}, block]
        result = self._call(chain_id, "eth_call", params)
        return str(result) if result is not None else "0x"

    def chain_id(self, chain_id: int) -> int:
        """Verify the RPC actually serves the chain we think it does."""
        result = self._call(chain_id, "eth_chainId", [])
        return int(result, 16) if isinstance(result, str) else int(result)

    # --- internals ---

    def _call(self, chain_id: int, method: str, params: list[Any]) -> Any:
        with self._lock:
            endpoint = self._endpoints.get(chain_id)
            if endpoint is None:
                raise RpcError(
                    -32000,
                    f"no RPC endpoint configured for chain id {chain_id}",
                    method=method,
                )
            req_id = self._next_request_id
            self._next_request_id += 1

        payload = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
        response = http_post(endpoint.url, json=payload, timeout=15.0)
        if not isinstance(response, dict):
            raise RpcError(
                -32700, f"non-dict RPC response: {type(response).__name__}", method=method
            )

        if "error" in response and response["error"]:
            err = response["error"]
            code = int(err.get("code", -32000)) if isinstance(err, dict) else -32000
            msg = err.get("message", "unknown") if isinstance(err, dict) else str(err)
            raise RpcError(code, msg, method=method)

        return response.get("result")

    def _discover_endpoints(self) -> dict[int, _Endpoint]:
        out: dict[int, _Endpoint] = {}
        for chain_id, default in _DEFAULT_ENDPOINTS.items():
            override = os.environ.get(f"CLAWMES_RPC_{chain_id}")
            url = override or default
            out[chain_id] = _Endpoint(chain_id=chain_id, url=url)
        return out


_instance: RpcService | None = None


def get_rpc_service() -> RpcService:
    global _instance
    if _instance is None:
        _instance = RpcService()
    return _instance
