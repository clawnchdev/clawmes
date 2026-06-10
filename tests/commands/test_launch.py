"""Tests for the /launch slash command."""

from __future__ import annotations

import pytest

from clawmes.commands import launch as launch_mod
from clawmes.services import clawnch as cl_mod
from clawmes.services.clawnch import ClawnchError


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    # Reset per-sender state between tests
    monkeypatch.setattr(launch_mod, "_log_state", {})
    monkeypatch.setattr(cl_mod, "_instance", None)


class _FakeSvc:
    def __init__(self):
        self.deploys: list[dict] = []
        self.deploy_return: dict = {"txHash": "0xtx", "tokenAddress": "0xtok"}
        self.deploy_raise: Exception | None = None

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

    def get_bypass_recipient(self):
        return {"recipient": "0xbypass", "fee_eth": "0.005"}

    def get_burn_config(self):
        return {
            "token_address": "0x" + "c" * 40,
            "burn_address": "0x" + "d" * 40,
            "min_burn_tokens": 1_000_000,
        }


@pytest.fixture
def fake_svc(monkeypatch):
    s = _FakeSvc()
    monkeypatch.setattr("clawmes.services.clawnch.get_clawnch_service", lambda: s)
    return s


class TestUsageAndStatus:
    async def test_no_args_shows_usage(self):
        out = await launch_mod.handle_launch("")
        assert "Launch a token on Clawnch" in out
        assert "CLAWNCH_API_KEY" in out

    async def test_usage_shows_existing_draft(self):
        await launch_mod.handle_launch("name MyCoin", sender_id="alice")
        out = await launch_mod.handle_launch("", sender_id="alice")
        assert "Current draft" in out
        assert "MyCoin" in out

    async def test_status_empty(self):
        out = await launch_mod.handle_launch("status", sender_id="alice")
        assert "No launch draft" in out

    async def test_status_with_draft(self):
        await launch_mod.handle_launch("name MyCoin", sender_id="alice")
        out = await launch_mod.handle_launch("status", sender_id="alice")
        assert "MyCoin" in out

    async def test_unknown_arg(self):
        out = await launch_mod.handle_launch("explode")
        assert "Unknown /launch arg" in out


class TestDraftBuilding:
    async def test_name_set(self):
        out = await launch_mod.handle_launch("name MyCoin", sender_id="alice")
        assert "Name set" in out
        assert launch_mod._log_state["alice"]["name"] == "MyCoin"

    async def test_name_requires_value(self):
        out = await launch_mod.handle_launch("name", sender_id="alice")
        assert "Usage" in out

    async def test_symbol_uppercases(self):
        await launch_mod.handle_launch("symbol foo", sender_id="alice")
        assert launch_mod._log_state["alice"]["symbol"] == "FOO"

    async def test_symbol_requires_value(self):
        out = await launch_mod.handle_launch("symbol", sender_id="alice")
        assert "Usage" in out

    async def test_description_set(self):
        await launch_mod.handle_launch("description a cool coin", sender_id="alice")
        assert launch_mod._log_state["alice"]["description"] == "a cool coin"

    async def test_description_requires_value(self):
        out = await launch_mod.handle_launch("description", sender_id="alice")
        assert "Usage" in out

    async def test_bypass_set(self):
        await launch_mod.handle_launch("bypass 0xbeef", sender_id="alice")
        assert launch_mod._log_state["alice"]["bypass_tx_hash"] == "0xbeef"

    async def test_bypass_requires_value(self):
        out = await launch_mod.handle_launch("bypass", sender_id="alice")
        assert "Usage" in out

    async def test_cancel_clears(self):
        await launch_mod.handle_launch("name X", sender_id="alice")
        out = await launch_mod.handle_launch("cancel", sender_id="alice")
        assert "cleared" in out
        assert "alice" not in launch_mod._log_state


class TestImage:
    async def test_image_requires_value(self):
        out = await launch_mod.handle_launch("image", sender_id="alice")
        assert "Usage" in out

    async def test_image_full_url(self):
        out = await launch_mod.handle_launch("image https://i.imgur.com/x.png", sender_id="alice")
        assert "Image set: https://i.imgur.com/x.png" in out
        assert launch_mod._log_state["alice"]["image"] == "https://i.imgur.com/x.png"

    async def test_image_bare_hostname_gets_https(self):
        await launch_mod.handle_launch("image example.com/x.png", sender_id="alice")
        assert launch_mod._log_state["alice"]["image"] == "https://example.com/x.png"

    async def test_image_passthrough_for_weird_input(self):
        # Doesn't look like a URL — pass through unchanged (no autocomplete)
        await launch_mod.handle_launch("image not a url", sender_id="alice")
        assert launch_mod._log_state["alice"]["image"] == "not a url"


class TestSocialPlatforms:
    async def test_twitter_handle_normalized(self):
        out = await launch_mod.handle_launch("twitter clawnchbot", sender_id="alice")
        assert "Twitter set" in out
        assert "https://x.com/clawnchbot" in out
        socials = launch_mod._log_state["alice"]["socials"]
        assert socials["twitter"] == "https://x.com/clawnchbot"

    async def test_twitter_at_handle_normalized(self):
        await launch_mod.handle_launch("twitter @clawnchbot", sender_id="alice")
        assert launch_mod._log_state["alice"]["socials"]["twitter"] == "https://x.com/clawnchbot"

    async def test_twitter_full_url_passthrough(self):
        await launch_mod.handle_launch("twitter https://x.com/clawnchbot", sender_id="alice")
        assert launch_mod._log_state["alice"]["socials"]["twitter"] == "https://x.com/clawnchbot"

    async def test_x_alias_works(self):
        # /launch x is an alias for /launch twitter; stores under "twitter"
        await launch_mod.handle_launch("x clawnchbot", sender_id="alice")
        assert "twitter" in launch_mod._log_state["alice"]["socials"]

    async def test_telegram_handle_normalized(self):
        await launch_mod.handle_launch("telegram clawnchalerts", sender_id="alice")
        assert launch_mod._log_state["alice"]["socials"]["telegram"] == "https://t.me/clawnchalerts"

    async def test_farcaster_handle_normalized(self):
        await launch_mod.handle_launch("farcaster clawn", sender_id="alice")
        assert (
            launch_mod._log_state["alice"]["socials"]["farcaster"] == "https://warpcast.com/clawn"
        )

    async def test_discord_url_passthrough(self):
        await launch_mod.handle_launch("discord https://discord.gg/abc", sender_id="alice")
        assert launch_mod._log_state["alice"]["socials"]["discord"] == "https://discord.gg/abc"

    async def test_discord_bare_hostname_gets_https(self):
        await launch_mod.handle_launch("discord discord.gg/abc", sender_id="alice")
        assert launch_mod._log_state["alice"]["socials"]["discord"] == "https://discord.gg/abc"

    async def test_website_full_url(self):
        await launch_mod.handle_launch("website https://mycoin.xyz", sender_id="alice")
        assert launch_mod._log_state["alice"]["socials"]["website"] == "https://mycoin.xyz"

    async def test_website_bare_hostname(self):
        await launch_mod.handle_launch("website mycoin.xyz", sender_id="alice")
        assert launch_mod._log_state["alice"]["socials"]["website"] == "https://mycoin.xyz"

    async def test_handle_only_at_falls_back(self):
        # Edge case: just an @ with no handle. Falls back to raw value.
        await launch_mod.handle_launch("twitter @", sender_id="alice")
        assert launch_mod._log_state["alice"]["socials"]["twitter"] == "@"

    async def test_platform_requires_value(self):
        out = await launch_mod.handle_launch("twitter", sender_id="alice")
        assert "Usage" in out
        assert "/launch twitter" in out


class TestConfirmIncludesMetadata:
    async def test_confirm_passes_image(self, fake_svc):
        await launch_mod.handle_launch("name X", sender_id="alice")
        await launch_mod.handle_launch("symbol X", sender_id="alice")
        await launch_mod.handle_launch("image https://i.imgur.com/x.png", sender_id="alice")
        await launch_mod.handle_launch("confirm", sender_id="alice")
        params = fake_svc.deploys[0]["params"]
        assert params["image"] == "https://i.imgur.com/x.png"

    async def test_confirm_packs_socials(self, fake_svc):
        await launch_mod.handle_launch("name X", sender_id="alice")
        await launch_mod.handle_launch("symbol X", sender_id="alice")
        await launch_mod.handle_launch("twitter clawn", sender_id="alice")
        await launch_mod.handle_launch("website mycoin.xyz", sender_id="alice")
        await launch_mod.handle_launch("confirm", sender_id="alice")
        params = fake_svc.deploys[0]["params"]
        urls = params["metadata"]["socialMediaUrls"]
        platforms = {entry["platform"]: entry["url"] for entry in urls}
        assert platforms["twitter"] == "https://x.com/clawn"
        assert platforms["website"] == "https://mycoin.xyz"

    async def test_confirm_no_metadata_when_none_set(self, fake_svc):
        await launch_mod.handle_launch("name X", sender_id="alice")
        await launch_mod.handle_launch("symbol X", sender_id="alice")
        await launch_mod.handle_launch("confirm", sender_id="alice")
        params = fake_svc.deploys[0]["params"]
        assert "metadata" not in params
        assert "image" not in params


class TestStatusRendersSocials:
    async def test_status_groups_socials(self):
        await launch_mod.handle_launch("name X", sender_id="alice")
        await launch_mod.handle_launch("twitter clawn", sender_id="alice")
        out = await launch_mod.handle_launch("status", sender_id="alice")
        assert "socials:" in out
        assert "twitter" in out
        assert "https://x.com/clawn" in out


class TestPerSenderIsolation:
    async def test_two_senders_independent(self):
        await launch_mod.handle_launch("name AliceCoin", sender_id="alice")
        await launch_mod.handle_launch("name BobCoin", sender_id="bob")
        assert launch_mod._log_state["alice"]["name"] == "AliceCoin"
        assert launch_mod._log_state["bob"]["name"] == "BobCoin"

    async def test_default_sender(self):
        # No sender_id kwarg = "default"
        await launch_mod.handle_launch("name DefCoin")
        assert launch_mod._log_state["default"]["name"] == "DefCoin"


class TestConfirm:
    async def test_missing_name(self, fake_svc):
        out = await launch_mod.handle_launch("confirm", sender_id="alice")
        assert "needs at minimum a name" in out

    async def test_missing_symbol(self, fake_svc):
        await launch_mod.handle_launch("name X", sender_id="alice")
        out = await launch_mod.handle_launch("confirm", sender_id="alice")
        assert "needs at minimum" in out

    async def test_successful_confirm_clears_draft(self, fake_svc):
        await launch_mod.handle_launch("name MyCoin", sender_id="alice")
        await launch_mod.handle_launch("symbol MC", sender_id="alice")
        out = await launch_mod.handle_launch("confirm", sender_id="alice")
        assert "Launched" in out
        assert "0xtok" in out
        assert "0xtx" in out
        # Draft cleared
        assert "alice" not in launch_mod._log_state

    async def test_with_description_passed_to_service(self, fake_svc):
        await launch_mod.handle_launch("name MyCoin", sender_id="alice")
        await launch_mod.handle_launch("symbol MC", sender_id="alice")
        await launch_mod.handle_launch("description a thing", sender_id="alice")
        await launch_mod.handle_launch("confirm", sender_id="alice")
        params = fake_svc.deploys[0]["params"]
        assert params["description"] == "a thing"

    async def test_with_bypass_passed_to_service(self, fake_svc):
        await launch_mod.handle_launch("name MyCoin", sender_id="alice")
        await launch_mod.handle_launch("symbol MC", sender_id="alice")
        await launch_mod.handle_launch("bypass 0xb", sender_id="alice")
        await launch_mod.handle_launch("confirm", sender_id="alice")
        assert fake_svc.deploys[0]["bypass"] == "0xb"

    async def test_no_credentials_error_shows_hint(self, fake_svc):
        fake_svc.deploy_raise = ClawnchError("no_credentials", "no key")
        await launch_mod.handle_launch("name MyCoin", sender_id="alice")
        await launch_mod.handle_launch("symbol MC", sender_id="alice")
        out = await launch_mod.handle_launch("confirm", sender_id="alice")
        assert "no_credentials" in out
        assert "/register_agent" in out

    async def test_rate_limited_error_shows_bypass(self, fake_svc):
        fake_svc.deploy_raise = ClawnchError("rate_limited", "wait 24h")
        await launch_mod.handle_launch("name MyCoin", sender_id="alice")
        await launch_mod.handle_launch("symbol MC", sender_id="alice")
        out = await launch_mod.handle_launch("confirm", sender_id="alice")
        assert "0xbypass" in out
        assert "0.005 ETH" in out
        assert "/launch bypass" in out

    async def test_burn_required_error_renders_burn_instructions(self, fake_svc):
        fake_svc.deploy_raise = ClawnchError(
            "burn_required",
            "burn first",
            meta={
                "minBurnTokens": "1000000",
                "burnAddress": "0x000000000000000000000000000000000000dEaD",
            },
        )
        await launch_mod.handle_launch("name MyCoin", sender_id="alice")
        await launch_mod.handle_launch("symbol MC", sender_id="alice")
        out = await launch_mod.handle_launch("confirm", sender_id="alice")
        assert "1,000,000+" in out
        assert "0x000000000000000000000000000000000000dEaD" in out
        assert "/launch burn" in out
        assert "/launch confirm" in out

    async def test_other_clawnch_error(self, fake_svc):
        fake_svc.deploy_raise = ClawnchError("api_error", "boom")
        await launch_mod.handle_launch("name MyCoin", sender_id="alice")
        await launch_mod.handle_launch("symbol MC", sender_id="alice")
        out = await launch_mod.handle_launch("confirm", sender_id="alice")
        assert "api_error" in out
        # No /register_agent or /bypass hints for unrelated codes
        assert "/register_agent" not in out
        assert "0xbypass" not in out

    async def test_unexpected_error(self, fake_svc):
        fake_svc.deploy_raise = RuntimeError("boom")
        await launch_mod.handle_launch("name MyCoin", sender_id="alice")
        await launch_mod.handle_launch("symbol MC", sender_id="alice")
        out = await launch_mod.handle_launch("confirm", sender_id="alice")
        assert "Launch failed" in out

    async def test_response_with_only_tx_hash(self, fake_svc):
        fake_svc.deploy_return = {"txHash": "0xtx"}
        await launch_mod.handle_launch("name MyCoin", sender_id="alice")
        await launch_mod.handle_launch("symbol MC", sender_id="alice")
        out = await launch_mod.handle_launch("confirm", sender_id="alice")
        assert "0xtx" in out


class TestBurn:
    async def test_burn_no_args_shows_usage(self):
        out = await launch_mod.handle_launch("burn", sender_id="alice")
        assert "Usage" in out
        assert "1,000,000" in out

    async def test_burn_tx_hash_recorded(self):
        tx_hash = "0x" + "a" * 64
        out = await launch_mod.handle_launch(f"burn {tx_hash}", sender_id="alice")
        assert "Burn tx recorded" in out
        assert launch_mod._log_state["alice"]["burn_tx_hash"] == tx_hash

    async def test_burn_invalid_input(self):
        out = await launch_mod.handle_launch("burn notanumber", sender_id="alice")
        assert "Invalid burn input" in out

    async def test_burn_amount_too_low(self):
        out = await launch_mod.handle_launch("burn 500000", sender_id="alice")
        assert "too low" in out
        assert "1,000,000" in out

    async def test_burn_underscores_and_commas_parse(self):
        # Should parse "1_000_000" and "1,000,000" and "1000000" identically
        out = await launch_mod.handle_launch("burn 1_000_000", sender_id="alice")
        # Without a wallet mocked, the actual submit fails — but at least
        # the parse succeeded (no "Invalid burn input" / "too low").
        assert "Invalid" not in out
        assert "too low" not in out

    async def test_burn_amount_submits_via_wallet(self, monkeypatch, fake_svc):
        from clawmes.commands import launch as launch_mod_inner
        from clawmes.wallet.state import WalletState

        captured = {}

        class _FakeMode:
            def send_transaction(self, **kwargs):
                captured.update(kwargs)
                return "0xburntx"

        monkeypatch.setattr(
            launch_mod_inner,
            "_log_state",
            launch_mod._log_state,
        )

        # Patch wallet
        def _state():
            return WalletState.for_chain(mode="local", address="0x" + "1" * 40, chain_id=8453)

        def _get_svc():
            class _S:
                @property
                def active_mode(self_inner):  # noqa: ARG002, N805
                    return _FakeMode()

            return _S()

        import clawmes.services.wallet as ws_mod

        monkeypatch.setattr(ws_mod, "get_wallet_state", _state)
        monkeypatch.setattr(ws_mod, "get_wallet_service", _get_svc)

        out = await launch_mod.handle_launch("burn 1000000", sender_id="alice")
        assert "Burn submitted" in out
        assert "0xburntx" in out
        assert launch_mod._log_state["alice"]["burn_tx_hash"] == "0xburntx"
        assert launch_mod._log_state["alice"]["burn_amount"] == 1_000_000
        # send_transaction got the right shape
        assert captured["to"] == "0x" + "c" * 40
        assert captured["value"] == 0
        assert captured["chain_id"] == 8453

    async def test_burn_amount_no_wallet_connected(self, monkeypatch, fake_svc):
        from clawmes.wallet.state import WalletState

        def _state():
            return WalletState.disconnected()

        import clawmes.services.wallet as ws_mod

        monkeypatch.setattr(ws_mod, "get_wallet_state", _state)
        out = await launch_mod.handle_launch("burn 1000000", sender_id="alice")
        assert "No wallet connected" in out

    async def test_burn_amount_no_active_mode(self, monkeypatch, fake_svc):
        from clawmes.wallet.state import WalletState

        def _state():
            return WalletState.for_chain(mode="local", address="0x" + "1" * 40, chain_id=8453)

        class _Svc:
            @property
            def active_mode(self):
                return None

        import clawmes.services.wallet as ws_mod

        monkeypatch.setattr(ws_mod, "get_wallet_state", _state)
        monkeypatch.setattr(ws_mod, "get_wallet_service", lambda: _Svc())
        out = await launch_mod.handle_launch("burn 1000000", sender_id="alice")
        assert "No active wallet mode" in out

    async def test_burn_send_tx_fails(self, monkeypatch, fake_svc):
        from clawmes.wallet.state import WalletState

        def _state():
            return WalletState.for_chain(mode="local", address="0x" + "1" * 40, chain_id=8453)

        class _FakeMode:
            def send_transaction(self, **kwargs):  # noqa: ARG002
                raise RuntimeError("rpc down")

        class _Svc:
            @property
            def active_mode(self):
                return _FakeMode()

        import clawmes.services.wallet as ws_mod

        monkeypatch.setattr(ws_mod, "get_wallet_state", _state)
        monkeypatch.setattr(ws_mod, "get_wallet_service", lambda: _Svc())
        out = await launch_mod.handle_launch("burn 1000000", sender_id="alice")
        assert "Burn tx submission failed" in out
        assert "rpc down" in out


class TestBurnPassesThroughToDeploy:
    async def test_burn_tx_hash_forwarded_to_deploy(self, fake_svc):
        tx_hash = "0x" + "a" * 64
        await launch_mod.handle_launch("name MyCoin", sender_id="alice")
        await launch_mod.handle_launch("symbol MC", sender_id="alice")
        await launch_mod.handle_launch(f"burn {tx_hash}", sender_id="alice")
        await launch_mod.handle_launch("confirm", sender_id="alice")
        assert fake_svc.deploys[0]["burn"] == tx_hash


class TestLooksLikeTxHash:
    def test_valid(self):
        assert launch_mod._looks_like_tx_hash("0x" + "a" * 64)

    def test_wrong_prefix(self):
        assert not launch_mod._looks_like_tx_hash("a" * 64)

    def test_wrong_length(self):
        assert not launch_mod._looks_like_tx_hash("0x" + "a" * 63)

    def test_non_hex(self):
        assert not launch_mod._looks_like_tx_hash("0x" + "z" * 64)


class TestRegister:
    def test_registers_one_command(self):
        captured = []

        class FakeCtx:
            def register_command(self, **kw):
                captured.append(kw["name"])

        launch_mod.register(FakeCtx())
        assert captured == ["launch"]


# ── non-custodial / mode resolution ────────────────────────────────


class TestResolveConfirmMode:
    def test_default_with_wallet_is_noncustodial(self):
        assert launch_mod._resolve_confirm_mode("", wallet_connected=True) == "noncustodial"

    def test_default_without_wallet_is_custodial(self):
        assert launch_mod._resolve_confirm_mode("", wallet_connected=False) == "custodial"

    def test_custodial_flag_forces(self):
        assert launch_mod._resolve_confirm_mode("--custodial", wallet_connected=True) == "custodial"

    def test_noncustodial_flag_forces_even_without_wallet(self):
        assert (
            launch_mod._resolve_confirm_mode("--noncustodial", wallet_connected=False)
            == "noncustodial"
        )

    def test_custodial_wins_when_both_present(self):
        # First flag in the parse wins; --custodial appears first
        result = launch_mod._resolve_confirm_mode(
            "--custodial --noncustodial", wallet_connected=True
        )
        assert result == "custodial"


class TestNonCustodialConfirm:
    """Wallet-connected path through ``/launch confirm`` (default mode)."""

    @pytest.fixture
    def fake_wallet_state(self, monkeypatch):
        from clawmes.services import wallet as ws_mod
        from clawmes.wallet.state import WalletState

        state: dict = {"connected": True, "address": "0x" + "1" * 40}

        def _state():
            if state["connected"]:
                return WalletState.for_chain(mode="local", address=state["address"], chain_id=8453)
            return WalletState.disconnected()

        monkeypatch.setattr(ws_mod, "get_wallet_state", _state)
        return state

    @pytest.fixture
    def fake_prepare(self, monkeypatch):
        state: dict = {
            "return": {
                "ok": True,
                "data": {
                    "to": "0xE85A59c628F7d27878ACeB4bf3b35733630083a9",
                    "data": "0xdf40224a" + "00" * 32,
                    "value": "0x0",
                    "chainId": 8453,
                },
                "meta": {
                    "platformFeeBps": 2000,
                    "vaultPercentage": 0,
                    "source": "base-mcp",
                },
            },
            "raise": None,
            "calls": [],
        }

        class _Svc:
            def prepare_deploy(self_inner, **kwargs):  # noqa: ARG002, N805
                state["calls"].append(kwargs)
                if state["raise"]:
                    raise state["raise"]
                return state["return"]

            def get_bypass_recipient(self_inner):  # noqa: ARG002, N805
                return {"recipient": "0xbypass", "fee_eth": "0.005"}

        import clawmes.services.clawnch as cl_mod

        monkeypatch.setattr(cl_mod, "get_clawnch_service", lambda: _Svc())
        return state

    @pytest.fixture
    def fake_wallet_mode(self, monkeypatch):
        state: dict = {"tx_hash": "0xdeploytx", "raise": None, "calls": []}

        class _FakeMode:
            def send_transaction(self_inner, **kwargs):  # noqa: ARG002, N805
                state["calls"].append(kwargs)
                if state["raise"]:
                    raise state["raise"]
                return state["tx_hash"]

        class _Svc:
            @property
            def active_mode(self_inner):  # noqa: ARG002, N805
                return _FakeMode() if not state.get("none_mode") else None

        import clawmes.services.wallet as ws_mod

        monkeypatch.setattr(ws_mod, "get_wallet_service", lambda: _Svc())
        return state

    @pytest.fixture
    def fake_receipt(self, monkeypatch):
        # Receipt returns logs with the token address in topics[1] so we can
        # surface "Token: 0x..." in the success message.
        state: dict = {"raise": None, "receipt": None}

        class _Rpc:
            def wait_for_receipt(self_inner, tx_hash, chain_id, *, timeout=120.0):  # noqa: ARG002, N805
                if state["raise"]:
                    raise state["raise"]
                if state["receipt"] is not None:
                    return state["receipt"]
                return {
                    "logs": [
                        {
                            "topics": [
                                "0xeventsig",
                                "0x" + "0" * 24 + "deadbeef" * 5,
                            ]
                        }
                    ]
                }

        import clawmes.services.rpc as rpc_mod

        monkeypatch.setattr(rpc_mod, "get_rpc_service", lambda: _Rpc())
        return state

    async def test_no_wallet_returns_hint(self, fake_wallet_state, fake_svc):
        fake_wallet_state["connected"] = False
        await launch_mod.handle_launch("name X", sender_id="alice")
        await launch_mod.handle_launch("symbol X", sender_id="alice")
        out = await launch_mod.handle_launch("confirm --noncustodial", sender_id="alice")
        assert "needs a connected wallet" in out

    async def test_happy_path_returns_tx_hash(
        self, fake_wallet_state, fake_prepare, fake_wallet_mode, fake_receipt
    ):
        await launch_mod.handle_launch("name MyCoin", sender_id="alice")
        await launch_mod.handle_launch("symbol MC", sender_id="alice")
        out = await launch_mod.handle_launch("confirm", sender_id="alice")
        assert "Launched (non-custodial)" in out
        assert "0xdeploytx" in out
        assert "basescan.org/tx/0xdeploytx" in out
        # Token address surfaced from receipt
        assert "deadbeef" in out
        # Draft cleared
        assert "alice" not in launch_mod._log_state

    async def test_prepare_rate_limited(self, fake_wallet_state, fake_prepare, fake_wallet_mode):
        fake_prepare["raise"] = ClawnchError("rate_limited", "10/day cap")
        await launch_mod.handle_launch("name X", sender_id="alice")
        await launch_mod.handle_launch("symbol X", sender_id="alice")
        out = await launch_mod.handle_launch("confirm", sender_id="alice")
        assert "Prepare failed (rate_limited)" in out
        assert "00:00 UTC" in out

    async def test_prepare_bad_request(self, fake_wallet_state, fake_prepare, fake_wallet_mode):
        fake_prepare["raise"] = ClawnchError("bad_request", "bad name")
        await launch_mod.handle_launch("name X", sender_id="alice")
        await launch_mod.handle_launch("symbol X", sender_id="alice")
        out = await launch_mod.handle_launch("confirm", sender_id="alice")
        assert "Prepare failed (bad_request)" in out
        assert "/launch status" in out

    async def test_prepare_burn_required_renders_instructions(
        self, fake_wallet_state, fake_prepare, fake_wallet_mode
    ):
        fake_prepare["raise"] = ClawnchError(
            "burn_required",
            "This launch path now requires a verified 1,000,000 $CLAWNCH burn.",
            meta={
                "minBurnTokens": "1000000",
                "burnAddress": "0x000000000000000000000000000000000000dEaD",
            },
        )
        await launch_mod.handle_launch("name X", sender_id="alice")
        await launch_mod.handle_launch("symbol X", sender_id="alice")
        out = await launch_mod.handle_launch("confirm", sender_id="alice")
        assert "1,000,000+" in out
        assert "0x000000000000000000000000000000000000dEaD" in out
        assert "/launch burn" in out
        # Vault upside surfaced so the burn doesn't read as a pure cost
        assert "vault" in out.lower()

    async def test_prepare_burn_required_without_meta_uses_defaults(
        self, fake_wallet_state, fake_prepare, fake_wallet_mode
    ):
        fake_prepare["raise"] = ClawnchError("burn_required", "burn first")
        await launch_mod.handle_launch("name X", sender_id="alice")
        await launch_mod.handle_launch("symbol X", sender_id="alice")
        out = await launch_mod.handle_launch("confirm", sender_id="alice")
        assert "1,000,000+" in out
        assert "dEaD" in out

    async def test_prepare_other_clawnch_error(
        self, fake_wallet_state, fake_prepare, fake_wallet_mode
    ):
        fake_prepare["raise"] = ClawnchError("api_error", "upstream down")
        await launch_mod.handle_launch("name X", sender_id="alice")
        await launch_mod.handle_launch("symbol X", sender_id="alice")
        out = await launch_mod.handle_launch("confirm", sender_id="alice")
        assert "Prepare failed (api_error)" in out

    async def test_prepare_unexpected_error(
        self, fake_wallet_state, fake_prepare, fake_wallet_mode
    ):
        fake_prepare["raise"] = RuntimeError("network")
        await launch_mod.handle_launch("name X", sender_id="alice")
        await launch_mod.handle_launch("symbol X", sender_id="alice")
        out = await launch_mod.handle_launch("confirm", sender_id="alice")
        assert "Prepare failed" in out

    async def test_prepare_returns_bad_shape(
        self, fake_wallet_state, fake_prepare, fake_wallet_mode
    ):
        fake_prepare["return"] = {"ok": True}  # Missing data
        await launch_mod.handle_launch("name X", sender_id="alice")
        await launch_mod.handle_launch("symbol X", sender_id="alice")
        out = await launch_mod.handle_launch("confirm", sender_id="alice")
        assert "unexpected shape" in out

    async def test_no_active_wallet_mode(self, fake_wallet_state, fake_prepare, fake_wallet_mode):
        fake_wallet_mode["none_mode"] = True
        await launch_mod.handle_launch("name X", sender_id="alice")
        await launch_mod.handle_launch("symbol X", sender_id="alice")
        out = await launch_mod.handle_launch("confirm", sender_id="alice")
        assert "No active wallet mode" in out

    async def test_send_transaction_fails(self, fake_wallet_state, fake_prepare, fake_wallet_mode):
        fake_wallet_mode["raise"] = RuntimeError("user rejected")
        await launch_mod.handle_launch("name X", sender_id="alice")
        await launch_mod.handle_launch("symbol X", sender_id="alice")
        out = await launch_mod.handle_launch("confirm", sender_id="alice")
        assert "Deploy submission failed" in out

    async def test_receipt_failure_falls_back_gracefully(
        self, fake_wallet_state, fake_prepare, fake_wallet_mode, fake_receipt
    ):
        fake_receipt["raise"] = RuntimeError("rpc timeout")
        await launch_mod.handle_launch("name X", sender_id="alice")
        await launch_mod.handle_launch("symbol X", sender_id="alice")
        out = await launch_mod.handle_launch("confirm", sender_id="alice")
        # Still surfaces the tx hash, just no token address
        assert "0xdeploytx" in out
        assert "Token:" not in out

    async def test_receipt_no_logs_no_token(
        self, fake_wallet_state, fake_prepare, fake_wallet_mode, fake_receipt
    ):
        fake_receipt["receipt"] = {"logs": []}
        await launch_mod.handle_launch("name X", sender_id="alice")
        await launch_mod.handle_launch("symbol X", sender_id="alice")
        out = await launch_mod.handle_launch("confirm", sender_id="alice")
        assert "Token:" not in out

    async def test_receipt_short_topic_skipped(
        self, fake_wallet_state, fake_prepare, fake_wallet_mode, fake_receipt
    ):
        # topic too short to slice an address from
        fake_receipt["receipt"] = {"logs": [{"topics": ["0xshort", "0xabc"]}]}
        await launch_mod.handle_launch("name X", sender_id="alice")
        await launch_mod.handle_launch("symbol X", sender_id="alice")
        out = await launch_mod.handle_launch("confirm", sender_id="alice")
        assert "Token:" not in out

    async def test_vault_pct_surfaced(
        self, fake_wallet_state, fake_prepare, fake_wallet_mode, fake_receipt
    ):
        fake_prepare["return"]["meta"]["vaultPercentage"] = 5
        await launch_mod.handle_launch("name X", sender_id="alice")
        await launch_mod.handle_launch("symbol X", sender_id="alice")
        out = await launch_mod.handle_launch("confirm", sender_id="alice")
        assert "Vault: 5%" in out

    async def test_socials_pass_through_to_prepare(
        self, fake_wallet_state, fake_prepare, fake_wallet_mode, fake_receipt
    ):
        await launch_mod.handle_launch("name X", sender_id="alice")
        await launch_mod.handle_launch("symbol X", sender_id="alice")
        await launch_mod.handle_launch("twitter clawnch", sender_id="alice")
        await launch_mod.handle_launch("website clawn.ch", sender_id="alice")
        await launch_mod.handle_launch("confirm", sender_id="alice")
        call = fake_prepare["calls"][0]
        assert call["twitter"] == "https://x.com/clawnch"
        assert call["website"] == "https://clawn.ch"

    async def test_custodial_flag_routes_to_custodial(self, fake_wallet_state, fake_svc):
        # Even with wallet connected, --custodial uses the legacy path
        await launch_mod.handle_launch("name X", sender_id="alice")
        await launch_mod.handle_launch("symbol X", sender_id="alice")
        out = await launch_mod.handle_launch("confirm --custodial", sender_id="alice")
        assert "Launched (custodial)" in out
        assert len(fake_svc.deploys) == 1


class TestCustodialErrorHints:
    async def test_no_credentials_hint_mentions_dropping_custodial(self, fake_svc):
        fake_svc.deploy_raise = ClawnchError("no_credentials", "no key")
        await launch_mod.handle_launch("name X", sender_id="alice")
        await launch_mod.handle_launch("symbol X", sender_id="alice")
        out = await launch_mod.handle_launch("confirm --custodial", sender_id="alice")
        assert "/register_agent" in out
        assert "drop --custodial" in out

    async def test_rate_limited_hint_mentions_noncustodial_option(self, fake_svc):
        fake_svc.deploy_raise = ClawnchError("rate_limited", "wait 24h")
        await launch_mod.handle_launch("name X", sender_id="alice")
        await launch_mod.handle_launch("symbol X", sender_id="alice")
        out = await launch_mod.handle_launch("confirm --custodial", sender_id="alice")
        assert "non-custodial path" in out


# ── /launch export ─────────────────────────────────────────────────


class TestExport:
    @pytest.fixture
    def fake_wallet_optional(self, monkeypatch):
        from clawmes.services import wallet as ws_mod
        from clawmes.wallet.state import WalletState

        state: dict = {"connected": False, "address": "0x" + "1" * 40}

        def _state():
            if state["connected"]:
                return WalletState.for_chain(mode="local", address=state["address"], chain_id=8453)
            return WalletState.disconnected()

        monkeypatch.setattr(ws_mod, "get_wallet_state", _state)
        return state

    @pytest.fixture
    def fake_prepare_for_export(self, monkeypatch):
        state: dict = {"raise": None, "return": None}

        class _Svc:
            def prepare_deploy(self_inner, **kwargs):  # noqa: ARG002, N805
                if state["raise"]:
                    raise state["raise"]
                return state["return"] or {
                    "ok": True,
                    "data": {
                        "to": "0xE85A",
                        "data": "0xdf40",
                        "value": "0x0",
                        "chainId": 8453,
                    },
                    "meta": {
                        "platformFeeBps": 2000,
                        "userFeeBps": 8000,
                        "vaultPercentage": 0,
                    },
                }

        import clawmes.services.clawnch as cl_mod

        monkeypatch.setattr(cl_mod, "get_clawnch_service", lambda: _Svc())
        return state

    async def test_no_draft(self):
        out = await launch_mod.handle_launch("export", sender_id="alice")
        assert "Export needs at minimum a name and a symbol" in out

    async def test_no_wallet_warns(self, fake_wallet_optional, fake_prepare_for_export):
        fake_wallet_optional["connected"] = False
        await launch_mod.handle_launch("name X", sender_id="alice")
        await launch_mod.handle_launch("symbol X", sender_id="alice")
        out = await launch_mod.handle_launch("export", sender_id="alice")
        assert "No wallet connected" in out
        assert "Unsigned calldata" in out

    async def test_happy_path(self, fake_wallet_optional, fake_prepare_for_export):
        fake_wallet_optional["connected"] = True
        await launch_mod.handle_launch("name X", sender_id="alice")
        await launch_mod.handle_launch("symbol X", sender_id="alice")
        out = await launch_mod.handle_launch("export", sender_id="alice")
        assert "Unsigned calldata" in out
        assert "send_calls" in out
        assert "0xE85A" in out

    async def test_prepare_error(self, fake_wallet_optional, fake_prepare_for_export):
        fake_wallet_optional["connected"] = True
        fake_prepare_for_export["raise"] = ClawnchError("api_error", "down")
        await launch_mod.handle_launch("name X", sender_id="alice")
        await launch_mod.handle_launch("symbol X", sender_id="alice")
        out = await launch_mod.handle_launch("export", sender_id="alice")
        assert "Export failed" in out
        assert "api_error" in out

    async def test_burn_required_renders_instructions(
        self, fake_wallet_optional, fake_prepare_for_export
    ):
        fake_wallet_optional["connected"] = True
        fake_prepare_for_export["raise"] = ClawnchError(
            "burn_required",
            "burn first",
            meta={
                "minBurnTokens": "1000000",
                "burnAddress": "0x000000000000000000000000000000000000dEaD",
            },
        )
        await launch_mod.handle_launch("name X", sender_id="alice")
        await launch_mod.handle_launch("symbol X", sender_id="alice")
        out = await launch_mod.handle_launch("export", sender_id="alice")
        assert "Export failed" in out
        assert "1,000,000+" in out
        assert "/launch burn" in out

    async def test_prepare_unexpected_error(self, fake_wallet_optional, fake_prepare_for_export):
        fake_wallet_optional["connected"] = True
        fake_prepare_for_export["raise"] = RuntimeError("boom")
        await launch_mod.handle_launch("name X", sender_id="alice")
        await launch_mod.handle_launch("symbol X", sender_id="alice")
        out = await launch_mod.handle_launch("export", sender_id="alice")
        assert "Export failed" in out


# ── /launch alerts ─────────────────────────────────────────────────


class TestCheck:
    """`/launch check` — pre-flight validation."""

    @pytest.fixture
    def fake_wallet_optional(self, monkeypatch):
        from clawmes.services import wallet as ws_mod
        from clawmes.wallet.state import WalletState

        state: dict = {"connected": False, "address": "0x" + "1" * 40}

        def _state():
            if state["connected"]:
                return WalletState.for_chain(mode="local", address=state["address"], chain_id=8453)
            return WalletState.disconnected()

        monkeypatch.setattr(ws_mod, "get_wallet_state", _state)
        return state

    @pytest.fixture
    def fake_prepare_for_check(self, monkeypatch):
        state: dict = {"raise": None, "return": None}

        class _Svc:
            def prepare_deploy(self_inner, **kwargs):  # noqa: ARG002, N805
                if state["raise"]:
                    raise state["raise"]
                return state["return"] or {
                    "ok": True,
                    "data": {"to": "0xE85A", "data": "0xdf40", "value": "0x0", "chainId": 8453},
                    "meta": {
                        "platformFeeBps": 2000,
                        "userFeeBps": 8000,
                        "vaultPercentage": 0,
                    },
                }

        import clawmes.services.clawnch as cl_mod

        monkeypatch.setattr(cl_mod, "get_clawnch_service", lambda: _Svc())
        return state

    async def test_no_draft(self):
        out = await launch_mod.handle_launch("check", sender_id="alice")
        assert "Check needs at minimum" in out

    async def test_basic_check(self, fake_wallet_optional, fake_prepare_for_check):
        fake_wallet_optional["connected"] = True
        await launch_mod.handle_launch("name MyCoin", sender_id="alice")
        await launch_mod.handle_launch("symbol MC", sender_id="alice")
        out = await launch_mod.handle_launch("check", sender_id="alice")
        assert "Pre-flight OK" in out
        assert "MyCoin" in out
        assert "80% you / 20% Clawnch" in out
        assert "Vault:      0%" in out

    async def test_with_vault_percentage(self, fake_wallet_optional, fake_prepare_for_check):
        fake_wallet_optional["connected"] = True
        fake_prepare_for_check["return"] = {
            "ok": True,
            "data": {"to": "0xE85A", "data": "0xdf40", "value": "0x0", "chainId": 8453},
            "meta": {
                "platformFeeBps": 2000,
                "userFeeBps": 8000,
                "vaultPercentage": 5,
            },
        }
        await launch_mod.handle_launch("name X", sender_id="alice")
        await launch_mod.handle_launch("symbol X", sender_id="alice")
        out = await launch_mod.handle_launch("check", sender_id="alice")
        assert "Vault:      5%" in out

    async def test_burn_no_vault(self, fake_wallet_optional, fake_prepare_for_check):
        # burn tx recorded on the draft but the meta returns 0% (e.g. burn was too small)
        fake_wallet_optional["connected"] = True
        await launch_mod.handle_launch("name X", sender_id="alice")
        await launch_mod.handle_launch("symbol X", sender_id="alice")
        launch_mod._log_state["alice"]["burn_tx_hash"] = "0x" + "a" * 64
        out = await launch_mod.handle_launch("check", sender_id="alice")
        assert "produced no vault" in out

    async def test_no_wallet_warning(self, fake_wallet_optional, fake_prepare_for_check):
        fake_wallet_optional["connected"] = False
        await launch_mod.handle_launch("name X", sender_id="alice")
        await launch_mod.handle_launch("symbol X", sender_id="alice")
        out = await launch_mod.handle_launch("check", sender_id="alice")
        assert "No wallet connected" in out

    async def test_bad_request(self, fake_wallet_optional, fake_prepare_for_check):
        fake_wallet_optional["connected"] = True
        fake_prepare_for_check["raise"] = ClawnchError("bad_request", "bad name")
        await launch_mod.handle_launch("name X", sender_id="alice")
        await launch_mod.handle_launch("symbol X", sender_id="alice")
        out = await launch_mod.handle_launch("check", sender_id="alice")
        assert "Check failed (bad_request)" in out
        assert "re-run /launch check" in out

    async def test_rate_limited(self, fake_wallet_optional, fake_prepare_for_check):
        fake_wallet_optional["connected"] = True
        fake_prepare_for_check["raise"] = ClawnchError("rate_limited", "cap hit")
        await launch_mod.handle_launch("name X", sender_id="alice")
        await launch_mod.handle_launch("symbol X", sender_id="alice")
        out = await launch_mod.handle_launch("check", sender_id="alice")
        assert "rate_limited" in out
        assert "00:00 UTC" in out

    async def test_burn_required_renders_instructions(
        self, fake_wallet_optional, fake_prepare_for_check
    ):
        fake_wallet_optional["connected"] = True
        fake_prepare_for_check["raise"] = ClawnchError(
            "burn_required",
            "burn first",
            meta={
                "minBurnTokens": "1000000",
                "burnAddress": "0x000000000000000000000000000000000000dEaD",
            },
        )
        await launch_mod.handle_launch("name X", sender_id="alice")
        await launch_mod.handle_launch("symbol X", sender_id="alice")
        out = await launch_mod.handle_launch("check", sender_id="alice")
        assert "params OK" in out
        assert "no verified burn" in out
        assert "1,000,000+" in out
        assert "/launch burn" in out

    async def test_other_error(self, fake_wallet_optional, fake_prepare_for_check):
        fake_wallet_optional["connected"] = True
        fake_prepare_for_check["raise"] = ClawnchError("api_error", "down")
        await launch_mod.handle_launch("name X", sender_id="alice")
        await launch_mod.handle_launch("symbol X", sender_id="alice")
        out = await launch_mod.handle_launch("check", sender_id="alice")
        assert "params look OK" in out

    async def test_unexpected_error(self, fake_wallet_optional, fake_prepare_for_check):
        fake_wallet_optional["connected"] = True
        fake_prepare_for_check["raise"] = RuntimeError("boom")
        await launch_mod.handle_launch("name X", sender_id="alice")
        await launch_mod.handle_launch("symbol X", sender_id="alice")
        out = await launch_mod.handle_launch("check", sender_id="alice")
        assert "Check failed" in out


class TestAlerts:
    async def test_no_arg(self):
        out = await launch_mod.handle_launch("alerts", sender_id="alice")
        assert "https://t.me/ClawnchAlerts" in out
        assert "/launch alerts <source>" in out

    async def test_known_source(self):
        out = await launch_mod.handle_launch("alerts base-mcp", sender_id="alice")
        assert "base-mcp" in out
        assert "Base MCP" in out

    async def test_known_source_capitalized_for_clawmes(self):
        out = await launch_mod.handle_launch("alerts clawmes", sender_id="alice")
        assert "Clawmes" in out

    async def test_unknown_source(self):
        out = await launch_mod.handle_launch("alerts garbage", sender_id="alice")
        assert "Unknown source" in out


class TestBuildTokenParamsHelper:
    """The extracted ``_build_token_params`` keeps custodial/non-custodial
    socials in sync. Direct unit tests guard against drift."""

    def test_minimal(self):
        params = launch_mod._build_token_params({}, name="X", symbol="X")
        assert params == {"name": "X", "symbol": "X"}

    def test_full(self):
        draft = {
            "description": "d",
            "image": "https://i",
            "socials": {"twitter": "https://x.com/x", "website": "https://x"},
        }
        params = launch_mod._build_token_params(draft, name="N", symbol="S")
        assert params["description"] == "d"
        assert params["image"] == "https://i"
        assert params["metadata"]["socialMediaUrls"][0]["platform"] == "twitter"


class TestCommandHistoryBestEffort:
    async def test_recording_failure_does_not_break_command(self, monkeypatch):
        from clawmes.services import command_history as ch_mod

        def _boom(*a, **kw):
            raise RuntimeError("history broken")

        monkeypatch.setattr(ch_mod, "record_command_call", _boom)
        out = await launch_mod.handle_launch("")
        assert isinstance(out, str)
