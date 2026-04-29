"""Tests for liquidity + browser (final 2 tools — 45/48)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from clawmes.wallet.state import WalletState

OWNER = "0x" + "a" * 40
TOKEN0 = "0x" + "b" * 40
TOKEN1 = "0x" + "c" * 40


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    from clawmes.policy import storage as policy_storage
    from clawmes.services import rpc as rpc_mod
    from clawmes.services import wallet as wallet_mod

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(wallet_mod, "_instance", None)
    monkeypatch.setattr(rpc_mod, "_instance", None)
    policy_storage.save_policies([])


@pytest.fixture
def connected(monkeypatch):
    state = WalletState.for_chain(mode="local", address=OWNER, chain_id=1)
    monkeypatch.setattr("clawmes.tools.liquidity.get_wallet_state", lambda: state)
    return state


@pytest.fixture
def fake_mode(monkeypatch):
    from clawmes.services import wallet as wallet_mod

    mode = MagicMock()
    mode.send_transaction.return_value = "0x" + "f" * 64
    svc = MagicMock()
    svc.active_mode = mode
    monkeypatch.setattr(wallet_mod, "get_wallet_service", lambda: svc)
    return mode


# --- liquidity ---


class TestLiquidity:
    def test_provide_with_structured_args(self, connected, fake_mode):
        from clawmes.tools.liquidity import liquidity

        out = json.loads(
            liquidity(
                {
                    "action": "provide",
                    "token0": "0x" + "1" * 40,
                    "token1": "0x" + "2" * 40,
                    "fee": 3000,
                    "tick_lower": -1000,
                    "tick_upper": 1000,
                    "amount0_desired": "1000000",
                    "amount1_desired": "1000000",
                }
            )
        )
        assert "isError" not in out
        kwargs = fake_mode.send_transaction.call_args.kwargs
        # Mint selector
        assert kwargs["data"].startswith("0x88316456")

    def test_provide_with_calldata_override(self, connected, fake_mode):
        from clawmes.tools.liquidity import liquidity

        out = json.loads(liquidity({"action": "provide", "calldata": "0xdeadbeef"}))
        assert "isError" not in out

    def test_provide_missing_fee(self, connected, fake_mode):
        from clawmes.tools.liquidity import liquidity

        out = json.loads(
            liquidity(
                {
                    "action": "provide",
                    "token0": "0x" + "1" * 40,
                    "token1": "0x" + "2" * 40,
                    "tick_lower": -1000,
                    "tick_upper": 1000,
                    "amount0_desired": "1",
                    "amount1_desired": "1",
                }
            )
        )
        assert out["isError"] is True
        assert out["details"]["error_code"] == "param_error"

    def test_provide_invalid_token(self, connected, fake_mode):
        from clawmes.tools.liquidity import liquidity

        out = json.loads(
            liquidity(
                {
                    "action": "provide",
                    "token0": "0xshort",
                    "token1": "0x" + "2" * 40,
                    "fee": 3000,
                    "tick_lower": -1000,
                    "tick_upper": 1000,
                    "amount0_desired": "1",
                    "amount1_desired": "1",
                }
            )
        )
        assert out["isError"] is True

    def test_provide_bad_amount(self, connected, fake_mode):
        from clawmes.tools.liquidity import liquidity

        out = json.loads(
            liquidity(
                {
                    "action": "provide",
                    "token0": "0x" + "1" * 40,
                    "token1": "0x" + "2" * 40,
                    "fee": 3000,
                    "tick_lower": -1000,
                    "tick_upper": 1000,
                    "amount0_desired": "garbage",
                    "amount1_desired": "1",
                }
            )
        )
        assert out["isError"] is True

    def test_provide_eth_abi_encoding_failure(self, connected, fake_mode, monkeypatch):
        # Stub eth_abi.encode to raise — covers the defensive
        # except-Exception branch around the encoder
        import eth_abi

        def boom(*args, **kwargs):
            raise RuntimeError("simulated encoder failure")

        monkeypatch.setattr(eth_abi, "encode", boom)
        from clawmes.tools.liquidity import liquidity

        out = json.loads(
            liquidity(
                {
                    "action": "provide",
                    "token0": "0x" + "1" * 40,
                    "token1": "0x" + "2" * 40,
                    "fee": 3000,
                    "tick_lower": -1000,
                    "tick_upper": 1000,
                    "amount0_desired": "1",
                    "amount1_desired": "1",
                }
            )
        )
        assert out["isError"] is True
        assert out["details"]["error_code"] == "param_error"

    def test_provide_with_explicit_minimums(self, connected, fake_mode):
        from clawmes.tools.liquidity import liquidity

        out = json.loads(
            liquidity(
                {
                    "action": "provide",
                    "token0": "0x" + "1" * 40,
                    "token1": "0x" + "2" * 40,
                    "fee": 500,
                    "tick_lower": -1000,
                    "tick_upper": 1000,
                    "amount0_desired": "1000",
                    "amount1_desired": "1000",
                    "amount0_min": "990",
                    "amount1_min": "990",
                }
            )
        )
        assert "isError" not in out

    def test_provide_no_wallet(self, monkeypatch, fake_mode):
        monkeypatch.setattr(
            "clawmes.tools.liquidity.get_wallet_state",
            lambda: WalletState.disconnected(),
        )
        from clawmes.tools.liquidity import liquidity

        out = json.loads(liquidity({"action": "provide", "calldata": "0xabc"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "wallet_not_connected"

    def test_withdraw(self, connected, fake_mode):
        from clawmes.tools.liquidity import liquidity

        out = json.loads(
            liquidity(
                {
                    "action": "withdraw",
                    "token_id": "12345",
                    "liquidity": "1000000",
                }
            )
        )
        assert "isError" not in out

    def test_withdraw_bad_int(self, connected, fake_mode):
        from clawmes.tools.liquidity import liquidity

        out = json.loads(
            liquidity(
                {
                    "action": "withdraw",
                    "token_id": "garbage",
                    "liquidity": "1",
                }
            )
        )
        assert out["isError"] is True

    def test_compound(self, connected, fake_mode):
        from clawmes.tools.liquidity import liquidity

        out = json.loads(liquidity({"action": "compound", "token_id": "42"}))
        assert "isError" not in out

    def test_compound_bad_int(self, connected, fake_mode):
        from clawmes.tools.liquidity import liquidity

        out = json.loads(liquidity({"action": "compound", "token_id": "junk"}))
        assert out["isError"] is True

    def test_info(self, connected, monkeypatch):
        from clawmes.services import rpc as rpc_mod

        svc = MagicMock()
        # 12 fields × 64 hex chars = 768 chars
        body = "0" * 64 * 2  # nonce + operator
        body += "0" * 24 + "1" * 40  # token0 (last 20 bytes of 32)
        body += "0" * 24 + "2" * 40  # token1
        body += format(3000, "064x")  # fee = 0.3%
        body += format(-1000 & ((1 << 256) - 1), "064x")  # tick lower (signed)
        body += format(1000, "064x")  # tick upper
        body += format(10**18, "064x")  # liquidity
        body += "0" * 64 * 5  # remaining 5 fields
        svc.eth_call.return_value = "0x" + body
        monkeypatch.setattr(rpc_mod, "_instance", svc)

        from clawmes.tools.liquidity import liquidity

        out = json.loads(liquidity({"action": "info", "token_id": "42"}))
        assert "isError" not in out
        assert out["details"]["fee_tier"] == 3000

    def test_info_bad_token_id(self):
        from clawmes.tools.liquidity import liquidity

        out = json.loads(liquidity({"action": "info", "token_id": "garbage"}))
        assert out["isError"] is True

    def test_info_short_response(self, monkeypatch):
        from clawmes.services import rpc as rpc_mod

        svc = MagicMock()
        svc.eth_call.return_value = "0x1234"
        monkeypatch.setattr(rpc_mod, "_instance", svc)

        from clawmes.tools.liquidity import liquidity

        out = json.loads(liquidity({"action": "info", "token_id": "1"}))
        assert out["isError"] is True

    def test_info_rpc_error(self, monkeypatch):
        from clawmes.services import rpc as rpc_mod
        from clawmes.services.rpc import RpcError

        svc = MagicMock()
        svc.eth_call.side_effect = RpcError(-32000, "no node", method="eth_call")
        monkeypatch.setattr(rpc_mod, "_instance", svc)
        from clawmes.tools.liquidity import liquidity

        out = json.loads(liquidity({"action": "info", "token_id": "1"}))
        assert out["isError"] is True

    def test_info_decode_failure(self, monkeypatch):
        from clawmes.services import rpc as rpc_mod

        svc = MagicMock()
        # 12 × 64 chars but with non-hex content in one field
        svc.eth_call.return_value = "0x" + ("z" * 64) * 12
        monkeypatch.setattr(rpc_mod, "_instance", svc)
        from clawmes.tools.liquidity import liquidity

        out = json.loads(liquidity({"action": "info", "token_id": "1"}))
        assert out["isError"] is True

    def test_pools_basic(self, monkeypatch):
        def fake_post(url, *, json=None, headers=None, timeout=30.0, **kw):
            return {"data": {"pools": [{"id": "0xpool", "feeTier": 3000, "liquidity": "1"}]}}

        monkeypatch.setattr("clawmes.tools.liquidity.http_post", fake_post)
        from clawmes.tools.liquidity import liquidity

        out = json.loads(liquidity({"action": "pools", "token0": TOKEN0, "token1": TOKEN1}))
        assert "isError" not in out
        assert out["details"]["count"] == 1

    def test_pools_unsupported_chain(self):
        from clawmes.tools.liquidity import liquidity

        out = json.loads(
            liquidity(
                {
                    "action": "pools",
                    "token0": TOKEN0,
                    "token1": TOKEN1,
                    "chain_id": 56,  # BSC not in subgraph map
                }
            )
        )
        assert out["isError"] is True
        assert out["details"]["error_code"] == "unsupported_chain"

    def test_pools_missing_tokens(self):
        from clawmes.tools.liquidity import liquidity

        out = json.loads(liquidity({"action": "pools", "token0": TOKEN0}))
        assert out["isError"] is True

    def test_pools_api_error(self, monkeypatch):
        def fake_post(*args, **kwargs):
            raise RuntimeError("network")

        monkeypatch.setattr("clawmes.tools.liquidity.http_post", fake_post)
        from clawmes.tools.liquidity import liquidity

        out = json.loads(liquidity({"action": "pools", "token0": TOKEN0, "token1": TOKEN1}))
        assert out["isError"] is True

    def test_pools_non_dict(self, monkeypatch):
        def fake_post(*args, **kwargs):
            return "garbage"

        monkeypatch.setattr("clawmes.tools.liquidity.http_post", fake_post)
        from clawmes.tools.liquidity import liquidity

        out = json.loads(liquidity({"action": "pools", "token0": TOKEN0, "token1": TOKEN1}))
        assert out["isError"] is True

    def test_no_active_mode(self, connected, monkeypatch):
        from clawmes.services import wallet as wallet_mod

        svc = MagicMock()
        svc.active_mode = None
        monkeypatch.setattr(wallet_mod, "get_wallet_service", lambda: svc)
        from clawmes.tools.liquidity import liquidity

        out = json.loads(liquidity({"action": "compound", "token_id": "1"}))
        assert out["isError"] is True

    def test_send_failure(self, connected, fake_mode):
        fake_mode.send_transaction.side_effect = RuntimeError("rejected")
        from clawmes.tools.liquidity import liquidity

        out = json.loads(liquidity({"action": "compound", "token_id": "1"}))
        assert out["isError"] is True


# --- browser ---


class FakePlaywrightContext:
    """Mock for the sync_playwright() context manager + nested objects."""

    def __init__(self, html="<html>test</html>", text="hello world", matches=None):
        self.html = html
        self.text = text
        self.matches = matches or ["match1", "match2"]
        self.screenshot_called = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    @property
    def chromium(self):
        return self

    def launch(self, **kwargs):
        return self

    def new_context(self):
        return self

    def new_page(self):
        return self

    def goto(self, url, **kwargs):
        pass

    def wait_for_timeout(self, ms):
        pass

    def content(self):
        return self.html

    def inner_text(self, selector):
        return self.text

    def locator(self, selector):
        return self

    def all_text_contents(self):
        return self.matches

    def screenshot(self, path):
        self.screenshot_called = True
        # Touch the file so it "exists" for the assertion
        from pathlib import Path

        Path(path).write_bytes(b"PNG_FAKE")

    def close(self):
        pass


class TestBrowser:
    def test_playwright_not_available(self, monkeypatch):
        monkeypatch.setattr("clawmes.tools.browser._resolve_playwright", lambda: None)
        from clawmes.tools.browser import browser

        out = json.loads(browser({"action": "open", "url": "https://example.com"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "not_available"

    def test_invalid_url(self):
        from clawmes.tools.browser import browser

        out = json.loads(browser({"action": "open", "url": "ftp://x"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "param_error"

    def test_open(self, monkeypatch):
        ctx = FakePlaywrightContext()
        monkeypatch.setattr("clawmes.tools.browser._resolve_playwright", lambda: lambda: ctx)
        from clawmes.tools.browser import browser

        out = json.loads(browser({"action": "open", "url": "https://example.com"}))
        assert "isError" not in out
        assert "<html>" in out["details"]["html"]

    def test_read(self, monkeypatch):
        ctx = FakePlaywrightContext()
        monkeypatch.setattr("clawmes.tools.browser._resolve_playwright", lambda: lambda: ctx)
        from clawmes.tools.browser import browser

        out = json.loads(browser({"action": "read", "url": "https://example.com"}))
        assert "isError" not in out
        assert out["details"]["text"] == "hello world"

    def test_extract(self, monkeypatch):
        ctx = FakePlaywrightContext()
        monkeypatch.setattr("clawmes.tools.browser._resolve_playwright", lambda: lambda: ctx)
        from clawmes.tools.browser import browser

        out = json.loads(
            browser(
                {
                    "action": "extract",
                    "url": "https://example.com",
                    "selector": "h1",
                }
            )
        )
        assert "isError" not in out
        assert out["details"]["count"] == 2

    def test_screenshot(self, monkeypatch):
        ctx = FakePlaywrightContext()
        monkeypatch.setattr("clawmes.tools.browser._resolve_playwright", lambda: lambda: ctx)
        from clawmes.tools.browser import browser

        out = json.loads(browser({"action": "screenshot", "url": "https://example.com"}))
        assert "isError" not in out
        assert "screenshot_path" in out["details"]
        assert ctx.screenshot_called

    def test_browser_raises(self, monkeypatch):
        ctx = FakePlaywrightContext()

        def bad_goto(*args, **kwargs):
            raise RuntimeError("page load timeout")

        ctx.goto = bad_goto
        monkeypatch.setattr("clawmes.tools.browser._resolve_playwright", lambda: lambda: ctx)
        from clawmes.tools.browser import browser

        out = json.loads(browser({"action": "open", "url": "https://example.com"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "browser_error"

    def test_zero_wait(self, monkeypatch):
        ctx = FakePlaywrightContext()
        monkeypatch.setattr("clawmes.tools.browser._resolve_playwright", lambda: lambda: ctx)
        from clawmes.tools.browser import browser

        out = json.loads(
            browser(
                {
                    "action": "open",
                    "url": "https://example.com",
                    "wait_seconds": 0,
                }
            )
        )
        assert "isError" not in out

    def test_resolve_playwright_import_error(self, monkeypatch):
        # Force playwright import to raise
        import builtins

        from clawmes.tools import browser as browser_mod

        original = builtins.__import__

        def fake_import(name, *a, **kw):
            if name.startswith("playwright"):
                raise ImportError("not installed")
            return original(name, *a, **kw)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        assert browser_mod._resolve_playwright() is None

    def test_resolve_playwright_when_installed(self, monkeypatch):
        # Inject a fake playwright.sync_api module so the import
        # succeeds and the resolver returns a callable.
        import sys
        import types

        from clawmes.tools import browser as browser_mod

        fake_sync_pw = MagicMock()
        fake_module = types.ModuleType("playwright.sync_api")
        fake_module.sync_playwright = fake_sync_pw  # type: ignore[attr-defined]
        fake_pkg = types.ModuleType("playwright")
        monkeypatch.setitem(sys.modules, "playwright", fake_pkg)
        monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_module)

        result = browser_mod._resolve_playwright()
        assert result is fake_sync_pw


# --- registers ---


class TestRegister:
    @pytest.mark.parametrize(
        "module_path,name",
        [
            ("clawmes.tools.liquidity", "liquidity"),
            ("clawmes.tools.browser", "browser"),
        ],
    )
    def test_register(self, module_path, name):
        import importlib

        mod = importlib.import_module(module_path)
        recorded = []

        class FakeCtx:
            def register_tool(self, **kw):
                recorded.append(kw)

        mod.register(FakeCtx())
        assert recorded[0]["name"] == name
