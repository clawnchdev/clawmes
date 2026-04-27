"""Tests for clawmes.services.wc_notifications."""

from __future__ import annotations

import queue
import time
from unittest.mock import MagicMock

import pytest

from clawmes.bridges.process import Notification
from clawmes.services import wallet as wallet_module
from clawmes.services import wc_notifications as wc_module
from clawmes.services.wc_notifications import (
    WcNotificationConsumer,
    get_wc_notification_consumer,
)
from clawmes.wallet.walletconnect import WalletConnectMode


@pytest.fixture(autouse=True)
def _reset_singletons(monkeypatch):
    monkeypatch.setattr(wc_module, "_instance", None)
    monkeypatch.setattr(wallet_module, "_instance", None)


@pytest.fixture
def consumer():
    c = WcNotificationConsumer()
    yield c
    c.stop()


def _make_wc_mode_with_queue() -> tuple[WalletConnectMode, queue.Queue]:
    """Build a real WalletConnectMode whose client.notifications() is a Queue
    we can feed from tests."""
    q: queue.Queue[Notification] = queue.Queue()
    client = MagicMock()
    client.notifications.return_value = q
    mode = WalletConnectMode(client=client)
    return mode, q


# --- Lifecycle ------------------------------------------------------------


class TestLifecycle:
    def test_start_creates_thread(self, consumer):
        consumer.start()
        assert consumer._thread is not None
        assert consumer._thread.is_alive()

    def test_double_start_idempotent(self, consumer):
        consumer.start()
        first_thread = consumer._thread
        consumer.start()  # should not start a second thread
        assert consumer._thread is first_thread

    def test_stop_joins_thread(self, consumer):
        consumer.start()
        consumer.stop()
        assert consumer._thread is None

    def test_stop_when_never_started(self, consumer):
        consumer.stop()  # safe

    def test_singleton(self):
        a = get_wc_notification_consumer()
        b = get_wc_notification_consumer()
        assert a is b


# --- Idle behavior --------------------------------------------------------


class TestIdleBehavior:
    def test_idles_when_no_active_mode(self, consumer):
        # No mode set → consumer just polls
        consumer.start()
        time.sleep(0.1)
        assert consumer._thread.is_alive()
        consumer.stop()

    def test_idles_when_non_wc_mode(self, consumer):
        # Active mode that's NOT WalletConnect → consumer just polls
        non_wc = MagicMock()
        wallet_module.get_wallet_service().set_mode(non_wc)
        consumer.start()
        time.sleep(0.1)
        assert consumer._thread.is_alive()
        consumer.stop()

    def test_idles_when_mode_has_no_client(self, consumer, monkeypatch):
        mode = WalletConnectMode()  # no client injected
        wallet_module.get_wallet_service().set_mode(mode)
        consumer.start()
        time.sleep(0.1)
        assert consumer._thread.is_alive()
        consumer.stop()


# --- Notification handling ------------------------------------------------


class TestPairingApproved:
    def test_applies_session_with_first_account(self, consumer):
        mode, q = _make_wc_mode_with_queue()
        wallet_module.get_wallet_service().set_mode(mode)

        consumer.start()
        q.put(
            Notification(
                method="pairing_approved",
                params={
                    "topic": "abc",
                    "accounts": ["eip155:8453:0x" + "a" * 40],
                },
            )
        )
        # Wait for the consumer thread to process
        for _ in range(20):
            if mode.state().connected:
                break
            time.sleep(0.05)
        consumer.stop()

        s = mode.state()
        assert s.connected is True
        assert s.address == "0x" + "a" * 40
        assert s.chain_id == 8453

    def test_no_accounts_logged_no_state_change(self, consumer):
        mode, q = _make_wc_mode_with_queue()
        wallet_module.get_wallet_service().set_mode(mode)

        consumer.start()
        q.put(Notification(method="pairing_approved", params={"topic": "abc"}))
        time.sleep(0.2)
        consumer.stop()
        assert mode.state().connected is False

    def test_malformed_account_skipped(self, consumer):
        mode, q = _make_wc_mode_with_queue()
        wallet_module.get_wallet_service().set_mode(mode)

        consumer.start()
        q.put(
            Notification(
                method="pairing_approved",
                params={"accounts": ["totally-malformed-account-string"]},
            )
        )
        time.sleep(0.2)
        consumer.stop()
        assert mode.state().connected is False


class TestOtherNotifications:
    def test_pairing_rejected_logs_no_state_change(self, consumer):
        mode, q = _make_wc_mode_with_queue()
        wallet_module.get_wallet_service().set_mode(mode)

        consumer.start()
        q.put(
            Notification(
                method="pairing_rejected",
                params={"reason": "user denied"},
            )
        )
        time.sleep(0.1)
        consumer.stop()
        assert mode.state().connected is False

    def test_pairing_rejected_default_reason(self, consumer):
        # Hit the default-reason branch (no 'reason' key)
        mode, q = _make_wc_mode_with_queue()
        wallet_module.get_wallet_service().set_mode(mode)
        consumer.start()
        q.put(Notification(method="pairing_rejected", params={}))
        time.sleep(0.1)
        consumer.stop()

    def test_session_expired_disconnects(self, consumer):
        mode, q = _make_wc_mode_with_queue()
        wallet_module.get_wallet_service().set_mode(mode)
        # Pre-set connected state via _apply_session
        mode._apply_session(address="0x" + "a" * 40, chain_id=8453)
        assert mode.state().connected is True

        consumer.start()
        q.put(Notification(method="session_expired", params={}))
        for _ in range(20):
            if not mode.state().connected:
                break
            time.sleep(0.05)
        consumer.stop()
        assert mode.state().connected is False

    def test_relay_disconnected_logged(self, consumer):
        mode, q = _make_wc_mode_with_queue()
        wallet_module.get_wallet_service().set_mode(mode)
        consumer.start()
        q.put(Notification(method="relay_disconnected", params={}))
        time.sleep(0.1)
        consumer.stop()

    def test_relay_reconnected_logged(self, consumer):
        mode, q = _make_wc_mode_with_queue()
        wallet_module.get_wallet_service().set_mode(mode)
        consumer.start()
        q.put(Notification(method="relay_reconnected", params={}))
        time.sleep(0.1)
        consumer.stop()

    def test_unknown_notification_logged_no_crash(self, consumer):
        mode, q = _make_wc_mode_with_queue()
        wallet_module.get_wallet_service().set_mode(mode)
        consumer.start()
        q.put(Notification(method="some_future_event", params={"data": 1}))
        time.sleep(0.1)
        consumer.stop()


# --- Robustness -----------------------------------------------------------


class TestRobustness:
    def test_handler_exception_does_not_kill_thread(self, consumer, monkeypatch):
        mode, q = _make_wc_mode_with_queue()
        wallet_module.get_wallet_service().set_mode(mode)

        # Inject a handler exception by patching _apply_session
        def boom(*a, **kw):
            raise RuntimeError("simulated handler failure")

        monkeypatch.setattr(mode, "_apply_session", boom)

        consumer.start()
        q.put(
            Notification(
                method="pairing_approved",
                params={"accounts": ["eip155:1:0x" + "b" * 40]},
            )
        )
        time.sleep(0.2)
        # Thread still running despite the exception
        assert consumer._thread.is_alive()
        # And we can still process more notifications
        q.put(Notification(method="relay_reconnected", params={}))
        time.sleep(0.1)
        consumer.stop()
