"""Tests for clawmes.wallet.state (the richer WalletState).

The simpler stub at ``clawmes/services/wallet.py`` is tested separately
in tests/services/test_wallet_service.py.
"""

from __future__ import annotations

from clawmes.wallet.state import WalletState


class TestDisconnected:
    def test_disconnected_factory(self):
        s = WalletState.disconnected()
        assert s.connected is False
        assert s.mode is None

    def test_balance_summary_empty(self):
        s = WalletState.disconnected()
        assert s.balance_summary() == "(no cached balances)"

    def test_policy_summary_empty(self):
        s = WalletState.disconnected()
        assert s.policy_summary() == "(no policies configured)"


class TestForChain:
    def test_basic(self):
        s = WalletState.for_chain(
            mode="walletconnect",
            address="0x" + "a" * 40,
            chain_id=8453,
        )
        assert s.connected is True
        assert s.chain_name == "Base"
        assert s.address == "0x" + "a" * 40

    def test_balance_summary_with_balances(self):
        # Cover lines 58-59
        s = WalletState.for_chain(
            mode="walletconnect",
            address="0x" + "a" * 40,
            chain_id=8453,
            balances={"ETH": "1.5", "USDC": "1000"},
        )
        summary = s.balance_summary()
        # Sorted alphabetically; both tokens listed
        assert "ETH" in summary
        assert "USDC" in summary

    def test_balance_summary_caps_at_5(self):
        # Cover the [:5] slice (line 64)
        s = WalletState.for_chain(
            mode="walletconnect",
            address="0x",
            chain_id=8453,
            balances={f"T{i:02d}": str(i) for i in range(10)},
        )
        summary = s.balance_summary()
        # 5 entries → 4 commas at most
        assert summary.count(",") <= 4

    def test_policy_summary_with_names(self):
        s = WalletState.for_chain(
            mode="walletconnect",
            address="0x",
            chain_id=8453,
            policy_names=("p1", "p2"),
        )
        assert s.policy_summary() == "p1, p2"

    def test_immutable(self):
        from dataclasses import FrozenInstanceError

        s = WalletState.disconnected()
        try:
            s.connected = True
            raise AssertionError("should have raised")
        except FrozenInstanceError:
            pass
