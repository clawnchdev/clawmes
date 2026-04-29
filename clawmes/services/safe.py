"""Safe (Gnosis Safe) Transaction Service client.

Safe is the canonical multisig contract on EVM chains — used by
DAOs, treasuries, and most crypto teams for shared custody. The
Transaction Service (api.safe.global) is the off-chain coordinator
where owner signatures are pooled until the multisig threshold is
reached, then anyone executes.

Endpoints we consume:

  * ``GET /v1/safes/{address}`` — Safe info (owners, threshold, nonce).
  * ``GET /v1/safes/{address}/multisig-transactions/`` — proposed +
    executed transactions for a Safe.
  * ``POST /v1/safes/{address}/multisig-transactions/`` — propose a
    new tx (or add a signature to an existing one).

Per-chain hostname pattern: ``safe-transaction-{chainSlug}.safe.global``.
The slugs match Safe's network IDs, not always equal to chain id.

API key: not required for the Transaction Service — it's a
public-good service maintained by Safe Foundation.
"""

from __future__ import annotations

from typing import Any

from clawmes.lib.http import http_get, http_post
from clawmes.lib.logger import logger_for

_log = logger_for("services.safe")

# Per-chain Safe Transaction Service hostnames. Adding a chain here
# requires verifying the slug at https://docs.safe.global.
_TX_SERVICE_HOSTS: dict[int, str] = {
    1: "https://safe-transaction-mainnet.safe.global",
    8453: "https://safe-transaction-base.safe.global",
    42161: "https://safe-transaction-arbitrum.safe.global",
    10: "https://safe-transaction-optimism.safe.global",
    137: "https://safe-transaction-polygon.safe.global",
}


class SafeError(RuntimeError):
    """Raised on Safe Transaction Service failures.

    ``code``:
      * ``unsupported_chain`` — Safe service not available on chain.
      * ``not_found`` — Safe address has no record on this chain.
      * ``api_error`` — generic upstream failure.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def supports_chain(chain_id: int) -> bool:
    return chain_id in _TX_SERVICE_HOSTS


def _base_url(chain_id: int) -> str:
    if chain_id not in _TX_SERVICE_HOSTS:
        raise SafeError(
            "unsupported_chain",
            f"Safe Transaction Service not available on chain {chain_id}",
        )
    return _TX_SERVICE_HOSTS[chain_id]


def get_safe_info(safe_address: str, chain_id: int) -> dict[str, Any]:
    """Fetch Safe metadata: owners, threshold, nonce, version.

    Raises :class:`SafeError` with ``not_found`` code if the address
    isn't a recognized Safe on the given chain.
    """
    base = _base_url(chain_id)
    try:
        resp = http_get(f"{base}/api/v1/safes/{safe_address}/", timeout=15.0)
    except Exception as exc:  # noqa: BLE001
        msg = str(exc).lower()
        if "404" in msg or "not found" in msg:
            raise SafeError(
                "not_found",
                f"Safe {safe_address} not found on chain {chain_id}",
            ) from exc
        raise SafeError("api_error", f"Safe API failed: {exc}") from exc

    if not isinstance(resp, dict):
        raise SafeError("api_error", "Safe API returned non-dict response")
    return resp


def get_pending_transactions(
    safe_address: str, chain_id: int, *, limit: int = 20
) -> list[dict[str, Any]]:
    """Fetch unexecuted proposed transactions awaiting signatures."""
    base = _base_url(chain_id)
    try:
        resp = http_get(
            f"{base}/api/v1/safes/{safe_address}/multisig-transactions/",
            params={"executed": "false", "limit": str(limit)},
            timeout=15.0,
        )
    except Exception as exc:  # noqa: BLE001
        raise SafeError("api_error", f"Safe API failed: {exc}") from exc

    if not isinstance(resp, dict):
        raise SafeError("api_error", "Safe API returned non-dict response")
    return resp.get("results") or []


def propose_transaction(
    safe_address: str,
    chain_id: int,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Submit a new proposed transaction (or signature) to the Service.

    The Service handles deduplication: if ``payload`` matches an
    existing proposal, this just adds the new signature.

    ``payload`` shape per Safe Transaction Service spec — caller is
    responsible for building it (EIP-712 hash + owner signature).
    See https://docs.safe.global for the canonical fields.
    """
    base = _base_url(chain_id)
    try:
        resp = http_post(
            f"{base}/api/v1/safes/{safe_address}/multisig-transactions/",
            json=payload,
            timeout=15.0,
        )
    except Exception as exc:  # noqa: BLE001
        raise SafeError("api_error", f"Safe propose failed: {exc}") from exc
    if not isinstance(resp, dict):
        # Some endpoints return None / empty body on success
        return {"submitted": True}
    return resp
