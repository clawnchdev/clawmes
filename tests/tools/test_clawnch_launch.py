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

    def deploy(self, *, token_params, bypass_tx_hash=None):
        self.deploys.append({"params": token_params, "bypass": bypass_tx_hash})
        if self.deploy_raise:
            raise self.deploy_raise
        return self.deploy_return

    def get_launch(self, token):
        self.infos.append(token)
        if self.info_raise:
            raise self.info_raise
        return self.info_return


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

    def test_clawnch_error_surfaces_code(self, fake_svc):
        fake_svc.deploy_raise = ClawnchError("rate_limited", "wait 24h")
        out = json.loads(clawnch_launch({"action": "deploy", "name": "Foo", "symbol": "FOO"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "rate_limited"

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
