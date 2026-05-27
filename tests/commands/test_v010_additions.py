"""Tests for v0.10.0 additions to existing commands.

* ``/copy add --pct`` and the ``_compute_copy_amount`` / ``_get_tx_eth_value``
  helpers.
* ``/portfolio pnl|realized|unrealized|export`` subcommands.
* ``/wallet tag|tags|untag|show`` subcommands.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from clawmes.commands import balance as balance_cmd
from clawmes.commands import copy
from clawmes.commands import wallet as wallet_cmd

# ── /copy --pct ─────────────────────────────────────────────────────


@pytest.fixture
def tmp_copy_state(tmp_path, monkeypatch):
    p = tmp_path / "follows.json"
    monkeypatch.setattr(copy, "_follows_path", lambda: p)
    return p


@pytest.fixture
def fake_basescan(monkeypatch):
    state: dict[str, Any] = {"tx_value": None, "raises": None}

    def _fake(url, *, params=None, timeout=None):  # noqa: ARG001
        if state["raises"] is not None:
            raise state["raises"]
        action = (params or {}).get("action")
        if action == "eth_getTransactionByHash":
            return state["tx_value"]
        return None

    monkeypatch.setattr(copy, "http_get", _fake)
    return state


class TestCopyPctValidation:
    def test_pct_requires_holder(self, tmp_copy_state, monkeypatch):
        import clawmes.services.token_gate as tg

        monkeypatch.setattr(
            tg,
            "check_tier_or_error",
            lambda *a, **k: "/copy --pct requires holding at least 10,000,000 $CLAWNCH.",
        )
        monkeypatch.setattr(copy, "_current_block_height", lambda: 1000)
        out = copy._cmd_add("u", ["0x" + "a" * 40, "0.001", "--pct", "50"])
        assert "requires holding" in out

    def test_pct_bad_number(self, tmp_copy_state, monkeypatch):
        monkeypatch.setattr(copy, "_current_block_height", lambda: 1000)
        out = copy._cmd_add("u", ["0x" + "a" * 40, "0.001", "--pct", "abc"])
        assert "--pct must be a number" in out

    def test_pct_out_of_range_low(self, tmp_copy_state, monkeypatch):
        monkeypatch.setattr(copy, "_current_block_height", lambda: 1000)
        out = copy._cmd_add("u", ["0x" + "a" * 40, "0.001", "--pct", "0"])
        assert "between 0 and 1000" in out

    def test_pct_out_of_range_high(self, tmp_copy_state, monkeypatch):
        monkeypatch.setattr(copy, "_current_block_height", lambda: 1000)
        out = copy._cmd_add("u", ["0x" + "a" * 40, "0.001", "--pct", "9999"])
        assert "between 0 and 1000" in out

    def test_pct_success(self, tmp_copy_state, monkeypatch):
        monkeypatch.setattr(copy, "_current_block_height", lambda: 1000)
        out = copy._cmd_add("u", ["0x" + "a" * 40, "0.001", "--pct", "50"])
        assert "Pct sizing:  50.0% of target tx" in out
        follow = copy._load_state()["follows"][0]
        assert follow["pct"] == 50.0


class TestComputeCopyAmount:
    def test_no_pct_returns_base(self, fake_basescan):
        follow = {"eth_per_copy": 0.001, "pct": None}
        amount = copy._compute_copy_amount(follow, {"hash": "0xabc"})
        assert amount == 0.001

    def test_pct_no_hash_falls_back(self, fake_basescan):
        follow = {"eth_per_copy": 0.001, "pct": 50.0}
        amount = copy._compute_copy_amount(follow, {})
        assert amount == 0.001  # fallback

    def test_pct_zero_target_falls_back(self, fake_basescan):
        """Target tx had no ETH value (token-token swap, etc.) → fallback."""
        fake_basescan["tx_value"] = {"result": {"value": "0x0"}}
        follow = {"eth_per_copy": 0.001, "pct": 50.0}
        amount = copy._compute_copy_amount(follow, {"hash": "0xabc"})
        assert amount == 0.001

    def test_pct_scales(self, fake_basescan):
        # Target sent 0.1 ETH; pct=50 → 0.05 ETH; cap=0.001 → 0.001
        fake_basescan["tx_value"] = {"result": {"value": hex(int(0.1 * 1e18))}}
        follow = {"eth_per_copy": 0.001, "pct": 50.0}
        amount = copy._compute_copy_amount(follow, {"hash": "0xabc"})
        # Scaled to 0.05 but capped at 0.001.
        assert amount == 0.001

    def test_pct_below_cap(self, fake_basescan):
        # Target sent 0.0001 ETH; pct=50 → 0.00005 ETH (below 0.001 cap)
        fake_basescan["tx_value"] = {"result": {"value": hex(int(0.0001 * 1e18))}}
        follow = {"eth_per_copy": 0.001, "pct": 50.0}
        amount = copy._compute_copy_amount(follow, {"hash": "0xabc"})
        # 0.0001 * 0.5 = 0.00005 (uncapped)
        assert amount == pytest.approx(0.00005, rel=1e-9)


class TestGetTxEthValue:
    def test_http_error(self, fake_basescan):
        fake_basescan["raises"] = RuntimeError("down")
        assert copy._get_tx_eth_value("0xabc") == 0

    def test_non_dict_body(self, fake_basescan):
        fake_basescan["tx_value"] = "junk"
        assert copy._get_tx_eth_value("0xabc") == 0

    def test_no_result(self, fake_basescan):
        fake_basescan["tx_value"] = {"result": None}
        assert copy._get_tx_eth_value("0xabc") == 0

    def test_result_not_dict(self, fake_basescan):
        fake_basescan["tx_value"] = {"result": "string"}
        assert copy._get_tx_eth_value("0xabc") == 0

    def test_no_value_key(self, fake_basescan):
        fake_basescan["tx_value"] = {"result": {"other": "thing"}}
        assert copy._get_tx_eth_value("0xabc") == 0

    def test_value_not_hex(self, fake_basescan):
        fake_basescan["tx_value"] = {"result": {"value": "garbage"}}
        assert copy._get_tx_eth_value("0xabc") == 0

    def test_value_invalid_hex(self, fake_basescan):
        fake_basescan["tx_value"] = {"result": {"value": "0xZZZ"}}
        assert copy._get_tx_eth_value("0xabc") == 0

    def test_value_success(self, fake_basescan):
        fake_basescan["tx_value"] = {"result": {"value": "0x3e8"}}
        assert copy._get_tx_eth_value("0xabc") == 1000

    def test_api_key_passed(self, monkeypatch):
        captured = {}

        def _spy(url, *, params=None, timeout=None):  # noqa: ARG001
            captured.update(params)
            return {"result": {"value": "0x0"}}

        monkeypatch.setattr(copy, "http_get", _spy)
        monkeypatch.setenv("BASESCAN_API_KEY", "MYKEY")
        copy._get_tx_eth_value("0xabc")
        assert captured.get("apikey") == "MYKEY"


# ── /portfolio v2 ──────────────────────────────────────────────────


@pytest.fixture
def fake_wallet_for_portfolio(monkeypatch):
    from dataclasses import dataclass

    @dataclass
    class _State:
        connected: bool = True
        address: str = "0x" + "1" * 40
        chain_id: int = 8453

    state = _State()
    # balance.py imports get_wallet_state at module-load time, so we
    # have to patch the binding inside balance.py (not the source module).
    monkeypatch.setattr(balance_cmd, "get_wallet_state", lambda: state)
    return state


@pytest.fixture
def fake_cost_basis(monkeypatch):
    state: dict[str, Any] = {"payload": None}

    def _fake(args):  # noqa: ARG001
        return json.dumps(state["payload"])

    import clawmes.tools.cost_basis as mod

    monkeypatch.setattr(mod, "cost_basis", _fake)
    return state


@pytest.fixture
def fake_defi_balance(monkeypatch):
    state: dict[str, Any] = {"payload": None}

    def _fake(args):  # noqa: ARG001
        return json.dumps(state["payload"])

    import clawmes.tools.defi_balance as mod

    monkeypatch.setattr(mod, "defi_balance", _fake)
    return state


class TestPortfolioPnl:
    async def test_pnl_routes_to_cost_basis(self, fake_cost_basis):
        fake_cost_basis["payload"] = {
            "isError": False,
            "content": [{"text": "Realized: $10.00"}],
        }
        out = await balance_cmd.handle_portfolio("pnl")
        assert "Realized: $10.00" in out

    async def test_realized(self, fake_cost_basis):
        fake_cost_basis["payload"] = {
            "isError": False,
            "content": [{"text": "Realized only"}],
        }
        out = await balance_cmd.handle_portfolio("realized")
        assert "Realized only" in out

    async def test_unrealized(self, fake_cost_basis):
        fake_cost_basis["payload"] = {
            "isError": False,
            "content": [{"text": "Open lots"}],
        }
        out = await balance_cmd.handle_portfolio("unrealized")
        assert "Open lots" in out

    async def test_export(self, fake_cost_basis):
        fake_cost_basis["payload"] = {
            "isError": False,
            "content": [{"text": "Full ledger"}],
        }
        out = await balance_cmd.handle_portfolio("export")
        assert "Full ledger" in out

    async def test_pnl_bad_json(self, monkeypatch):
        import clawmes.tools.cost_basis as mod

        monkeypatch.setattr(mod, "cost_basis", lambda *a, **k: "not-json")
        out = await balance_cmd.handle_portfolio("pnl")
        assert "bad response" in out

    async def test_pnl_isError(self, fake_cost_basis):
        fake_cost_basis["payload"] = {
            "isError": True,
            "content": [{"text": "ledger missing"}],
        }
        out = await balance_cmd.handle_portfolio("pnl")
        assert "ledger missing" in out

    async def test_pnl_empty_content(self, fake_cost_basis):
        fake_cost_basis["payload"] = {"isError": False}
        out = await balance_cmd.handle_portfolio("pnl")
        assert "empty result" in out


class TestPortfolioBalance:
    async def test_balance_summary_hints_pnl(self, fake_wallet_for_portfolio, fake_defi_balance):
        """The default balance summary appends a P&L-views hint."""
        fake_defi_balance["payload"] = {
            "isError": False,
            "content": [{"text": "Native: 1.0 ETH"}],
        }
        out = await balance_cmd.handle_portfolio("")
        assert "Native: 1.0 ETH" in out
        assert "P&L views" in out

    async def test_balance_no_wallet(self, monkeypatch):
        class _Disc:
            connected = False
            address = ""
            chain_id = None

        monkeypatch.setattr(balance_cmd, "get_wallet_state", lambda: _Disc())
        out = await balance_cmd.handle_portfolio("")
        assert "No wallet connected" in out


# ── /wallet tag/tags/untag/show ────────────────────────────────────


@pytest.fixture
def tmp_tags(tmp_path, monkeypatch):
    p = tmp_path / "tags.json"
    monkeypatch.setattr(wallet_cmd, "_tags_path", lambda: p)
    return p


@pytest.fixture
def fake_wallet_state(monkeypatch):
    from dataclasses import dataclass

    @dataclass
    class _State:
        connected: bool = True
        address: str = "0x" + "abcdef" + "0" * 34
        chain_id: int = 8453
        chain_name: str = "base"
        mode: str = "local"

        def balance_summary(self):
            return "1.0 ETH"

        def policy_summary(self):
            return "none"

    state = _State()
    monkeypatch.setattr(wallet_cmd, "get_wallet_state", lambda: state)
    return state


class TestTagsStateIO:
    def test_load_missing(self, tmp_tags):
        assert wallet_cmd._load_tags() == {}

    def test_load_bad_json(self, tmp_tags):
        tmp_tags.write_text("not-json")
        assert wallet_cmd._load_tags() == {}

    def test_load_not_dict(self, tmp_tags):
        tmp_tags.write_text(json.dumps([]))
        assert wallet_cmd._load_tags() == {}

    def test_load_filters_non_dict_values(self, tmp_tags):
        tmp_tags.write_text(json.dumps({"a": {"x": 1}, "b": "junk"}))
        assert wallet_cmd._load_tags() == {"a": {"x": 1}}

    def test_roundtrip(self, tmp_tags):
        t = {"trading": {"address": "0xabc"}}
        wallet_cmd._save_tags(t)
        assert wallet_cmd._load_tags() == t

    def test_default_path(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        assert wallet_cmd._tags_path().name == "tags.json"


class TestCmdTag:
    async def test_no_args_active_wallet(self, fake_wallet_state, tmp_tags):
        # /wallet with no args shows status when wallet connected.
        out = await wallet_cmd.handle_wallet("")
        assert fake_wallet_state.address in out
        assert "Tag this wallet" in out

    async def test_tag_usage(self, tmp_tags):
        out = await wallet_cmd.handle_wallet("tag")
        assert "Usage:" in out

    async def test_tag_no_wallet(self, monkeypatch, tmp_tags):
        class _Disc:
            connected = False
            address = ""

        monkeypatch.setattr(wallet_cmd, "get_wallet_state", lambda: _Disc())
        out = await wallet_cmd.handle_wallet("tag trading")
        assert "Connect one first" in out

    async def test_tag_success(self, fake_wallet_state, tmp_tags):
        out = await wallet_cmd.handle_wallet("tag trading")
        assert "Tagged active wallet" in out
        tags = wallet_cmd._load_tags()
        assert "trading" in tags
        assert tags["trading"]["address"] == fake_wallet_state.address

    async def test_tag_handles_missing_chain(self, monkeypatch, tmp_tags):
        from dataclasses import dataclass

        @dataclass
        class _State:
            connected: bool = True
            address: str = "0x" + "1" * 40
            chain_id: int | None = None
            chain_name: str = ""
            mode: str = ""

            def balance_summary(self):
                return ""

            def policy_summary(self):
                return ""

        monkeypatch.setattr(wallet_cmd, "get_wallet_state", lambda: _State())
        out = await wallet_cmd.handle_wallet("tag x")
        assert "Tagged active wallet" in out


class TestCmdTagsList:
    async def test_empty(self, tmp_tags):
        out = await wallet_cmd.handle_wallet("tags")
        assert "No wallet tags saved" in out

    async def test_list_tags_alias(self, tmp_tags):
        out = await wallet_cmd.handle_wallet("list_tags")
        assert "No wallet tags saved" in out

    async def test_lists_existing(self, fake_wallet_state, tmp_tags):
        await wallet_cmd.handle_wallet("tag trading")
        out = await wallet_cmd.handle_wallet("tags")
        assert "trading" in out
        assert "base" in out


class TestCmdUntag:
    async def test_usage(self, tmp_tags):
        out = await wallet_cmd.handle_wallet("untag")
        assert "Usage:" in out

    async def test_not_found(self, tmp_tags):
        out = await wallet_cmd.handle_wallet("untag missing")
        assert "No tag named" in out

    async def test_success(self, fake_wallet_state, tmp_tags):
        await wallet_cmd.handle_wallet("tag trading")
        out = await wallet_cmd.handle_wallet("untag trading")
        assert "Removed tag" in out
        assert wallet_cmd._load_tags() == {}


class TestCmdShow:
    async def test_usage(self, tmp_tags):
        out = await wallet_cmd.handle_wallet("show")
        assert "Usage:" in out

    async def test_not_found(self, tmp_tags):
        out = await wallet_cmd.handle_wallet("show missing")
        assert "No tag named" in out

    async def test_success(self, fake_wallet_state, tmp_tags):
        await wallet_cmd.handle_wallet("tag trading")
        out = await wallet_cmd.handle_wallet("show trading")
        assert "Tag 'trading'" in out
        assert fake_wallet_state.address in out


class TestUnknownSubcommandFallsThrough:
    async def test_unknown_sub_with_wallet(self, fake_wallet_state, tmp_tags):
        """An unknown subcommand falls through to the normal wallet status."""
        out = await wallet_cmd.handle_wallet("totally_unknown_sub")
        assert fake_wallet_state.address in out

    async def test_unknown_sub_no_wallet(self, monkeypatch, tmp_tags):
        class _Disc:
            connected = False
            address = ""

        monkeypatch.setattr(wallet_cmd, "get_wallet_state", lambda: _Disc())
        out = await wallet_cmd.handle_wallet("garbage")
        assert "No wallet connected" in out
        assert "/wallet tags" in out  # hint shown
