"""Tests for the pre_llm_call (prompt builder) hook."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from clawmes.hooks.prompt_builder import MAX_INJECT_CHARS, callback
from clawmes.services import command_history as command_history_module
from clawmes.services import mode_service as mode_module
from clawmes.services import persona_service as persona_module
from clawmes.wallet.state import WalletState


@pytest.fixture(autouse=True)
def _reset_singletons(monkeypatch):
    """Each test gets a fresh mode + persona + command-history service."""
    monkeypatch.setattr(mode_module, "_instance", None)
    monkeypatch.setattr(persona_module, "_instance", None)
    monkeypatch.setattr(command_history_module, "_instance", None)


class TestNoSources:
    def test_all_sources_empty_returns_none(self):
        with patch("clawmes.services.wallet.get_wallet_state") as get:
            get.return_value = WalletState.disconnected()
            assert callback(session_key="s") is None

    def test_no_session_key(self):
        with patch("clawmes.services.wallet.get_wallet_state") as get:
            get.return_value = WalletState.disconnected()
            assert callback() is None


class TestWalletSource:
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
        text = result["context"]
        assert "connected=" + ("0x" + "a" * 40) in text
        assert "chain=Base" in text
        assert "mode=walletconnect" in text

    def test_wallet_read_failure_does_not_propagate(self):
        with patch("clawmes.services.wallet.get_wallet_state") as get:
            get.side_effect = RuntimeError("simulated wallet failure")
            # No pieces collected → None
            assert callback(session_key="s") is None


class TestModeSource:
    def test_readonly_injects_warning(self):
        mode_module.get_mode_service().set_mode("readonly")
        with patch("clawmes.services.wallet.get_wallet_state") as get:
            get.return_value = WalletState.disconnected()
            result = callback()
        assert isinstance(result, dict)
        assert "readonly mode active" in result["context"]
        assert "/safemode off" in result["context"]

    def test_danger_injects_warning(self):
        mode_module.get_mode_service().set_mode("danger")
        with patch("clawmes.services.wallet.get_wallet_state") as get:
            get.return_value = WalletState.disconnected()
            result = callback()
        assert isinstance(result, dict)
        assert "danger mode active" in result["context"]

    def test_normal_mode_injects_nothing(self):
        # Default is "normal" — no context for mode
        with patch("clawmes.services.wallet.get_wallet_state") as get:
            get.return_value = WalletState.disconnected()
            assert callback() is None

    def test_mode_read_failure_does_not_propagate(self, monkeypatch):
        from clawmes.services import mode_service as mod

        def boom():
            raise RuntimeError("simulated")

        monkeypatch.setattr(mod, "get_mode_service", boom)
        with patch("clawmes.services.wallet.get_wallet_state") as get:
            get.return_value = WalletState.disconnected()
            assert callback() is None


class TestPersonaSource:
    def test_active_persona_injects_snippet(self):
        persona_module.get_persona_service().set_persona("degen")
        with patch("clawmes.services.wallet.get_wallet_state") as get:
            get.return_value = WalletState.disconnected()
            result = callback()
        assert isinstance(result, dict)
        assert "[clawmes/persona]" in result["context"]
        # Snippet text — at least mentions degen lingo
        assert "degen" in result["context"].lower() or "ct" in result["context"].lower()

    def test_no_persona_injects_nothing(self):
        # No active persona — and disconnected wallet, normal mode
        with patch("clawmes.services.wallet.get_wallet_state") as get:
            get.return_value = WalletState.disconnected()
            assert callback() is None

    def test_persona_read_failure_does_not_propagate(self, monkeypatch):
        from clawmes.services import persona_service as mod

        def boom():
            raise RuntimeError("simulated")

        monkeypatch.setattr(mod, "get_persona_service", boom)
        with patch("clawmes.services.wallet.get_wallet_state") as get:
            get.return_value = WalletState.disconnected()
            assert callback() is None


class TestCombined:
    def test_wallet_plus_mode_plus_persona(self):
        mode_module.get_mode_service().set_mode("danger")
        persona_module.get_persona_service().set_persona("technical")
        connected = WalletState.for_chain(
            mode="walletconnect",
            address="0x" + "a" * 40,
            chain_id=8453,
        )
        with patch("clawmes.services.wallet.get_wallet_state") as get:
            get.return_value = connected
            result = callback()
        text = result["context"]
        assert "[clawmes/wallet]" in text
        assert "[clawmes/mode]" in text
        assert "[clawmes/persona]" in text


class TestTruncation:
    def test_truncation_at_max_chars(self):
        long_addr = "0x" + "f" * 40
        connected = WalletState.for_chain(
            mode="walletconnect",
            address=long_addr,
            chain_id=8453,
        )
        with patch("clawmes.services.wallet.get_wallet_state") as get:
            get.return_value = connected
            with patch("clawmes.hooks.prompt_builder.MAX_INJECT_CHARS", 10):
                result = callback(session_key="s")
        assert isinstance(result, dict)
        assert "[clawmes/context truncated]" in result["context"]


class TestConstants:
    def test_max_inject_chars_reasonable(self):
        assert MAX_INJECT_CHARS > 0
        assert MAX_INJECT_CHARS <= 100_000
