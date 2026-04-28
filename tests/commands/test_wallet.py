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
    async def test_connect_surfaces_pair_uri(self, monkeypatch, tmp_path):
        # Mock connect_walletconnect to return a state with a pair URI
        from clawmes.services import wallet as wallet_svc
        from clawmes.wallet.state import WalletState

        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.setattr(wallet_svc, "_instance", None)

        svc = wallet_svc.get_wallet_service()
        monkeypatch.setattr(
            svc,
            "connect_walletconnect",
            lambda: WalletState(
                connected=False,
                mode="walletconnect",
                balances={"_pair_uri": "wc:abc@2"},
            ),
        )
        out = await wallet_cmd.handle_connect("")
        assert "wc:abc@2" in out
        assert "phone wallet" in out

    @pytest.mark.asyncio
    async def test_connect_no_uri_returned(self, monkeypatch, tmp_path):
        from clawmes.services import wallet as wallet_svc
        from clawmes.wallet.state import WalletState

        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.setattr(wallet_svc, "_instance", None)
        svc = wallet_svc.get_wallet_service()
        monkeypatch.setattr(
            svc,
            "connect_walletconnect",
            lambda: WalletState(connected=False, mode="walletconnect"),
        )
        out = await wallet_cmd.handle_connect("")
        assert "no URI was returned" in out

    @pytest.mark.asyncio
    async def test_connect_config_error(self, monkeypatch, tmp_path):
        from clawmes.services import wallet as wallet_svc

        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.setattr(wallet_svc, "_instance", None)
        svc = wallet_svc.get_wallet_service()

        def boom():
            raise wallet_svc.WalletConfigError("bridge unavailable")

        monkeypatch.setattr(svc, "connect_walletconnect", boom)
        out = await wallet_cmd.handle_connect("")
        assert "WalletConnect setup error" in out
        assert "bridge unavailable" in out

    @pytest.mark.asyncio
    async def test_connect_missing_project_id(self, monkeypatch, tmp_path):
        from clawmes.bridges.process import BridgeError
        from clawmes.services import wallet as wallet_svc

        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.setattr(wallet_svc, "_instance", None)
        svc = wallet_svc.get_wallet_service()

        def boom():
            raise BridgeError("config_error", "WALLETCONNECT_PROJECT_ID not set")

        monkeypatch.setattr(svc, "connect_walletconnect", boom)
        out = await wallet_cmd.handle_connect("")
        assert "WALLETCONNECT_PROJECT_ID" in out
        assert "cloud.walletconnect.com" in out

    @pytest.mark.asyncio
    async def test_connect_other_bridge_error(self, monkeypatch, tmp_path):
        from clawmes.bridges.process import BridgeError
        from clawmes.services import wallet as wallet_svc

        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.setattr(wallet_svc, "_instance", None)
        svc = wallet_svc.get_wallet_service()

        def boom():
            raise BridgeError("network", "relay unreachable")

        monkeypatch.setattr(svc, "connect_walletconnect", boom)
        out = await wallet_cmd.handle_connect("")
        assert "WalletConnect bridge error" in out

    @pytest.mark.asyncio
    async def test_disconnect_when_disconnected(self, monkeypatch, tmp_path):
        from clawmes.services import wallet as wallet_svc
        from clawmes.wallet.state import WalletState

        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.setattr(wallet_svc, "_instance", None)
        svc = wallet_svc.get_wallet_service()
        monkeypatch.setattr(svc, "disconnect", lambda: WalletState.disconnected())

        out = await wallet_cmd.handle_disconnect("")
        assert "No active wallet session" in out

    @pytest.mark.asyncio
    async def test_disconnect_walletconnect(self, monkeypatch, tmp_path):
        from clawmes.services import wallet as wallet_svc
        from clawmes.wallet.state import WalletState

        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.setattr(wallet_svc, "_instance", None)
        svc = wallet_svc.get_wallet_service()
        previous = WalletState(
            connected=True,
            mode="walletconnect",
            address="0x" + "a" * 40,
            chain_id=8453,
            chain_name="Base",
        )
        monkeypatch.setattr(svc, "disconnect", lambda: previous)

        out = await wallet_cmd.handle_disconnect("")
        assert "Disconnected WalletConnect session" in out
        assert "Base" in out
        # short-form address present
        assert "0xaaaa…aaaa" in out or "0xaaaa" in out

    @pytest.mark.asyncio
    async def test_disconnect_local_key(self, monkeypatch, tmp_path):
        from clawmes.services import wallet as wallet_svc
        from clawmes.wallet.state import WalletState

        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.setattr(wallet_svc, "_instance", None)
        svc = wallet_svc.get_wallet_service()
        previous = WalletState(
            connected=True,
            mode="local",
            address="0x" + "b" * 40,
            chain_id=8453,
            chain_name="Base",
        )
        monkeypatch.setattr(svc, "disconnect", lambda: previous)

        out = await wallet_cmd.handle_disconnect("")
        assert "Disconnected local-key session" in out

    @pytest.mark.asyncio
    async def test_disconnect_bankr(self, monkeypatch, tmp_path):
        from clawmes.services import wallet as wallet_svc
        from clawmes.wallet.state import WalletState

        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.setattr(wallet_svc, "_instance", None)
        svc = wallet_svc.get_wallet_service()
        previous = WalletState(
            connected=True,
            mode="bankr",
            address="0x" + "c" * 40,
            chain_id=8453,
            chain_name="Base",
        )
        monkeypatch.setattr(svc, "disconnect", lambda: previous)

        out = await wallet_cmd.handle_disconnect("")
        assert "Disconnected Bankr session" in out

    @pytest.mark.asyncio
    async def test_disconnect_unknown_mode_falls_back(self, monkeypatch, tmp_path):
        from clawmes.services import wallet as wallet_svc
        from clawmes.wallet.state import WalletState

        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.setattr(wallet_svc, "_instance", None)
        svc = wallet_svc.get_wallet_service()
        previous = WalletState(
            connected=True,
            mode="weird",
            address="0x" + "d" * 40,
            chain_id=999,
            chain_name=None,
        )
        monkeypatch.setattr(svc, "disconnect", lambda: previous)

        out = await wallet_cmd.handle_disconnect("")
        assert "Disconnected wallet session" in out
        assert "chain 999" in out


class TestConnectBankr:
    @pytest.mark.asyncio
    async def test_connect_bankr_success(self, monkeypatch, tmp_path):
        from clawmes.services import wallet as wallet_svc
        from clawmes.wallet.state import WalletState

        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.setattr(wallet_svc, "_instance", None)

        svc = wallet_svc.get_wallet_service()
        monkeypatch.setattr(
            svc,
            "connect_bankr",
            lambda: WalletState(
                connected=True,
                mode="bankr",
                address="0x" + "b" * 40,
                chain_id=8453,
                chain_name="Base",
            ),
        )
        out = await wallet_cmd.handle_connect_bankr("")
        assert "Bankr wallet connected" in out
        assert "0x" + "b" * 40 in out
        assert "Base" in out

    @pytest.mark.asyncio
    async def test_connect_bankr_no_credentials(self, monkeypatch, tmp_path):
        from clawmes.services import wallet as wallet_svc
        from clawmes.services.bankr_service import BankrError

        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.setattr(wallet_svc, "_instance", None)
        svc = wallet_svc.get_wallet_service()

        def boom():
            raise BankrError("no_credentials", "no key")

        monkeypatch.setattr(svc, "connect_bankr", boom)
        out = await wallet_cmd.handle_connect_bankr("")
        assert "BANKR_API_KEY" in out
        assert "bankr.bot" in out

    @pytest.mark.asyncio
    async def test_connect_bankr_other_error(self, monkeypatch, tmp_path):
        from clawmes.services import wallet as wallet_svc
        from clawmes.services.bankr_service import BankrError

        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.setattr(wallet_svc, "_instance", None)
        svc = wallet_svc.get_wallet_service()

        def boom():
            raise BankrError("network", "relay down")

        monkeypatch.setattr(svc, "connect_bankr", boom)
        out = await wallet_cmd.handle_connect_bankr("")
        assert "Bankr connect failed" in out
        assert "relay down" in out


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
    def test_registers_seven_commands(self):
        recorded = []

        class FakeCtx:
            def register_command(self, **kw):
                recorded.append(kw["name"])

        wallet_cmd.register(FakeCtx())
        assert set(recorded) == {
            "wallet",
            "connect",
            "connect_bankr",
            "disconnect",
            "mode",
            "chain",
            "address",
        }
