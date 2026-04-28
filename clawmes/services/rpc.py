"""JSON-RPC client for EVM chains.

A thin wrapper around ``clawmes.lib.http`` that speaks the
``eth_*`` JSON-RPC subset clawmes needs:

  * ``eth_blockNumber``
  * ``eth_getBalance``
  * ``eth_call``
  * ``eth_chainId`` — used by ``hermes clawmes doctor`` to verify a
    configured RPC actually serves the chain it claims
  * ``eth_getTransactionCount`` — nonce lookup before signing
  * ``eth_sendRawTransaction`` — broadcast a signed tx
  * ``eth_getTransactionReceipt`` + :meth:`RpcService.wait_for_receipt`
    — receipt polling for the local-key mode's send path

RPC endpoint URLs come from ``clawmes.rpc`` config (one URL per chain
id) with env-var substitution. See :mod:`clawmes.lib.chains` for the
canonical chain registry.

The service itself does not sign — broadcast paths take an already-
signed raw hex from a wallet mode. The trust boundary is "private keys
never leave the wallet mode."
"""

from __future__ import annotations

import os
import threading
import time
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

    def get_transaction_count(
        self,
        address: str,
        chain_id: int,
        *,
        block: str = "pending",
    ) -> int:
        """Return the current nonce for ``address`` on ``chain_id``.

        Defaults to ``"pending"`` so callers about to broadcast a tx
        get a nonce that accounts for any in-flight tx still in the
        mempool. Pass ``block="latest"`` for confirmed-only counts.
        """
        result = self._call(chain_id, "eth_getTransactionCount", [address, block])
        return int(result, 16) if isinstance(result, str) else int(result)

    def send_raw_transaction(self, raw_hex: str, chain_id: int) -> str:
        """Broadcast a signed tx; return the tx hash.

        ``raw_hex`` may be with or without the ``0x`` prefix — we
        normalize either way before sending so callers don't have to
        care about how their signing library serializes.
        """
        if not raw_hex.startswith("0x"):
            raw_hex = "0x" + raw_hex
        result = self._call(chain_id, "eth_sendRawTransaction", [raw_hex])
        if not isinstance(result, str):
            raise RpcError(
                -32700,
                f"unexpected sendRawTransaction result: {type(result).__name__}",
                method="eth_sendRawTransaction",
            )
        return result

    def get_transaction_receipt(
        self,
        tx_hash: str,
        chain_id: int,
    ) -> dict[str, Any] | None:
        """Return the receipt for ``tx_hash`` or ``None`` if not yet mined."""
        result = self._call(chain_id, "eth_getTransactionReceipt", [tx_hash])
        if result is None:
            return None
        if not isinstance(result, dict):
            raise RpcError(
                -32700,
                f"unexpected receipt type: {type(result).__name__}",
                method="eth_getTransactionReceipt",
            )
        return result

    def wait_for_receipt(
        self,
        tx_hash: str,
        chain_id: int,
        *,
        timeout: float = 120.0,
        poll_interval: float = 2.0,
    ) -> dict[str, Any]:
        """Poll for ``tx_hash``'s receipt; raise on timeout.

        Polling cadence defaults to 2s — fine for most L2s where blocks
        finalize in a couple of seconds. For Ethereum mainnet a 5–10s
        cadence is friendlier to public RPCs but the default still
        finishes in under a minute for typical txs.
        """
        deadline = time.monotonic() + timeout
        while True:
            receipt = self.get_transaction_receipt(tx_hash, chain_id)
            if receipt is not None:
                return receipt
            if time.monotonic() >= deadline:
                raise RpcError(
                    -32000,
                    f"timed out after {timeout:.0f}s waiting for {tx_hash}",
                    method="eth_getTransactionReceipt",
                )
            time.sleep(poll_interval)

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
