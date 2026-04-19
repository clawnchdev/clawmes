"""WalletConnect v2 mode.

Delegates to the Node ``clawmes-wc-bridge`` subprocess via JSON-line RPC
(see ``clawmes/bridges/wc_client.py``). Every write tx is forwarded to
the user's phone wallet for approval; clawmes never holds private keys
in this mode.
"""

from __future__ import annotations

from typing import Any

from clawmes.lib.logger import logger_for
from clawmes.wallet._base import WalletMode
from clawmes.wallet.state import WalletState

_log = logger_for("wallet.walletconnect")


class WalletConnectMode(WalletMode):
    name = "walletconnect"

    def __init__(self, project_id: str | None = None) -> None:
        self._project_id = project_id
        self._state = WalletState.disconnected()

    def connect(self, **kwargs: Any) -> WalletState:
        # TODO(v0.1.0): spawn clawmes-wc-bridge via bridges.wc_client,
        # call pair(), surface the URI to the user, await
        # pairing_approved notification, then materialize WalletState.
        _log.info("walletconnect connect requested (stub)")
        return self._state

    def disconnect(self) -> None:
        # TODO(v0.1.0): bridges.wc_client.disconnect()
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
            "WalletConnect bridge not wired in this milestone. "
            "Forthcoming: clawmes/bridges/wc_client.request_signature()"
        )

    def sign_typed_data_v4(self, typed_data: dict[str, Any]) -> str:
        raise NotImplementedError(
            "WalletConnect bridge not wired in this milestone."
        )
