"""Tests for the /help command."""

from __future__ import annotations

import pytest

from clawmes.commands.help import handle_help


class TestHelp:
    @pytest.mark.asyncio
    async def test_full_help_no_args(self):
        out = await handle_help("")
        assert "Categories:" in out
        assert "trading" in out
        assert "/setup" in out

    @pytest.mark.asyncio
    async def test_category_trading(self):
        out = await handle_help("trading")
        assert "swap" in out.lower()
        assert "/dca" in out

    @pytest.mark.asyncio
    async def test_category_defi(self):
        out = await handle_help("defi")
        assert "/lend" in out
        assert "/stake" in out

    @pytest.mark.asyncio
    async def test_category_wallet(self):
        out = await handle_help("wallet")
        assert "/connect" in out
        assert "/balance" in out

    @pytest.mark.asyncio
    async def test_category_portfolio(self):
        out = await handle_help("portfolio")
        assert "/balance" in out
        assert "/cost" in out

    @pytest.mark.asyncio
    async def test_category_tools(self):
        out = await handle_help("tools")
        assert "/setup" in out

    @pytest.mark.asyncio
    async def test_category_agents(self):
        out = await handle_help("agents")
        assert "/agents" in out

    @pytest.mark.asyncio
    async def test_category_bankr(self):
        out = await handle_help("bankr")
        assert "/connect_bankr" in out
        assert "/topup" in out

    @pytest.mark.asyncio
    async def test_unknown_category(self):
        out = await handle_help("nonexistent")
        assert "Unknown category" in out
        assert "Categories:" in out

    @pytest.mark.asyncio
    async def test_case_insensitive(self):
        out = await handle_help("TRADING")
        # Same content as lowercase
        assert "swap" in out.lower()


class TestRegister:
    def test_registers_help_command(self):
        from clawmes.commands import help as help_mod

        recorded = []

        class FakeCtx:
            def register_command(self, **kw):
                recorded.append(kw)

        help_mod.register(FakeCtx())
        assert len(recorded) == 1
        assert recorded[0]["name"] == "help"
        assert recorded[0]["args_hint"] == "[category]"
