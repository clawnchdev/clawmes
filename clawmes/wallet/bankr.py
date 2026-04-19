"""Bankr custodial wallet mode.

Multi-chain custodial wallet with on-chain account abstraction. Uses
HTTP API at ``api.bankr.bot``. Required for:

  * Avantis leverage (1-10x long/short)
  * Polymarket prediction markets (Polygon)
  * Token launches via Bankr's gas-sponsored deploy flow

Configuration: ``BANKR_API_KEY`` env. Optional ``BANKR_LLM_KEY`` for
the inference-credit gateway.
"""

from __future__ import annotations

from typing import Any

from clawmes.lib.logger import logger_for
from clawmes.wallet._base import WalletMode
from clawmes.wallet.state import WalletState

_log = logger_for("wallet.bankr")


class BankrMode(WalletMode):
    name = "bankr"

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key
        self._state = WalletState.disconnected()

    def connect(self, **kwargs: Any) -> WalletState:
        # TODO(v0.1.0): GET /account with the API key, materialize
        # WalletState including all chain addresses.
        _log.info("bankr connect requested (stub)")
        return self._state

    def disconnect(self) -> None:
        self._state = WalletState.disconnected()

    def state(self) -> WalletState:
        return self._state

    def send_transaction(
        self,
        *,
        to: str,
        value: int,
        data: bytes | str = b"",
        chain_id: int | None = None,
        gas: int | None = None,
        max_fee_per_gas: int | None = None,
        max_priority_fee_per_gas: int | None = None,
    ) -> str:
        raise NotImplementedError(
            "Bankr send not wired in this milestone. "
            "Forthcoming: POST /tx via services.bankr_service."
        )

    def sign_typed_data_v4(self, typed_data: dict[str, Any]) -> str:
        raise NotImplementedError("Bankr typed-data signing not wired in this milestone.")
