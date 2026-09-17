"""Tests for clawmes.tools.clawnch_launch."""

from __future__ import annotations

import json

import pytest

from clawmes.services import clawnch as cl_mod
from clawmes.services.clawnch import ClawnchError
from clawmes.tools.clawnch_launch import clawnch_launch, register


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(cl_mod, "_instance", None)


class _FakeSvc:
    """In-place replacement for ClawnchService."""

    def __init__(self):
        self.deploys: list[dict] = []
        self.infos: list[str] = []
        self.deploy_return: dict = {"txHash": "0xtx", "tokenAddress": "0xtok"}
        self.deploy_raise: Exception | None = None
        self.info_return: dict = {"name": "X"}
        self.info_raise: Exception | None = None
        self.prepare_raises: Exception | None = None
        self.prepare_calls: list[dict] = []
        self.rh_ticket_return: dict = {
            "ok": True,
            "data": {"to": "0xrouter", "data": "0xdead", "value": "0x1", "chainId": 4663},
            "ticket": {"agent": "0x" + "1" * 40},
            "meta": {
                "chain": "robinhood",
                "depositAddress": "0xdeposit",
                "creationFeeWei": "20000000000000000",
            },
        }
        self.rh_ticket_raises: Exception | None = None
        self.rh_ticket_calls: list[dict] = []
        self.rh_confirm_return: dict = {"ok": True, "launch": {"token": "0x" + "a" * 40}}
        self.rh_confirm_raises: Exception | None = None
        self.rh_confirm_calls: list[str] = []
        self.rh_deposit_return: dict = {"ok": True, "launch": {"token": "0x" + "a" * 40}}
        self.rh_deposit_raises: Exception | None = None
        self.rh_deposit_calls: list[dict] = []
        self.rh_token_info_return: dict = {
            "chain": "robinhood",
            "chain_id": 4663,
            "token_address": "0x6a50F139F3eD4C9c7bDa0D067c5Ed09De1EEBbeA",
            "trade_url": "https://bags.fm/token/0x6a50F139F3eD4C9c7bDa0D067c5Ed09De1EEBbeA",
        }

    def deploy(self, *, token_params, bypass_tx_hash=None, burn_tx_hash=None):
        self.deploys.append(
            {
                "params": token_params,
                "bypass": bypass_tx_hash,
                "burn": burn_tx_hash,
            }
        )
        if self.deploy_raise:
            raise self.deploy_raise
        return self.deploy_return

    def get_launch(self, token):
        self.infos.append(token)
        if self.info_raise:
            raise self.info_raise
        return self.info_return

    def prepare_deploy(self, **kwargs):
        self.prepare_calls.append(kwargs)
        if self.prepare_raises:
            raise self.prepare_raises
        return self.deploy_return

    def rh_ticket(self, **kwargs):
        self.rh_ticket_calls.append(kwargs)
        if self.rh_ticket_raises:
            raise self.rh_ticket_raises
        return self.rh_ticket_return

    def rh_confirm_launch(self, *, tx_hash):
        self.rh_confirm_calls.append(tx_hash)
        if self.rh_confirm_raises:
            raise self.rh_confirm_raises
        return self.rh_confirm_return

    def rh_deposit_launch(self, **kwargs):
        self.rh_deposit_calls.append(kwargs)
        if self.rh_deposit_raises:
            raise self.rh_deposit_raises
        return self.rh_deposit_return

    def rh_token_info(self):
        return self.rh_token_info_return


@pytest.fixture
def fake_svc(monkeypatch):
    s = _FakeSvc()
    monkeypatch.setattr(
        "clawmes.services.clawnch.get_clawnch_service",
        lambda: s,
    )
    return s


class TestDeploy:
    def test_requires_name(self, fake_svc):
        out = json.loads(clawnch_launch({"action": "deploy", "symbol": "X"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "param_error"

    def test_requires_symbol(self, fake_svc):
        out = json.loads(clawnch_launch({"action": "deploy", "name": "Foo"}))
        assert out["isError"] is True

    def test_basic_deploy(self, fake_svc):
        out = json.loads(clawnch_launch({"action": "deploy", "name": "Foo", "symbol": "FOO"}))
        assert out["details"]["txHash"] == "0xtx"
        assert "Launched FOO" in out["content"][0]["text"]
        # source defaulted by service, not by tool
        assert fake_svc.deploys[0]["params"] == {"name": "Foo", "symbol": "FOO"}

    def test_deploy_attaches_receipt_preview(self, fake_svc):
        # Desktop UI: a launch-receipt card path is surfaced at the envelope
        # top level (result.preview) where the desktop chat tool-card reads it.
        out = json.loads(clawnch_launch({"action": "deploy", "name": "Foo", "symbol": "FOO"}))
        assert out["preview"].endswith(".html")
        assert "launch-foo" in out["preview"].lower()
        assert "preview" not in out["details"]

    def test_deploy_card_failure_is_swallowed(self, fake_svc, monkeypatch):
        # A UI rendering failure must never break the actual launch.
        import clawmes.lib.ui_cards as ui_cards

        def _boom(*_a, **_k):
            raise RuntimeError("render failed")

        monkeypatch.setattr(ui_cards, "write_card", _boom)
        out = json.loads(clawnch_launch({"action": "deploy", "name": "Foo", "symbol": "FOO"}))
        assert out["details"]["txHash"] == "0xtx"
        assert "preview" not in out

    def test_with_description_image(self, fake_svc):
        clawnch_launch(
            {
                "action": "deploy",
                "name": "Foo",
                "symbol": "FOO",
                "description": "the foo coin",
                "image": "https://x/foo.png",
            }
        )
        params = fake_svc.deploys[0]["params"]
        assert params["description"] == "the foo coin"
        assert params["image"] == "https://x/foo.png"

    def test_with_socials_normalized(self, fake_svc):
        clawnch_launch(
            {
                "action": "deploy",
                "name": "Foo",
                "symbol": "FOO",
                "twitter": "clawn",
                "website": "https://mycoin.xyz",
                "telegram": "@clawnchalerts",
                "farcaster": "clawn",
                "discord": "https://discord.gg/abc",
            }
        )
        params = fake_svc.deploys[0]["params"]
        urls = {entry["platform"]: entry["url"] for entry in params["metadata"]["socialMediaUrls"]}
        assert urls["twitter"] == "https://x.com/clawn"
        assert urls["website"] == "https://mycoin.xyz"
        assert urls["telegram"] == "https://t.me/clawnchalerts"
        assert urls["farcaster"] == "https://warpcast.com/clawn"
        assert urls["discord"] == "https://discord.gg/abc"

    def test_full_url_pass_through(self, fake_svc):
        clawnch_launch(
            {
                "action": "deploy",
                "name": "Foo",
                "symbol": "FOO",
                "twitter": "https://x.com/already-formatted",
            }
        )
        params = fake_svc.deploys[0]["params"]
        urls = params["metadata"]["socialMediaUrls"]
        assert urls[0]["url"] == "https://x.com/already-formatted"

    def test_bare_hostname_without_base_url_gets_https(self, fake_svc):
        # website has no base URL — bare-hostname autocomplete applies
        clawnch_launch(
            {
                "action": "deploy",
                "name": "Foo",
                "symbol": "FOO",
                "website": "mycoin.xyz",
            }
        )
        params = fake_svc.deploys[0]["params"]
        urls = params["metadata"]["socialMediaUrls"]
        assert urls[0]["url"] == "https://mycoin.xyz"

    def test_no_metadata_when_no_socials(self, fake_svc):
        clawnch_launch({"action": "deploy", "name": "Foo", "symbol": "FOO"})
        params = fake_svc.deploys[0]["params"]
        assert "metadata" not in params

    def test_handle_only_at_falls_back(self, fake_svc):
        # Edge case: bare @ for twitter — falls back to raw value
        clawnch_launch(
            {
                "action": "deploy",
                "name": "Foo",
                "symbol": "FOO",
                "twitter": "@",
            }
        )
        params = fake_svc.deploys[0]["params"]
        urls = params["metadata"]["socialMediaUrls"]
        assert urls[0]["url"] == "@"

    def test_non_url_passthrough_for_url_fields(self, fake_svc):
        # website has empty base_url + the value doesn't look like a URL
        clawnch_launch(
            {
                "action": "deploy",
                "name": "Foo",
                "symbol": "FOO",
                "website": "not a url",
            }
        )
        params = fake_svc.deploys[0]["params"]
        urls = params["metadata"]["socialMediaUrls"]
        assert urls[0]["url"] == "not a url"

    def test_with_bypass(self, fake_svc):
        clawnch_launch(
            {
                "action": "deploy",
                "name": "Foo",
                "symbol": "FOO",
                "bypass_tx_hash": "0xbeef",
            }
        )
        assert fake_svc.deploys[0]["bypass"] == "0xbeef"

    def test_with_burn(self, fake_svc):
        clawnch_launch(
            {
                "action": "deploy",
                "name": "Foo",
                "symbol": "FOO",
                "burn_tx_hash": "0x" + "a" * 64,
            }
        )
        assert fake_svc.deploys[0]["burn"] == "0x" + "a" * 64

    def test_clawnch_error_surfaces_code(self, fake_svc):
        fake_svc.deploy_raise = ClawnchError("rate_limited", "wait 24h")
        out = json.loads(clawnch_launch({"action": "deploy", "name": "Foo", "symbol": "FOO"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "rate_limited"

    def test_burn_required_error_includes_instructions(self, fake_svc):
        fake_svc.deploy_raise = ClawnchError(
            "burn_required",
            "This launch path now requires a verified 1,000,000 $CLAWNCH burn.",
            meta={
                "minBurnTokens": "1000000",
                "burnAddress": "0x000000000000000000000000000000000000dEaD",
            },
        )
        out = json.loads(clawnch_launch({"action": "deploy", "name": "Foo", "symbol": "FOO"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "burn_required"
        text = out["content"][0]["text"]
        assert "1000000" in text
        assert "0x000000000000000000000000000000000000dEaD" in text
        assert "burn_tx_hash" in text

    def test_burn_required_error_without_meta_uses_defaults(self, fake_svc):
        fake_svc.deploy_raise = ClawnchError("burn_required", "burn first")
        out = json.loads(clawnch_launch({"action": "deploy", "name": "Foo", "symbol": "FOO"}))
        assert out["details"]["error_code"] == "burn_required"
        assert "dEaD" in out["content"][0]["text"]

    def test_unexpected_error(self, fake_svc):
        fake_svc.deploy_raise = RuntimeError("boom")
        out = json.loads(clawnch_launch({"action": "deploy", "name": "Foo", "symbol": "FOO"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "api_error"

    def test_tx_hash_only_no_token_address(self, fake_svc):
        # Some upstream responses might only have txHash; tool should still render
        fake_svc.deploy_return = {"txHash": "0xtx"}
        out = json.loads(clawnch_launch({"action": "deploy", "name": "Foo", "symbol": "FOO"}))
        assert "0xtx" in out["content"][0]["text"]

    def test_snake_case_keys_handled(self, fake_svc):
        # Service might return tx_hash + token_address instead of camelCase
        fake_svc.deploy_return = {"tx_hash": "0xtx", "token_address": "0xtok"}
        out = json.loads(clawnch_launch({"action": "deploy", "name": "Foo", "symbol": "FOO"}))
        assert "0xtok" in out["content"][0]["text"]


class TestInfo:
    def test_requires_token(self, fake_svc):
        out = json.loads(clawnch_launch({"action": "info"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "param_error"

    def test_basic_info(self, fake_svc):
        out = json.loads(clawnch_launch({"action": "info", "token": "0xabc"}))
        assert out["details"] == {"name": "X"}

    def test_clawnch_error(self, fake_svc):
        fake_svc.info_raise = ClawnchError("not_found", "no such token")
        out = json.loads(clawnch_launch({"action": "info", "token": "0xabc"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "not_found"

    def test_unexpected_error(self, fake_svc):
        fake_svc.info_raise = RuntimeError("boom")
        out = json.loads(clawnch_launch({"action": "info", "token": "0xabc"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "api_error"


class TestRegister:
    def test_registers_one_tool(self):
        captured = []

        class FakeCtx:
            def register_tool(self, **kw):
                captured.append(kw["name"])

        register(FakeCtx())
        assert captured == ["clawnch_launch"]


# ──────────────────────────────────────────────────────────────────────
#  Robinhood Chain actions
# ──────────────────────────────────────────────────────────────────────

_ADDR = "0x" + "a" * 40
_WALLET = "0x" + "1" * 40
_TX = "0x" + "f" * 64


class TestExtractChainId:
    def test_data_chain_id_wins(self):
        from clawmes.tools.clawnch_launch import _extract_chain_id

        assert _extract_chain_id({"data": {"chainId": 4663}}) == 4663
        assert _extract_chain_id({"data": {"chainId": 8453}}) == 8453

    def test_top_level_and_snake_case(self):
        from clawmes.tools.clawnch_launch import _extract_chain_id

        assert _extract_chain_id({"chainId": 4663}) == 4663
        assert _extract_chain_id({"chain_id": "4663"}) == 4663
        assert _extract_chain_id({"data": {"chain_id": 4663}}) == 4663

    def test_meta_chain_fallback(self):
        from clawmes.tools.clawnch_launch import _extract_chain_id

        assert _extract_chain_id({"meta": {"chain": "robinhood"}}) == 4663

    def test_base_default_only_when_absent(self):
        from clawmes.tools.clawnch_launch import _extract_chain_id

        # The Base custodial path echoes no chain id → Base.
        assert _extract_chain_id({}) == 8453
        assert _extract_chain_id({"txHash": "0x1"}) == 8453

    def test_garbage_chain_id_falls_through(self):
        from clawmes.tools.clawnch_launch import _extract_chain_id

        assert _extract_chain_id({"data": {"chainId": "not-a-number"}}) == 8453


class TestDeployChainGuard:
    def test_robinhood_chain_surfaces_unsupported(self, fake_svc):
        fake_svc.prepare_raises = ClawnchError(
            "unsupported_chain", "prepare_deploy is Base-only … use rh_ticket"
        )
        out = json.loads(
            clawnch_launch(
                {
                    "action": "deploy",
                    "name": "Foo",
                    "symbol": "FOO",
                    "chain": "robinhood",
                    "from_address": _WALLET,
                }
            )
        )
        assert out["isError"] is True
        assert out["details"]["error_code"] == "unsupported_chain"
        # The tool routed the request to the non-custodial prepare path —
        # never silently through the Base custodial deploy.
        assert fake_svc.prepare_calls[0]["chain"] == "robinhood"
        assert fake_svc.prepare_calls[0]["from_address"] == _WALLET
        assert fake_svc.deploys == []

    def test_base_chain_uses_custodial_deploy(self, fake_svc):
        out = json.loads(
            clawnch_launch({"action": "deploy", "name": "Foo", "symbol": "FOO", "chain": "base"})
        )
        assert out["details"]["txHash"] == "0xtx"
        assert fake_svc.prepare_calls == []


class TestRHTicketAction:
    def test_requires_name_symbol_wallet(self, fake_svc):
        out = json.loads(clawnch_launch({"action": "rh_ticket", "name": "Foo"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "param_error"
        out = json.loads(clawnch_launch({"action": "rh_ticket", "symbol": "FOO"}))
        assert out["isError"] is True
        out = json.loads(clawnch_launch({"action": "rh_ticket", "name": "Foo", "symbol": "FOO"}))
        assert out["isError"] is True
        assert "from_address" in out["content"][0]["text"]

    def test_success_path(self, fake_svc):
        out = json.loads(
            clawnch_launch(
                {
                    "action": "rh_ticket",
                    "name": "Foo",
                    "symbol": "FOO",
                    "from_address": _WALLET,
                    "description": "d",
                    "image": "https://x/i.png",
                    "fee_recipient": _ADDR,
                }
            )
        )
        assert out["details"]["data"]["chainId"] == 4663
        call = fake_svc.rh_ticket_calls[0]
        assert call["agent_wallet"] == _WALLET
        assert call["name"] == "Foo"
        assert call["symbol"] == "FOO"
        assert call["description"] == "d"
        assert call["image"] == "https://x/i.png"
        assert call["fee_recipient"] == _ADDR
        text = out["content"][0]["text"]
        assert "rh_confirm" in text
        assert "0xdeposit" in text  # deposit-path guidance

    def test_error_surfaces_code(self, fake_svc):
        fake_svc.rh_ticket_raises = ClawnchError("wallet_mismatch", "wrong wallet")
        out = json.loads(
            clawnch_launch(
                {"action": "rh_ticket", "name": "Foo", "symbol": "FOO", "from_address": _WALLET}
            )
        )
        assert out["isError"] is True
        assert out["details"]["error_code"] == "wallet_mismatch"

    def test_unexpected_error(self, fake_svc):
        fake_svc.rh_ticket_raises = RuntimeError("boom")
        out = json.loads(
            clawnch_launch(
                {"action": "rh_ticket", "name": "Foo", "symbol": "FOO", "from_address": _WALLET}
            )
        )
        assert out["details"]["error_code"] == "api_error"


class TestRHConfirmAction:
    def test_requires_tx_hash(self, fake_svc):
        out = json.loads(clawnch_launch({"action": "rh_confirm"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "param_error"

    def test_success_adds_links(self, fake_svc):
        out = json.loads(clawnch_launch({"action": "rh_confirm", "tx_hash": _TX}))
        assert fake_svc.rh_confirm_calls == [_TX]
        assert out["details"]["explorer_url"] == f"https://robinhoodchain.blockscout.com/tx/{_TX}"
        token = out["details"]["launch"]["token"]
        assert out["details"]["trade_url"] == f"https://bags.fm/token/{token}"
        assert out["details"]["token_explorer_url"].startswith(
            "https://robinhoodchain.blockscout.com/token/"
        )

    def test_error_surfaces_code(self, fake_svc):
        fake_svc.rh_confirm_raises = ClawnchError("tx_not_found", "no such tx")
        out = json.loads(clawnch_launch({"action": "rh_confirm", "tx_hash": _TX}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "tx_not_found"

    def test_unexpected_error(self, fake_svc):
        fake_svc.rh_confirm_raises = RuntimeError("boom")
        out = json.loads(clawnch_launch({"action": "rh_confirm", "tx_hash": _TX}))
        assert out["details"]["error_code"] == "api_error"


class TestRHDepositAction:
    def test_requires_all_fields(self, fake_svc):
        out = json.loads(clawnch_launch({"action": "rh_deposit"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "param_error"

    def test_success_path(self, fake_svc):
        out = json.loads(
            clawnch_launch(
                {
                    "action": "rh_deposit",
                    "deposit_tx_hash": _TX,
                    "from_address": _WALLET,
                    "name": "Foo",
                    "symbol": "FOO",
                    "description": "d",
                }
            )
        )
        call = fake_svc.rh_deposit_calls[0]
        assert call["deposit_tx_hash"] == _TX
        assert call["agent_wallet"] == _WALLET
        assert call["name"] == "Foo"
        assert call["symbol"] == "FOO"
        assert out["details"]["trade_url"] == f"https://bags.fm/token/{_ADDR}"
        assert "deposit" in out["content"][0]["text"]

    def test_error_surfaces_code(self, fake_svc):
        fake_svc.rh_deposit_raises = ClawnchError("duplicate_deposit", "used")
        out = json.loads(
            clawnch_launch(
                {
                    "action": "rh_deposit",
                    "deposit_tx_hash": _TX,
                    "from_address": _WALLET,
                    "name": "Foo",
                    "symbol": "FOO",
                }
            )
        )
        assert out["isError"] is True
        assert out["details"]["error_code"] == "duplicate_deposit"

    def test_unexpected_error(self, fake_svc):
        fake_svc.rh_deposit_raises = RuntimeError("boom")
        out = json.loads(
            clawnch_launch(
                {
                    "action": "rh_deposit",
                    "deposit_tx_hash": _TX,
                    "from_address": _WALLET,
                    "name": "Foo",
                    "symbol": "FOO",
                }
            )
        )
        assert out["details"]["error_code"] == "api_error"


class TestRHTokenAction:
    def test_returns_rhc_token(self, fake_svc):
        out = json.loads(clawnch_launch({"action": "rh_token"}))
        assert out["details"]["token_address"] == "0x6a50F139F3eD4C9c7bDa0D067c5Ed09De1EEBbeA"
        assert "bags.fm" in out["content"][0]["text"]


class TestSchema:
    def test_rhc_actions_advertised(self):
        from clawmes.tools.clawnch_launch import _SCHEMA

        enum = _SCHEMA["properties"]["action"]["enum"]
        assert {"rh_ticket", "rh_confirm", "rh_deposit", "rh_token"} <= set(enum)
        # from_address / tx_hash / deposit_tx_hash are declared (the RHC
        # flows are unreachable otherwise).
        for key in ("from_address", "tx_hash", "deposit_tx_hash", "chain"):
            assert key in _SCHEMA["properties"]
