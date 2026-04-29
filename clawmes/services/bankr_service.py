"""Bankr custodial wallet HTTP client.

Bankr is the custodial wallet provider that ships with clawmes. Three
features require it (per BANKR_INTEGRATION.md):

  * Token launches on Base/Solana (Bankr sponsors the deploy gas)
  * Leveraged trading via Avantis (1-10x long/short)
  * Polymarket prediction-market execution on Polygon

Plus a curated set of features that Bankr supports but where clawmes
also exposes a non-Bankr alternative (custodial wallet vs. WC, swaps
via Bankr router vs. direct 0x, server-side automations vs. local
plan scheduler, LLM credit gateway vs. user-supplied API keys).

Auth: ``BANKR_API_KEY`` env var, sent as a Bearer token. Free-tier
accounts work for read endpoints; signing / sending requires a paid
account.

Endpoints exposed at this milestone:

  * :meth:`get_account` — account metadata, per-chain addresses
  * :meth:`send_transaction` — submits a tx; returns hash
  * :meth:`sign_typed_data_v4` — EIP-712 signature
  * :meth:`sign_personal_message` — personal_sign signature
"""

from __future__ import annotations

import os
import threading
from typing import Any

from clawmes.lib.http import http_get, http_post
from clawmes.lib.logger import logger_for
from clawmes.services._base import Service

_log = logger_for("services.bankr")

_BASE_URL = "https://api.bankr.bot"


class BankrError(RuntimeError):
    """Raised on Bankr API failures."""

    def __init__(self, code: str, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class BankrService(Service):
    id = "clawmes.bankr"

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._api_key: str | None = None

    def start(self) -> None:
        with self._lock:
            self._api_key = os.environ.get("BANKR_API_KEY") or None
            if self._api_key:
                _log.info("bankr service started (api key configured)")
            else:
                _log.info("bankr service started (no api key — read-only)")

    def stop(self) -> None:
        with self._lock:
            self._api_key = None

    @property
    def has_credentials(self) -> bool:
        return self._api_key is not None

    # --- public methods ---

    def get_account(self) -> dict[str, Any]:
        """Return account metadata. Shape:

        .. code-block:: json

            {
              "user_id":   "...",
              "tier":      "free" | "pro",
              "addresses": {"1": "0x...", "8453": "0x...", ...},
              "credits":   {"balance_usd": 12.34, "currency": "usd"}
            }

        Raises :class:`BankrError` on auth/network failure.
        """
        return self._get("/v1/account")

    def send_transaction(
        self,
        *,
        chain_id: int,
        to: str,
        value: int = 0,
        data: bytes | str = b"",
        gas: int | None = None,
    ) -> str:
        payload: dict[str, Any] = {
            "chain_id": int(chain_id),
            "to": to,
            "value": hex(int(value)),
        }
        if data:
            payload["data"] = data if isinstance(data, str) else "0x" + data.hex()
        if gas is not None:
            payload["gas"] = hex(int(gas))
        result = self._post("/v1/tx/send", payload)
        tx_hash = result.get("tx_hash")
        if not isinstance(tx_hash, str):
            raise BankrError(
                "bad_response",
                "Bankr send_transaction did not return a tx_hash",
            )
        return tx_hash

    def sign_typed_data_v4(
        self,
        typed_data: dict[str, Any],
        *,
        chain_id: int | None = None,
    ) -> str:
        result = self._post(
            "/v1/sign/typed_data",
            {"typed_data": typed_data, "chain_id": chain_id},
        )
        sig = result.get("signature")
        if not isinstance(sig, str):
            raise BankrError("bad_response", "Bankr sign returned no signature")
        return sig

    def sign_personal_message(
        self,
        message: bytes | str,
        *,
        chain_id: int | None = None,
    ) -> str:
        if isinstance(message, bytes):
            message_hex = "0x" + message.hex()
        else:
            message_hex = message
        result = self._post(
            "/v1/sign/personal",
            {"message": message_hex, "chain_id": chain_id},
        )
        sig = result.get("signature")
        if not isinstance(sig, str):
            raise BankrError("bad_response", "Bankr sign returned no signature")
        return sig

    def request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Generic call into the Bankr API for the bankr_* tools
        (launch, automate, polymarket, leverage).

        Each Bankr-tier tool maps its actions to a specific endpoint
        and passes the user-supplied payload through. This lets the
        tools stay thin wrappers without hardcoding endpoint paths
        in three different places.
        """
        method = method.upper()
        if method == "GET":
            return self._get(path)
        if method == "POST":
            if body is None:
                body = {}
            return self._post(path, body)
        raise BankrError("api_error", f"unsupported method: {method}")

    # --- internals ---

    def _headers(self) -> dict[str, str]:
        with self._lock:
            api_key = self._api_key
        if not api_key:
            raise BankrError(
                "no_credentials",
                "BANKR_API_KEY not set. Get one at https://bankr.bot",
            )
        return {"Authorization": f"Bearer {api_key}"}

    def _get(self, path: str) -> dict[str, Any]:
        url = _BASE_URL + path
        try:
            return http_get(url, headers=self._headers(), timeout=15.0)
        except BankrError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise BankrError("network", f"GET {path} failed: {exc}") from exc

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        url = _BASE_URL + path
        try:
            return http_post(url, json=body, headers=self._headers(), timeout=30.0)
        except BankrError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise BankrError("network", f"POST {path} failed: {exc}") from exc


_instance: BankrService | None = None


def get_bankr_service() -> BankrService:
    global _instance
    if _instance is None:
        _instance = BankrService()
    return _instance
