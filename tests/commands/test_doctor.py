"""Tests for the /doctor command."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from clawmes.commands import doctor as doctor_mod
from clawmes.commands.doctor import (
    _api_keys_section,
    _bridge_section,
    _check_keys,
    _format,
    _plugin_section,
    _render,
    _rpc_section,
    _wallet_section,
    handle_doctor,
)
from clawmes.wallet.state import WalletState


class TestFormat:
    def test_empty(self):
        assert _format([]) == "  (none)"

    def test_padding(self):
        rows = [
            ("[ok] ", "short", "ENV1", "hint"),
            ("[----]", "longer-label", "ENV_LONGER", ""),
        ]
        out = _format(rows)
        # Both rows include their labels, padded to the longer
        assert "short       " in out
        assert "longer-label" in out


class TestCheckKeys:
    def test_set_marks_ok(self, monkeypatch):
        monkeypatch.setenv("FAKE_KEY", "value")
        rows = _check_keys([("FAKE_KEY", "label", "url")])
        assert "ok" in rows[0][0]
        # No hint when set
        assert rows[0][3] == ""

    def test_unset_marks_dash(self, monkeypatch):
        monkeypatch.delenv("FAKE_KEY", raising=False)
        rows = _check_keys([("FAKE_KEY", "label", "https://signup")])
        assert "----" in rows[0][0]
        assert rows[0][3] == "https://signup"


class TestWalletSection:
    def test_disconnected(self):
        with patch.object(doctor_mod, "get_wallet_state", return_value=WalletState.disconnected()):
            section = _wallet_section()
        assert section.title == "WALLET"
        assert "no wallet connected" in section.body
        assert "/connect" in section.body

    def test_connected(self):
        state = WalletState.for_chain(
            mode="local",
            address="0x" + "ab" * 20,
            chain_id=8453,
            balances={"ETH": "0.01"},
        )
        with patch.object(doctor_mod, "get_wallet_state", return_value=state):
            section = _wallet_section()
        assert "Mode:    local" in section.body
        assert "0xabab" in section.body
        assert "Base" in section.body or "8453" in section.body


class TestRpcSection:
    def test_all_default(self, monkeypatch):
        # Wipe any CLAWMES_RPC_* env vars
        for key in list(__import__("os").environ.keys()):
            if key.startswith("CLAWMES_RPC_"):
                monkeypatch.delenv(key, raising=False)
        section = _rpc_section()
        assert "[default]" in section.body
        assert "public-node defaults" in section.body

    def test_user_override_for_one_chain(self, monkeypatch):
        for key in list(__import__("os").environ.keys()):
            if key.startswith("CLAWMES_RPC_"):
                monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("CLAWMES_RPC_8453", "https://my-base-rpc.example")
        section = _rpc_section()
        assert "[custom]" in section.body
        assert "[default]" in section.body


class TestApiKeysSection:
    def test_groups_present(self, monkeypatch):
        # Clear all env vars used by the section
        for env in (
            "ZEROX_API_KEY",
            "LIFI_API_KEY",
            "TALLY_API_KEY",
            "RESERVOIR_API_KEY",
            "WALLETCONNECT_PROJECT_ID",
            "BANKR_API_KEY",
        ):
            monkeypatch.delenv(env, raising=False)
        section = _api_keys_section()
        assert section.title == "API KEYS"
        # All group headings render
        assert "Wallet modes" in section.body
        assert "Trading" in section.body
        assert "Specialized" in section.body

    def test_set_keys_marked_ok(self, monkeypatch):
        monkeypatch.setenv("ZEROX_API_KEY", "deadbeef")
        section = _api_keys_section()
        # Find the line for ZEROX_API_KEY and confirm it's marked ok
        lines = [ln for ln in section.body.splitlines() if "ZEROX_API_KEY" in ln]
        assert lines, "ZEROX_API_KEY row not found"
        assert "[ok]" in lines[0]


class TestBridgeSection:
    def test_with_node(self, monkeypatch):
        import shutil

        monkeypatch.setattr(shutil, "which", lambda _: "/usr/local/bin/node")
        section = _bridge_section()
        assert "Node.js runtime" in section.body
        assert "[ok]" in section.body

    def test_without_node(self, monkeypatch):
        import shutil

        monkeypatch.setattr(shutil, "which", lambda _: None)
        section = _bridge_section()
        assert "[----]" in section.body
        assert "install Node" in section.body

    def test_dist_missing(self, monkeypatch, tmp_path):
        # Stub Path.exists to return False for the dist entry
        real_exists = Path.exists

        def fake_exists(self):
            if "dist" in str(self) and "index.mjs" in str(self):
                return False
            return real_exists(self)

        monkeypatch.setattr(Path, "exists", fake_exists)
        section = _bridge_section()
        assert "WC bridge built" in section.body
        # The dist line should be marked not-ok
        lines = [ln for ln in section.body.splitlines() if "WC bridge built" in ln]
        assert "[----]" in lines[0]

    def test_project_id_set(self, monkeypatch):
        monkeypatch.setenv("WALLETCONNECT_PROJECT_ID", "proj-123")
        section = _bridge_section()
        lines = [ln for ln in section.body.splitlines() if "WC project ID" in ln]
        assert "[ok]" in lines[0]


class TestPluginSection:
    def test_real_manifest_counts(self):
        section = _plugin_section()
        assert "Tools registered:" in section.body
        # 45 at 0.1.0 + policy_manage + agent_identity + bv7x + bv7x_oracle
        # + bv7x_market + eas_attestation + a2a_call = 52
        assert "52" in section.body
        assert "Hooks registered:" in section.body
        assert "11" in section.body  # 11 hooks
        assert "Commands registered:" in section.body

    def test_manifest_missing(self, monkeypatch):
        # Patch Path.exists to return False for plugin.yaml
        real_exists = Path.exists

        def fake_exists(self):
            if self.name == "plugin.yaml":
                return False
            return real_exists(self)

        monkeypatch.setattr(Path, "exists", fake_exists)
        section = _plugin_section()
        assert "missing" in section.body.lower()

    def test_count_commands_resilient_to_failure(self, monkeypatch):
        """If register_all raises, we degrade to 0 instead of crashing."""
        from clawmes.commands import doctor as dmod

        def boom(_ctx):
            raise RuntimeError("boom")

        # Patch the import target inside _count_commands
        from clawmes import commands as commands_pkg

        monkeypatch.setattr(commands_pkg, "register_all", boom)
        # Trigger a manifest read so we hit _count_commands
        section = dmod._plugin_section()
        # Output renders even if commands count is 0
        assert "Commands registered: 0" in section.body


class TestRender:
    def test_render_includes_all_sections(self):
        from clawmes.commands.doctor import _Section

        sections = [
            _Section("ALPHA", "  alpha-body"),
            _Section("BETA", "  beta-body"),
        ]
        out = _render(sections)
        assert "clawmes doctor" in out
        assert "ALPHA" in out
        assert "BETA" in out
        assert "alpha-body" in out
        assert "beta-body" in out


class TestHandleDoctor:
    @pytest.mark.asyncio
    async def test_full_output_renders(self):
        out = await handle_doctor("")
        # Must contain all section titles
        for header in (
            "WALLET",
            "RPC ENDPOINTS",
            "API KEYS",
            "WALLETCONNECT BRIDGE",
            "PLUGIN MANIFEST",
            "CLAWNCH PREMIUM",
        ):
            assert header in out


class TestPremiumSection:
    def test_premium_section_includes_tier(self):
        from clawmes.commands.doctor import _premium_section

        sec = _premium_section()
        assert sec.title == "CLAWNCH PREMIUM"
        assert "Active tier" in sec.body
        assert "Pro threshold" in sec.body
        assert "Max threshold" in sec.body
        assert "Verifier" in sec.body

    def test_premium_section_fallback_when_service_unavailable(self, monkeypatch):
        from clawmes.commands import doctor as dmod
        from clawmes.services import clawnch_premium as cp_mod

        def _explode():
            raise RuntimeError("singleton borked")

        monkeypatch.setattr(cp_mod, "get_clawnch_premium_service", _explode)
        sec = dmod._premium_section()
        assert sec.title == "CLAWNCH PREMIUM"
        assert "not available" in sec.body

    @pytest.mark.asyncio
    async def test_ignores_args(self):
        # Args are accepted but unused
        out = await handle_doctor("verbose")
        assert "WALLET" in out


class TestRegister:
    def test_registers_doctor_command(self):
        recorded = []

        class FakeCtx:
            def register_command(self, **kw):
                recorded.append(kw)

        doctor_mod.register(FakeCtx())
        assert len(recorded) == 1
        assert recorded[0]["name"] == "doctor"
        assert "Health-check" in recorded[0]["description"]
