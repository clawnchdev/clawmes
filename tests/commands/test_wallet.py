"""Tests for the /wallet, /connect, /disconnect, /mode, /chain, /address commands."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from clawmes.commands import wallet as wallet_cmd
from clawmes.wallet.state import WalletState

# Patch the binding inside the command module, not the source — the command
# does ``from clawmes.services.wallet import get_wallet_state`` so the alias
# is bound at import time and patching the source has no effect on a call
# that's already resolved.
_PATCH_PATH = "clawmes.commands.wallet.get_wallet_state"


@pytest.fixture
def disconnected_state():
    return WalletState.disconnected()


@pytest.fixture
def connected_state():
    return WalletState.for_chain(
        mode="walletconnect",
        address="0x" + "a" * 40,
        chain_id=8453,
    )


class TestWalletStatus:
    @pytest.mark.asyncio
    async def test_no_wallet(self, disconnected_state):
        with patch("clawmes.commands.wallet.get_wallet_state", return_value=disconnected_state):
            out = await wallet_cmd.handle_wallet("")
        assert "No wallet connected" in out
        assert "/connect" in out
        assert "/connect_bankr" in out
        assert "/connect_local" in out

    @pytest.mark.asyncio
    async def test_connected(self, connected_state):
        with patch("clawmes.commands.wallet.get_wallet_state", return_value=connected_state):
            out = await wallet_cmd.handle_wallet("")
        assert "Address:" in out
        assert "Chain:" in out
        assert "Mode:" in out
        assert "walletconnect" in out
        assert "Base" in out
        assert "0x" + "a" * 40 in out


class TestConnect:
    @pytest.mark.asyncio
    async def test_connect_stub(self):
        out = await wallet_cmd.handle_connect("")
        assert "not yet implemented" in out

    @pytest.mark.asyncio
    async def test_disconnect_when_disconnected(self, disconnected_state):
        with patch("clawmes.commands.wallet.get_wallet_state", return_value=disconnected_state):
            out = await wallet_cmd.handle_disconnect("")
        assert "No active wallet session" in out

    @pytest.mark.asyncio
    async def test_disconnect_when_connected(self, connected_state):
        with patch("clawmes.commands.wallet.get_wallet_state", return_value=connected_state):
            out = await wallet_cmd.handle_disconnect("")
        assert "not yet implemented" in out


class TestMode:
    @pytest.mark.asyncio
    async def test_show_current_mode(self, disconnected_state):
        with patch("clawmes.commands.wallet.get_wallet_state", return_value=disconnected_state):
            out = await wallet_cmd.handle_mode("")
        assert "Current wallet mode" in out

    @pytest.mark.asyncio
    async def test_show_current_mode_connected(self, connected_state):
        with patch("clawmes.commands.wallet.get_wallet_state", return_value=connected_state):
            out = await wallet_cmd.handle_mode("")
        assert "walletconnect" in out

    @pytest.mark.asyncio
    async def test_invalid_mode(self):
        out = await wallet_cmd.handle_mode("nonsense")
        assert "Unknown mode" in out
        assert "walletconnect" in out  # lists valid choices

    @pytest.mark.asyncio
    async def test_valid_mode_change_stubbed(self):
        out = await wallet_cmd.handle_mode("walletconnect")
        assert "not yet implemented" in out

    @pytest.mark.asyncio
    async def test_valid_mode_local(self):
        out = await wallet_cmd.handle_mode("local")
        assert "not yet implemented" in out

    @pytest.mark.asyncio
    async def test_valid_mode_bankr(self):
        out = await wallet_cmd.handle_mode("bankr")
        assert "not yet implemented" in out

    @pytest.mark.asyncio
    async def test_case_insensitive(self):
        out = await wallet_cmd.handle_mode("BANKR")
        assert "not yet implemented" in out


class TestChain:
    @pytest.mark.asyncio
    async def test_show_current(self, connected_state):
        with patch("clawmes.commands.wallet.get_wallet_state", return_value=connected_state):
            out = await wallet_cmd.handle_chain("")
        assert "Current chain" in out
        assert "Base" in out

    @pytest.mark.asyncio
    async def test_show_none(self, disconnected_state):
        with patch("clawmes.commands.wallet.get_wallet_state", return_value=disconnected_state):
            out = await wallet_cmd.handle_chain("")
        assert "Current chain" in out

    @pytest.mark.asyncio
    async def test_with_arg_stub(self):
        out = await wallet_cmd.handle_chain("8453")
        assert "not yet implemented" in out


class TestAddress:
    @pytest.mark.asyncio
    async def test_no_wallet(self, disconnected_state):
        with patch("clawmes.commands.wallet.get_wallet_state", return_value=disconnected_state):
            out = await wallet_cmd.handle_address("")
        assert "No wallet connected" in out

    @pytest.mark.asyncio
    async def test_connected(self, connected_state):
        with patch("clawmes.commands.wallet.get_wallet_state", return_value=connected_state):
            out = await wallet_cmd.handle_address("")
        assert "0x" + "a" * 40 == out


class TestRegister:
    def test_registers_six_commands(self):
        recorded = []

        class FakeCtx:
            def register_command(self, **kw):
                recorded.append(kw["name"])

        wallet_cmd.register(FakeCtx())
        assert set(recorded) == {
            "wallet",
            "connect",
            "disconnect",
            "mode",
            "chain",
            "address",
        }
