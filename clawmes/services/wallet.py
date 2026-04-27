"""Wallet service — singleton holding the active wallet mode + state.

This is the public face the rest of clawmes talks to. Implementations
(WalletConnect, local key, Bankr custodial) live under
``clawmes/wallet/`` and the service routes to whichever the user has
selected.

The service exposes the rich :class:`clawmes.wallet.state.WalletState`
(connected/mode/address/chain_id/chain_name/balances/policy_names) so
tools and the prompt builder can surface meaningful per-turn context.

Default state is disconnected; tools that need a wallet check
``state.connected`` and surface a clear error otherwise.
"""

from __future__ import annotations

import threading

from clawmes.lib.logger import logger_for
from clawmes.services._base import Service
from clawmes.wallet._base import WalletMode
from clawmes.wallet.state import WalletState

_log = logger_for("services.wallet")


class WalletConfigError(RuntimeError):
    """Raised when wallet setup is missing required configuration."""


class WalletService(Service):
    id = "clawmes.wallet"

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._mode: WalletMode | None = None

    def start(self) -> None:
        # Lazy: real key validation deferred to first use. Mode selection
        # happens when the tool/command layer calls set_mode (e.g. from
        # /connect or hermes clawmes init).
        pass

    def stop(self) -> None:
        with self._lock:
            mode = self._mode
        if mode is not None:
            try:
                mode.disconnect()
            except Exception:  # noqa: BLE001 — best-effort
                _log.exception("wallet mode disconnect raised on stop")

    # --- mode management ----------------------------------------------

    def set_mode(self, mode: WalletMode | None) -> None:
        """Replace the active wallet mode.

        Disconnects the previous mode if one was active. Pass ``None``
        to clear without setting a new mode.
        """
        with self._lock:
            previous = self._mode
            self._mode = mode
        if previous is not None:
            try:
                previous.disconnect()
            except Exception:  # noqa: BLE001 — best-effort cleanup
                _log.exception("previous mode disconnect raised during set_mode")
        if mode is not None:
            _log.info("wallet mode set to %s", mode.name)

    @property
    def active_mode(self) -> WalletMode | None:
        with self._lock:
            return self._mode

    @property
    def state(self) -> WalletState:
        with self._lock:
            mode = self._mode
        if mode is None:
            return WalletState.disconnected()
        try:
            return mode.state()
        except Exception:  # noqa: BLE001 — never propagate
            _log.exception("wallet mode state() raised; returning disconnected")
            return WalletState.disconnected()

    # --- WalletConnect convenience --------------------------------------

    def connect_walletconnect(self) -> WalletState:
        """Lazy-create the WC mode + bridge client and start pairing.

        Returns the resulting :class:`WalletState`. The returned state
        will have the pairing URI in ``state.balances['_pair_uri']`` —
        ``connected`` flips to True only after the user approves on
        their phone, which the WC notification consumer service
        catches and applies via ``_apply_session``.

        Raises :class:`WalletConfigError` if Node isn't installed or
        the bridge sources aren't bundled.
        """
        from clawmes.bridges.installer import ensure_node_bridges
        from clawmes.bridges.wc_client import WalletConnectClient
        from clawmes.wallet.walletconnect import WalletConnectMode

        paths = ensure_node_bridges()
        if paths.wc_entry is None:
            raise WalletConfigError(
                "WalletConnect bridge unavailable. Either Node ≥ 20 isn't "
                "installed or the bridge sources aren't bundled. "
                "Run `hermes clawmes doctor` for details."
            )

        client = WalletConnectClient(paths.wc_entry)
        client.start()
        mode = WalletConnectMode(client=client)
        self.set_mode(mode)
        return mode.connect()


_instance: WalletService | None = None


def get_wallet_service() -> WalletService:
    global _instance
    if _instance is None:
        _instance = WalletService()
    return _instance


def get_wallet_state() -> WalletState:
    return get_wallet_service().state
