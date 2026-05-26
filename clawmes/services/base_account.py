"""Base Account / Coinbase Smart Wallet — OAuth + stored-request client.

Base App's wallet (Coinbase Smart Wallet, sometimes referenced as
"Base Account") is the wallet most Base ecosystem users live in
day-to-day. It exposes:

  * **OAuth 2.1** for authentication — same flow Base MCP uses to
    pair an external agent with a user's Base Account.
  * **Stored-request endpoint** for tx / signature requests — the
    agent constructs the request, the wallet returns a request id
    plus an approval URL, and the user opens the URL in their Base
    App to approve / cancel.

This service is the clawmes-side client for that surface. It backs
:class:`clawmes.wallet.base_account.BaseAccountMode`, which presents
the same wallet-mode interface as Bankr / WalletConnect / local-key
so the rest of clawmes can treat it as just another mode.

Configuration:

  * ``CLAWMES_BASE_ACCOUNT_CLIENT_ID`` — OAuth client id registered
    with Coinbase Developer Platform. Required for ``connect``.
  * ``CLAWMES_BASE_ACCOUNT_REDIRECT_URI`` — OAuth redirect URI
    (defaults to ``http://localhost:8765/oauth/base_account/callback``
    — only used by local CLI flows; production users should override).
  * ``CLAWMES_BASE_ACCOUNT_AUTH_URL`` — OAuth authorize endpoint
    (defaults to Coinbase's public endpoint; overridable for staging).
  * ``CLAWMES_BASE_ACCOUNT_TOKEN_URL`` — OAuth token-exchange endpoint.
  * ``CLAWMES_BASE_ACCOUNT_API_URL`` — Base Account API root for
    wallet-request submission / polling.

Why ship this even when the real Coinbase endpoints might shift: the
*shape* of the integration is stable (OAuth code flow, stored-request
pattern matches Base MCP), and the env-var overrides let the user
point clawmes at the right URLs without code changes when Base App's
production endpoints stabilize.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any
from urllib.parse import urlencode

from clawmes.lib.http import http_get, http_post
from clawmes.lib.logger import logger_for
from clawmes.services._base import Service

_log = logger_for("services.base_account")

# Defaults — production users should override via env if Coinbase ships
# different endpoints. Path conventions match Coinbase's OAuth 2.1 +
# Base Account "wallet/requests" patterns described in the Base MCP
# spec; treat as best-effort until publicly documented.
_DEFAULT_AUTH_URL = "https://auth.coinbase.com/oauth2/authorize"
_DEFAULT_TOKEN_URL = "https://auth.coinbase.com/oauth2/token"
_DEFAULT_API_URL = "https://api.base.app"
_DEFAULT_REDIRECT_URI = "http://localhost:8765/oauth/base_account/callback"
_DEFAULT_SCOPE = "wallet:read wallet:sign"

# How long to poll a stored request before giving up. Approvals are
# user-paced; 5 minutes is the right ceiling for chat-platform UX.
_DEFAULT_POLL_TIMEOUT_S = 300.0
_DEFAULT_POLL_INTERVAL_S = 2.0


class BaseAccountError(RuntimeError):
    """Raised on Base Account API failures.

    ``code`` classification:
      * ``not_configured``  — required env var missing.
      * ``not_connected``   — operation needs an access token but none set.
      * ``oauth_error``     — OAuth flow failure (auth code / token exchange).
      * ``request_failed``  — stored-request submission rejected by the API.
      * ``approval_timeout``— user didn't approve within the polling window.
      * ``api_error``       — generic upstream failure.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class BaseAccountService(Service):
    """Singleton OAuth + stored-request client for Base Account."""

    id = "clawmes.base_account"

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._token_expires_at: float | None = None
        self._user_address: str | None = None
        self._auth_url: str = _DEFAULT_AUTH_URL
        self._token_url: str = _DEFAULT_TOKEN_URL
        self._api_url: str = _DEFAULT_API_URL
        self._client_id: str | None = None
        self._redirect_uri: str = _DEFAULT_REDIRECT_URI

    # ── lifecycle ───────────────────────────────────────────────────

    def start(self) -> None:
        with self._lock:
            self._auth_url = os.environ.get("CLAWMES_BASE_ACCOUNT_AUTH_URL", _DEFAULT_AUTH_URL)
            self._token_url = os.environ.get("CLAWMES_BASE_ACCOUNT_TOKEN_URL", _DEFAULT_TOKEN_URL)
            self._api_url = os.environ.get("CLAWMES_BASE_ACCOUNT_API_URL", _DEFAULT_API_URL)
            self._client_id = os.environ.get("CLAWMES_BASE_ACCOUNT_CLIENT_ID") or None
            self._redirect_uri = os.environ.get(
                "CLAWMES_BASE_ACCOUNT_REDIRECT_URI", _DEFAULT_REDIRECT_URI
            )
        if self._client_id:
            _log.info("base_account service started (client=%s)", self._client_id[:8] + "…")
        else:
            _log.info(
                "base_account service started without CLAWMES_BASE_ACCOUNT_CLIENT_ID — "
                "connect will fail until configured"
            )

    def stop(self) -> None:
        with self._lock:
            self._access_token = None
            self._refresh_token = None
            self._token_expires_at = None
            self._user_address = None

    # ── OAuth ───────────────────────────────────────────────────────

    def get_auth_url(self, *, state: str | None = None) -> str:
        """Return the OAuth authorize URL for the user to visit.

        Raises :class:`BaseAccountError` with code ``not_configured``
        when ``CLAWMES_BASE_ACCOUNT_CLIENT_ID`` is not set.
        """
        if not self._client_id:
            raise BaseAccountError(
                "not_configured",
                "CLAWMES_BASE_ACCOUNT_CLIENT_ID env var not set. Register a Coinbase "
                "Developer Platform OAuth client and export the id before /connect_base.",
            )
        params = {
            "client_id": self._client_id,
            "redirect_uri": self._redirect_uri,
            "response_type": "code",
            "scope": _DEFAULT_SCOPE,
        }
        if state:
            params["state"] = state
        return f"{self._auth_url}?{urlencode(params)}"

    def exchange_code(self, code: str) -> dict[str, Any]:
        """Exchange an authorization ``code`` for access + refresh tokens.

        Stores the tokens on the service singleton and returns the raw
        upstream response (useful for callers that want to inspect
        ``expires_in`` directly).
        """
        if not self._client_id:
            raise BaseAccountError(
                "not_configured", "CLAWMES_BASE_ACCOUNT_CLIENT_ID env var not set"
            )
        if not code:
            raise BaseAccountError("oauth_error", "authorization code is required")
        body = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": self._client_id,
            "redirect_uri": self._redirect_uri,
        }
        try:
            resp = http_post(
                self._token_url,
                json=body,
                headers={"Content-Type": "application/json"},
                timeout=30.0,
            )
        except Exception as exc:  # noqa: BLE001 — classified below
            raise BaseAccountError("oauth_error", f"token exchange failed: {exc}") from exc
        if not isinstance(resp, dict) or not resp.get("access_token"):
            raise BaseAccountError(
                "oauth_error", f"token exchange returned no access token: {resp!r}"
            )
        with self._lock:
            self._access_token = str(resp["access_token"])
            self._refresh_token = (
                str(resp.get("refresh_token")) if resp.get("refresh_token") else None
            )
            expires_in = resp.get("expires_in")
            if isinstance(expires_in, (int, float)):
                self._token_expires_at = time.time() + float(expires_in)
            self._user_address = resp.get("address") or resp.get("wallet_address") or None
        _log.info("base_account oauth code exchanged successfully")
        return resp

    # ── account ─────────────────────────────────────────────────────

    def get_user_address(self) -> str:
        """Return the connected Base Account's primary address.

        If the token-exchange response didn't include the address, we
        hit the API's ``/v1/wallet`` endpoint to fetch it. Cached
        thereafter for the lifetime of the connection.
        """
        with self._lock:
            cached = self._user_address
        if cached:
            return cached
        token = self._require_token()
        try:
            resp = http_get(
                f"{self._api_url}/v1/wallet",
                headers={"Authorization": f"Bearer {token}"},
                timeout=15.0,
            )
        except Exception as exc:  # noqa: BLE001
            raise BaseAccountError("api_error", f"wallet lookup failed: {exc}") from exc
        addr = (resp or {}).get("address") or (resp or {}).get("primary_address")
        if not isinstance(addr, str) or not addr:
            raise BaseAccountError("api_error", f"wallet API returned no address: {resp!r}")
        with self._lock:
            self._user_address = addr
        return addr

    # ── stored-request ──────────────────────────────────────────────

    def submit_request(self, *, method: str, params: list[Any]) -> dict[str, Any]:
        """Submit a wallet JSON-RPC request to Base Account.

        Returns ``{request_id, approval_url, status}``. The user opens
        ``approval_url`` in their Base App to approve. Caller should
        poll :meth:`poll_request` until status is ``confirmed`` or
        ``rejected``.
        """
        token = self._require_token()
        body = {"method": method, "params": params}
        try:
            resp = http_post(
                f"{self._api_url}/v1/wallet/requests",
                json=body,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                timeout=30.0,
            )
        except Exception as exc:  # noqa: BLE001
            raise BaseAccountError("request_failed", f"wallet/requests POST failed: {exc}") from exc
        if not isinstance(resp, dict) or not resp.get("request_id"):
            raise BaseAccountError(
                "request_failed", f"wallet/requests returned bad shape: {resp!r}"
            )
        return resp

    def poll_request(
        self,
        request_id: str,
        *,
        timeout: float = _DEFAULT_POLL_TIMEOUT_S,
        interval: float = _DEFAULT_POLL_INTERVAL_S,
    ) -> dict[str, Any]:
        """Poll until ``request_id`` is approved / rejected / expires."""
        token = self._require_token()
        deadline = time.monotonic() + timeout
        url = f"{self._api_url}/v1/wallet/requests/{request_id}"
        while True:
            try:
                resp = http_get(
                    url,
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=15.0,
                )
            except Exception as exc:  # noqa: BLE001
                raise BaseAccountError("api_error", f"wallet/requests poll failed: {exc}") from exc
            status = (resp or {}).get("status") or ""
            if status in ("confirmed", "rejected", "expired", "failed"):
                if status != "confirmed":
                    raise BaseAccountError(
                        "request_failed", f"request {request_id} {status}: {resp!r}"
                    )
                return resp
            if time.monotonic() >= deadline:
                raise BaseAccountError(
                    "approval_timeout",
                    f"timed out waiting for user approval of {request_id} (status={status})",
                )
            time.sleep(interval)

    # ── helpers ─────────────────────────────────────────────────────

    def is_connected(self) -> bool:
        with self._lock:
            return self._access_token is not None

    def _require_token(self) -> str:
        with self._lock:
            tok = self._access_token
        if not tok:
            raise BaseAccountError(
                "not_connected",
                "Base Account not connected. Run /connect_base to authenticate.",
            )
        return tok


_instance: BaseAccountService | None = None


def get_base_account_service() -> BaseAccountService:
    global _instance
    if _instance is None:
        _instance = BaseAccountService()
        _instance.start()
    return _instance
