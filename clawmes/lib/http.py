"""HTTP client with allowlist + retries.

All outbound HTTP from clawmes goes through this module. It enforces:

  * **Network allowlist** — only hosts in the curated set (or
    ``clawmes.network_allowlist.extra_hosts``) can be reached. Disabled
    via ``clawmes.network_allowlist.enabled: false`` but ``hermes
    clawmes doctor`` flags this yellow.
  * **Retry policy** — exponential backoff via ``tenacity`` for
    transient 5xx and connection errors. 4xx is never retried.
  * **Timeouts** — 30s default, configurable per call.
  * **User-Agent** — ``clawmes/<version>`` so upstream services can
    identify our traffic.

Tools should call ``http_get`` / ``http_post`` here rather than
instantiating their own ``httpx`` clients.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from clawmes._version import __version__
from clawmes.lib.logger import logger_for

_log = logger_for("lib.http")

USER_AGENT = f"clawmes/{__version__}"

DEFAULT_TIMEOUT_SECONDS = 30.0


_DEFAULT_ALLOWLIST: frozenset[str] = frozenset(
    {
        # DEX aggregators
        "api.0x.org",
        "api.1inch.dev",
        "li.quest",
        # RPCs — paid providers
        "eth-mainnet.g.alchemy.com",
        "base-mainnet.g.alchemy.com",
        "arb-mainnet.g.alchemy.com",
        "opt-mainnet.g.alchemy.com",
        "polygon-mainnet.g.alchemy.com",
        "rpc.ankr.com",
        # RPCs — official chain-team public endpoints (the new defaults)
        "ethereum-rpc.publicnode.com",
        "mainnet.base.org",
        "arb1.arbitrum.io",
        "mainnet.optimism.io",
        "polygon-rpc.com",
        # Price feeds + market data
        "api.coingecko.com",
        "api.dexscreener.com",
        "data.chain.link",
        "api.defillama.com",
        # Block explorers
        "api.basescan.org",
        "api.etherscan.io",
        "api.arbiscan.io",
        "api.optimistic.etherscan.io",
        "api.polygonscan.com",
        # Lending / yield
        "aave-api-v3.aave.com",
        "api.lido.fi",
        "api.rocketpool.net",
        "api.yearn.fi",
        # Bridges
        "api.across.to",
        "stargate.finance",
        # Governance / social
        "hub.snapshot.org",
        "api.tally.xyz",
        "api.neynar.com",
        # NFTs
        "api.reservoir.tools",
        "api-base.reservoir.tools",
        "api-arbitrum.reservoir.tools",
        "api-optimism.reservoir.tools",
        "api-polygon.reservoir.tools",
        # Yield aggregator
        "yields.llama.fi",
        # Safe (Gnosis Safe Transaction Service) — per-chain hosts
        "safe-transaction-mainnet.safe.global",
        "safe-transaction-base.safe.global",
        "safe-transaction-arbitrum.safe.global",
        "safe-transaction-optimism.safe.global",
        "safe-transaction-polygon.safe.global",
        # Bankr
        "api.bankr.bot",
        "llm.bankr.bot",
        # LLM inference gateway (gitlawb opengateway — see services.opengateway)
        "opengateway.gitlawb.com",
        # Simulation
        "api.tenderly.co",
        # Fiat ramps
        "bridge.xyz",
        "api.moonpay.com",
        # WC v2 relay
        "relay.walletconnect.com",
    }
)


class NetworkAllowlistError(RuntimeError):
    """Raised when an HTTP call targets a host not on the allowlist."""


def _check_allowlist(url: str, *, extra_hosts: frozenset[str] | None = None) -> None:
    host = (urlparse(url).hostname or "").lower()
    if not host:
        raise NetworkAllowlistError(f"URL has no host: {url!r}")
    if host in _DEFAULT_ALLOWLIST:
        return
    if extra_hosts and host in extra_hosts:
        return
    # Consult the runtime user allowlist + record the block for audit.
    # Defensive import so a test environment without the services
    # subsystem (or a service that hasn't been started yet) doesn't
    # break the existing default-allowlist check.
    try:
        from clawmes.services.endpoint_allowlist import (
            get_endpoint_allowlist_service,
        )

        svc = get_endpoint_allowlist_service()
    except Exception:  # noqa: BLE001 — never let allowlist plumbing kill a request
        svc = None
    if svc is not None:
        if svc.is_allowed(host):
            return
        svc.record_block(url, host)
    raise NetworkAllowlistError(
        f"Host {host!r} is not on the clawmes network allowlist. "
        "Add via /allow <host> for this session, or extend "
        "clawmes.network_allowlist.extra_hosts in config.yaml for "
        "permanent additions."
    )


def http_get(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    extra_hosts: frozenset[str] | None = None,
) -> dict[str, Any]:
    """GET ``url`` and return parsed JSON.

    Raises ``NetworkAllowlistError`` if the host is not allowed,
    ``httpx.HTTPStatusError`` on non-2xx, ``httpx.TimeoutException`` on
    timeout. Tools should catch and convert to ``error_result``.
    """
    _check_allowlist(url, extra_hosts=extra_hosts)
    return _request("GET", url, params=params, headers=headers, timeout=timeout)


def http_post(
    url: str,
    *,
    json: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    extra_hosts: frozenset[str] | None = None,
) -> dict[str, Any]:
    """POST JSON to ``url`` and return parsed JSON."""
    _check_allowlist(url, extra_hosts=extra_hosts)
    return _request("POST", url, json=json, headers=headers, timeout=timeout)


def _request(
    method: str,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    json: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Lazy-import httpx so tests can run without it."""
    import httpx
    from tenacity import (
        retry,
        retry_if_exception_type,
        stop_after_attempt,
        wait_exponential,
    )

    merged_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        merged_headers.update(headers)

    @retry(
        retry=retry_if_exception_type(
            (httpx.TransportError, httpx.HTTPStatusError),
        ),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        stop=stop_after_attempt(3),
        reraise=True,
    )
    def _do() -> dict[str, Any]:
        with httpx.Client(timeout=timeout) as client:
            resp = client.request(
                method,
                url,
                params=params,
                json=json,
                headers=merged_headers,
            )
            resp.raise_for_status()
            return resp.json()

    _log.debug("%s %s", method, url)
    return _do()
