"""WalletConnect notification consumer.

Bridges the JSON-line notifications stream emitted by the Node WC
bridge into the Python wallet-mode state machine. Runs a background
thread that polls the active mode's bridge client and dispatches:

  ``pairing_approved`` → :meth:`WalletConnectMode._apply_session`
  ``pairing_rejected`` → log + state stays disconnected
  ``session_expired``  → :meth:`WalletConnectMode.disconnect`

Idempotent — safe to start/stop multiple times. Polls every 500ms
when no WC mode is active so a slow ``/connect`` doesn't burn CPU.
"""

from __future__ import annotations

import queue
import re
import threading

from clawmes.bridges.process import Notification
from clawmes.lib.logger import logger_for
from clawmes.services._base import Service
from clawmes.services.wallet import get_wallet_service
from clawmes.wallet.walletconnect import WalletConnectMode

_log = logger_for("services.wc_notifications")

_IDLE_POLL_SECONDS = 0.5
_QUEUE_TIMEOUT_SECONDS = 0.5


_ACCOUNT_RE = re.compile(r"^eip155:(\d+):(0x[0-9a-fA-F]{40})$")


class WcNotificationConsumer(Service):
    id = "clawmes.wc_notifications"

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="wc-notifications",
            daemon=True,
        )
        self._thread.start()
        _log.info("wc notification consumer started")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None

    # --- thread loop --------------------------------------------------

    def _run(self) -> None:
        while not self._stop_event.is_set():
            mode = self._active_wc_mode()
            if mode is None:
                self._stop_event.wait(timeout=_IDLE_POLL_SECONDS)
                continue
            client = getattr(mode, "_client", None)
            if client is None:
                self._stop_event.wait(timeout=_IDLE_POLL_SECONDS)
                continue
            try:
                notif = client.notifications().get(timeout=_QUEUE_TIMEOUT_SECONDS)
            except queue.Empty:
                continue
            try:
                self._handle(notif, mode)
            except Exception:  # noqa: BLE001 — never crash the consumer
                _log.exception("wc consumer: handler raised for %s", notif.method)

    def _active_wc_mode(self) -> WalletConnectMode | None:
        mode = get_wallet_service().active_mode
        if isinstance(mode, WalletConnectMode):
            return mode
        return None

    def _handle(self, notif: Notification, mode: WalletConnectMode) -> None:
        if notif.method == "pairing_approved":
            self._handle_pairing_approved(notif, mode)
        elif notif.method == "pairing_rejected":
            reason = notif.params.get("reason", "(no reason given)")
            _log.warning("wc pairing rejected: %s", reason)
        elif notif.method == "session_expired":
            _log.info("wc session expired; disconnecting")
            mode.disconnect()
        elif notif.method == "relay_disconnected":
            _log.warning("wc relay disconnected")
        elif notif.method == "relay_reconnected":
            _log.info("wc relay reconnected")
        else:
            _log.debug("wc consumer: unhandled notification %r", notif.method)

    def _handle_pairing_approved(
        self,
        notif: Notification,
        mode: WalletConnectMode,
    ) -> None:
        accounts = notif.params.get("accounts") or []
        if not accounts:
            _log.warning("wc pairing_approved had no accounts")
            return
        first = accounts[0]
        match = _ACCOUNT_RE.match(str(first))
        if not match:
            _log.warning("wc pairing_approved account malformed: %r", first)
            return
        chain_id = int(match.group(1))
        address = match.group(2)
        mode._apply_session(address=address, chain_id=chain_id)


_instance: WcNotificationConsumer | None = None


def get_wc_notification_consumer() -> WcNotificationConsumer:
    global _instance
    if _instance is None:
        _instance = WcNotificationConsumer()
    return _instance
