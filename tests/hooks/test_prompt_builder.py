"""Tests for the pre_llm_call (prompt builder) hook."""

from __future__ import annotations

from unittest.mock import patch

from clawmes.hooks.prompt_builder import MAX_INJECT_CHARS, callback
from clawmes.wallet.state import WalletState


class TestNoWallet:
    def test_disconnected_returns_none(self):
        with patch("clawmes.services.wallet.get_wallet_state") as get:
            get.return_value = WalletState.disconnected()
            assert callback(session_key="s") is None

    def test_no_session_key(self):
        with patch("clawmes.services.wallet.get_wallet_state") as get:
            get.return_value = WalletState.disconnected()
            assert callback() is None


class TestWithWallet:
    def test_connected_wallet_injects_context(self):
        connected = WalletState.for_chain(
            mode="walletconnect",
            address="0x" + "a" * 40,
            chain_id=8453,
        )
        with patch("clawmes.services.wallet.get_wallet_state") as get:
            get.return_value = connected
            result = callback(session_key="s")

        assert isinstance(result, dict)
        assert "context" in result
        text = result["context"]
        assert "connected=" + ("0x" + "a" * 40) in text
        assert "chain=Base" in text
        assert "mode=walletconnect" in text

    def test_truncation_at_max_chars(self):
        # Force the wallet read to "succeed" with an absurdly long address
        # so we exercise the truncation branch.
        long_addr = "0x" + "f" * 40
        connected = WalletState.for_chain(
            mode="walletconnect",
            address=long_addr,
            chain_id=8453,
        )
        with patch("clawmes.services.wallet.get_wallet_state") as get:
            get.return_value = connected
            # Lower the cap to force truncation in this test
            with patch(
                "clawmes.hooks.prompt_builder.MAX_INJECT_CHARS",
                10,
            ):
                result = callback(session_key="s")

        assert isinstance(result, dict)
        assert "[clawmes/context truncated]" in result["context"]


class TestWalletReadFailure:
    def test_exception_does_not_propagate(self):
        with patch("clawmes.services.wallet.get_wallet_state") as get:
            get.side_effect = RuntimeError("simulated wallet failure")
            # Returns None (no pieces collected) but does not raise
            assert callback(session_key="s") is None


class TestConstants:
    def test_max_inject_chars_reasonable(self):
        # Basic sanity — guards against accidental zeroing
        assert MAX_INJECT_CHARS > 0
        assert MAX_INJECT_CHARS <= 100_000
