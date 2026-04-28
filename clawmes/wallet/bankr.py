"""Bankr custodial wallet mode.

Routes every wallet operation through the Bankr HTTP API
(:mod:`clawmes.services.bankr_service`). Bankr signs and submits
transactions on the user's behalf — clawmes never sees a private key
in this mode.

Configuration: ``BANKR_API_KEY`` env var. Without it, ``connect``
raises a clear error and the user can switch to WC or local-key mode.
"""

from __future__ import annotations

from typing import Any

from clawmes.lib.logger import logger_for
from clawmes.services.bankr_service import BankrError, get_bankr_service
from clawmes.wallet._base import WalletMode
from clawmes.wallet.state import WalletState

_log = logger_for("wallet.bankr")


class BankrMode(WalletMode):
    name = "bankr"

    def __init__(self, *, api_key: str | None = None) -> None:
        # ``api_key`` is accepted for API symmetry but the service
        # singleton reads the env var on its own start. Tests can
        # inject a fake service via clawmes.services.bankr_service._instance.
        self._api_key = api_key
        self._state = WalletState.disconnected()

    # --- lifecycle ----------------------------------------------------

    def connect(self, **kwargs: Any) -> WalletState:
        """Authenticate to Bankr and materialize wallet state.

        Reads the user's account metadata; picks the address for the
        requested chain (or default 8453 = Base) and sets state to
        ``connected``.
        """
        chain_id = int(kwargs.get("chain_id") or 8453)
        svc = get_bankr_service()
        try:
            account = svc.get_account()
        except BankrError as exc:
            _log.warning("bankr connect failed: %s", exc)
            raise

        addresses = account.get("addresses") or {}
        # Bankr returns chain ids as string keys
        address = addresses.get(str(chain_id)) or addresses.get(chain_id)
        if not isinstance(address, str) or not address:
            raise BankrError(
                "no_address",
                f"Bankr account has no address for chain {chain_id}",
            )

        from clawmes.lib.chains import get_chain

        try:
            chain_name = get_chain(chain_id).name
        except KeyError:
            chain_name = f"chain {chain_id}"

        self._state = WalletState(
            connected=True,
            mode="bankr",
            address=address,
            chain_id=chain_id,
            chain_name=chain_name,
        )
        _log.info("bankr wallet connected: %s on %s", address, chain_name)
        return self._state

    def disconnect(self) -> None:
        self._state = WalletState.disconnected()

    def state(self) -> WalletState:
        return self._state

    # --- signing ------------------------------------------------------

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
        if not self._state.connected:
            raise BankrError("not_connected", "Bankr wallet not connected")
        target_chain = chain_id or self._state.chain_id or 8453
        # Bankr's send endpoint manages gas/fees internally; we ignore
        # max_fee_per_gas + max_priority_fee_per_gas at the API layer.
        # If the user wants explicit gas, they can pass `gas`.
        del max_fee_per_gas, max_priority_fee_per_gas
        return get_bankr_service().send_transaction(
            chain_id=target_chain,
            to=to,
            value=value,
            data=data,
            gas=gas,
        )

    def sign_typed_data_v4(self, typed_data: dict[str, Any]) -> str:
        if not self._state.connected:
            raise BankrError("not_connected", "Bankr wallet not connected")
        return get_bankr_service().sign_typed_data_v4(
            typed_data,
            chain_id=self._state.chain_id,
        )

    def sign_personal_message(self, message: bytes | str) -> str:
        if not self._state.connected:
            raise BankrError("not_connected", "Bankr wallet not connected")
        return get_bankr_service().sign_personal_message(
            message,
            chain_id=self._state.chain_id,
        )

    def switch_chain(self, chain_id: int) -> WalletState:
        """Switch chains by re-pulling the address map and picking
        the address for ``chain_id``.

        Bankr keeps a separate address per chain. We re-fetch every
        time rather than caching: the user could have added a chain
        on the Bankr web UI between the original connect and this
        switch, and we'd otherwise see a stale ``no_address`` error.
        """
        if not self._state.connected:
            raise BankrError("not_connected", "Bankr wallet not connected")

        account = get_bankr_service().get_account()
        addresses = account.get("addresses") or {}
        address = addresses.get(str(chain_id)) or addresses.get(chain_id)
        if not isinstance(address, str) or not address:
            raise BankrError(
                "no_address",
                f"Bankr account has no address for chain {chain_id}",
            )

        from clawmes.lib.chains import get_chain

        try:
            chain_name = get_chain(chain_id).name
        except KeyError:
            chain_name = f"chain {chain_id}"

        self._state = WalletState(
            connected=True,
            mode="bankr",
            address=address,
            chain_id=chain_id,
            chain_name=chain_name,
            balances=self._state.balances,
            policy_names=self._state.policy_names,
        )
        _log.info("bankr wallet switched to %s on %s", address, chain_name)
        return self._state
