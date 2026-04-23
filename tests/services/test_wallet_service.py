"""Tests for clawmes.services.wallet (the service-layer WalletState stub).

Note: there are two WalletState classes by design at this milestone:
  * ``clawmes.services.wallet.WalletState`` — the simple stub used by the
    accessor pattern (this file)
  * ``clawmes.wallet.state.WalletState`` — the richer state used by the
    wallet mode implementations (covered separately in tests/wallet/test_state.py)

The duplication will be unified in v0.2.0 when the mode layer is wired
through.
"""

from __future__ import annotations

import pytest

from clawmes.services import wallet as wallet_mod
from clawmes.services.wallet import (
    WalletService,
    WalletState,
    get_wallet_service,
    get_wallet_state,
)


@pytest.fixture(autouse=True)
def _reset_singleton(monkeypatch):
    monkeypatch.setattr(wallet_mod, "_instance", None)


class TestWalletService:
    def test_start_stop_lifecycle(self):
        svc = WalletService()
        svc.start()  # cover line 47
        svc.stop()  # cover line 50

    def test_initial_state_disconnected(self):
        svc = WalletService()
        # cover line 54 — the state @property
        assert svc.state.connected is False
        assert svc.state.address is None


class TestSingleton:
    def test_returns_same_instance(self):
        a = get_wallet_service()
        b = get_wallet_service()
        assert a is b

    def test_initial_state_via_accessor(self):
        # cover line 68 — get_wallet_state delegates to get_wallet_service().state
        state = get_wallet_state()
        assert isinstance(state, WalletState)
        assert state.connected is False


class TestStateStubMethods:
    def test_balance_summary_returns_placeholder(self):
        # cover line 33
        s = WalletState()
        assert s.balance_summary() == "(not implemented)"

    def test_policy_summary_returns_placeholder(self):
        # cover line 36
        s = WalletState()
        assert s.policy_summary() == "(no policies configured)"
