"""Base Account / Coinbase Smart Wallet — wallet mode.

Wraps :class:`clawmes.services.base_account.BaseAccountService` in
the standard :class:`clawmes.wallet._base.WalletMode` shape so every
clawmes tool can treat Base Account as just another wallet mode
alongside WalletConnect, local-key, and Bankr.

Flow:

  1. ``connect`` returns immediately with an authorization URL the
     user should visit. The actual access token isn't set until the
     user completes OAuth and the caller invokes ``connect_with_code``.
  2. ``send_transaction`` / signing operations POST to Base Account's
     stored-request endpoint. The user approves in Base App.

Approval UX: every tx + signature triggers a Base App approval. Same
shape as WalletConnect's "tx goes to your phone" model — clawmes
never holds the user's private key.
"""

from __future__ import annotations

import json
from typing import Any

from clawmes.lib.logger import logger_for
from clawmes.services.base_account import BaseAccountError, get_base_account_service
from clawmes.wallet._base import WalletMode
from clawmes.wallet.state import WalletState

_log = logger_for("wallet.base_account")

#: Default chain for Base Account. The wallet is Base-native; cross-chain
#: support is gated on Coinbase exposing it server-side.
_DEFAULT_CHAIN_ID = 8453


class BaseAccountMode(WalletMode):
    name = "base_account"

    def __init__(self) -> None:
        self._state = WalletState.disconnected()
        self._pending_auth_url: str | None = None

    # ── lifecycle ───────────────────────────────────────────────────

    def connect(self, **kwargs: Any) -> WalletState:
        """Start the OAuth flow.

        Returns a ``disconnected`` :class:`WalletState` (since OAuth
        is async / out-of-band) but stashes the authorize URL on the
        mode so the caller can read it via :meth:`get_pending_auth_url`.
        """
        del kwargs
        svc = get_base_account_service()
        self._pending_auth_url = svc.get_auth_url()
        _log.info("base_account oauth pending; user must visit auth URL")
        return self._state

    def connect_with_code(self, code: str, *, chain_id: int | None = None) -> WalletState:
        """Complete the OAuth code exchange and materialize wallet state.

        Called after the user visits ``get_pending_auth_url()`` and
        retrieves the ``code`` query param from the redirect.
        """
        svc = get_base_account_service()
        svc.exchange_code(code)
        address = svc.get_user_address()

        target_chain = int(chain_id or _DEFAULT_CHAIN_ID)
        from clawmes.lib.chains import get_chain

        try:
            chain_name = get_chain(target_chain).name
        except KeyError:
            chain_name = f"chain {target_chain}"

        self._state = WalletState(
            connected=True,
            mode="base_account",
            address=address,
            chain_id=target_chain,
            chain_name=chain_name,
        )
        self._pending_auth_url = None
        _log.info("base_account connected: %s on %s", address, chain_name)
        return self._state

    def disconnect(self) -> None:
        get_base_account_service().stop()
        self._state = WalletState.disconnected()
        self._pending_auth_url = None

    def state(self) -> WalletState:
        return self._state

    def get_pending_auth_url(self) -> str | None:
        """Return the OAuth URL the user needs to visit, if any."""
        return self._pending_auth_url

    # ── signing / sending ───────────────────────────────────────────

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
            raise BaseAccountError(
                "not_connected", "Base Account not connected. Run /connect_base."
            )
        del max_fee_per_gas, max_priority_fee_per_gas  # Base Account manages gas
        target_chain = chain_id or self._state.chain_id or _DEFAULT_CHAIN_ID

        # Normalize calldata for JSON-RPC: hex string with 0x prefix.
        if isinstance(data, bytes):
            data_hex = "0x" + data.hex()
        else:
            data_hex = data if data.startswith("0x") else "0x" + data

        # eth_sendTransaction shape — most common request method for
        # Base Account's stored-request flow per the Base MCP spec.
        params: list[Any] = [
            {
                "from": self._state.address,
                "to": to,
                "value": hex(value) if value else "0x0",
                "data": data_hex,
                "chainId": hex(target_chain),
            }
        ]
        if gas is not None:
            params[0]["gas"] = hex(gas)

        svc = get_base_account_service()
        submitted = svc.submit_request(method="eth_sendTransaction", params=params)
        approval_url = submitted.get("approval_url")
        request_id = submitted["request_id"]
        if approval_url:
            _log.info("base_account approval required for %s: %s", request_id, approval_url)
        confirmed = svc.poll_request(request_id)
        tx_hash = confirmed.get("result") or confirmed.get("tx_hash")
        if not isinstance(tx_hash, str) or not tx_hash:
            raise BaseAccountError(
                "request_failed",
                f"approved request returned no tx hash: {confirmed!r}",
            )
        return tx_hash

    def sign_typed_data_v4(self, typed_data: dict[str, Any]) -> str:
        if not self._state.connected:
            raise BaseAccountError("not_connected", "Base Account not connected")
        svc = get_base_account_service()
        submitted = svc.submit_request(
            method="eth_signTypedData_v4",
            params=[self._state.address, json.dumps(typed_data)],
        )
        confirmed = svc.poll_request(submitted["request_id"])
        sig = confirmed.get("result") or confirmed.get("signature")
        if not isinstance(sig, str) or not sig:
            raise BaseAccountError(
                "request_failed", f"signTypedData returned no signature: {confirmed!r}"
            )
        return sig

    def sign_personal_message(self, message: bytes | str) -> str:
        if not self._state.connected:
            raise BaseAccountError("not_connected", "Base Account not connected")
        # personal_sign expects (message_hex, address) in JSON-RPC.
        if isinstance(message, bytes):
            message_hex = "0x" + message.hex()
        else:
            message_hex = message if message.startswith("0x") else "0x" + message.encode().hex()
        svc = get_base_account_service()
        submitted = svc.submit_request(
            method="personal_sign",
            params=[message_hex, self._state.address],
        )
        confirmed = svc.poll_request(submitted["request_id"])
        sig = confirmed.get("result") or confirmed.get("signature")
        if not isinstance(sig, str) or not sig:
            raise BaseAccountError(
                "request_failed", f"personal_sign returned no signature: {confirmed!r}"
            )
        return sig

    def switch_chain(self, chain_id: int) -> WalletState:
        """Switch the active chain — pure metadata update.

        Base Account exposes the same wallet address across every chain
        it supports; switching is just changing what chain id we hand
        to subsequent JSON-RPC requests.
        """
        if not self._state.connected:
            raise BaseAccountError("not_connected", "Base Account not connected")
        from clawmes.lib.chains import get_chain

        try:
            chain_name = get_chain(chain_id).name
        except KeyError:
            chain_name = f"chain {chain_id}"
        self._state = WalletState(
            connected=True,
            mode="base_account",
            address=self._state.address,
            chain_id=chain_id,
            chain_name=chain_name,
            balances=self._state.balances,
            policy_names=self._state.policy_names,
        )
        return self._state
