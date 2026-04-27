"""WalletConnect v2 mode.

Delegates to the Node ``clawmes-wc-bridge`` subprocess via JSON-line RPC
(see ``clawmes/bridges/wc_client.py``). Every write tx is forwarded to
the user's phone wallet for approval; clawmes never holds private keys
in this mode.

Sign methods route through ``request_signature`` on the bridge:
  * ``send_transaction`` → ``eth_sendTransaction``
  * ``sign_typed_data_v4`` → ``eth_signTypedData_v4``
  * ``sign_personal_message`` → ``personal_sign``
"""

from __future__ import annotations

import json
from typing import Any

from clawmes.bridges.process import BridgeError
from clawmes.bridges.wc_client import WalletConnectClient
from clawmes.lib.logger import logger_for
from clawmes.wallet._base import WalletMode
from clawmes.wallet.state import WalletState

_log = logger_for("wallet.walletconnect")


class WalletConnectMode(WalletMode):
    name = "walletconnect"

    def __init__(
        self,
        *,
        client: WalletConnectClient | None = None,
        project_id: str | None = None,
    ) -> None:
        self._project_id = project_id
        self._client = client  # injected for tests; real init in connect()
        self._state = WalletState.disconnected()
        self._active_topic: str | None = None

    # --- lifecycle ----------------------------------------------------

    def connect(self, **kwargs: Any) -> WalletState:
        """Generate a pairing URI and return it via the WalletState.

        The actual wallet pairing happens out-of-band: the user scans
        the QR / opens the deep link on their phone, approves, and the
        bridge emits a ``pairing_approved`` notification we handle in
        :meth:`_apply_session`.

        At this milestone we expose the pairing URI through the state
        so the caller (tool / command layer) can surface it to the
        user. Full session population happens on the notification.
        """
        if self._client is None:
            raise RuntimeError(
                "WalletConnectMode requires a WalletConnectClient — "
                "use ensure_node_bridges() and pass the entry to construct one"
            )
        try:
            result = self._client.pair()
        except BridgeError as exc:
            _log.warning("pair failed: %s", exc)
            raise

        uri = result.get("uri", "")
        topic = result.get("topic", "")
        self._active_topic = topic
        # The state stays "disconnected" until the user actually approves
        # on their phone (which produces a pairing_approved notification);
        # we store the URI in balances for the caller to display until
        # then. A richer state with `pending_pair` would be nicer but
        # this keeps the WalletState shape stable for v0.1.
        self._state = WalletState(
            connected=False,
            mode="walletconnect",
            balances={"_pair_uri": uri},
        )
        return self._state

    def disconnect(self) -> None:
        if self._client is None or self._active_topic is None:
            self._state = WalletState.disconnected()
            return
        try:
            self._client.disconnect()
        except BridgeError as exc:
            _log.warning("disconnect failed: %s", exc)
        self._active_topic = None
        self._state = WalletState.disconnected()

    def state(self) -> WalletState:
        return self._state

    # --- signing -------------------------------------------------------

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
        if self._client is None or self._active_topic is None:
            raise RuntimeError("no active WalletConnect session — call connect() first")

        tx: dict[str, Any] = {"to": to, "value": hex(value)}
        if data:
            tx["data"] = data.hex() if isinstance(data, bytes) else data
        if gas is not None:
            tx["gas"] = hex(gas)
        if max_fee_per_gas is not None:
            tx["maxFeePerGas"] = hex(max_fee_per_gas)
        if max_priority_fee_per_gas is not None:
            tx["maxPriorityFeePerGas"] = hex(max_priority_fee_per_gas)
        if self._state.address is not None:
            tx["from"] = self._state.address

        return self._client.request_signature(
            method="eth_sendTransaction",
            params=[tx],
            metadata={"chain_id": chain_id},
        )

    def sign_typed_data_v4(self, typed_data: dict[str, Any]) -> str:
        if self._client is None or self._active_topic is None:
            raise RuntimeError("no active WalletConnect session — call connect() first")

        addr = self._state.address or ""
        return self._client.request_signature(
            method="eth_signTypedData_v4",
            params=[addr, json.dumps(typed_data)],
        )

    def sign_personal_message(self, message: bytes | str) -> str:
        if self._client is None or self._active_topic is None:
            raise RuntimeError("no active WalletConnect session — call connect() first")

        msg_hex = message.hex() if isinstance(message, bytes) else _to_hex(message)
        addr = self._state.address or ""
        return self._client.request_signature(
            method="personal_sign",
            params=[msg_hex, addr],
        )

    # --- notification handler -----------------------------------------

    def _apply_session(self, *, address: str, chain_id: int) -> None:
        """Called by the bridge-notification consumer when pairing
        completes on the user's phone.

        Parses the chain id and address out of the notification
        payload and updates the wallet state to ``connected``.
        """
        from clawmes.lib.chains import get_chain

        try:
            chain = get_chain(chain_id)
            chain_name = chain.name
        except KeyError:
            chain_name = f"chain {chain_id}"

        self._state = WalletState(
            connected=True,
            mode="walletconnect",
            address=address,
            chain_id=chain_id,
            chain_name=chain_name,
        )
        _log.info(
            "WC session active: %s on %s (chain id %d)",
            address,
            chain_name,
            chain_id,
        )


def _to_hex(s: str) -> str:
    """Encode a string as 0x-prefixed UTF-8 bytes for personal_sign."""
    return "0x" + s.encode("utf-8").hex()
