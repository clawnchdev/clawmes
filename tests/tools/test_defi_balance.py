"""Tests for clawmes.tools.defi_balance."""

from __future__ import annotations

import json

import pytest

from clawmes.services import rpc as rpc_module
from clawmes.services import token_decimals as td_module
from clawmes.services.rpc import RpcError, RpcService
from clawmes.tools.defi_balance import defi_balance


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(rpc_module, "_instance", None)
    monkeypatch.setattr(td_module, "_instance", None)


@pytest.fixture
def fake_rpc(monkeypatch):
    """Replace the RPC service with a fake whose responses are scripted."""

    class FakeRpc(RpcService):
        def __init__(self):
            super().__init__()
            self._endpoints = {1: object(), 8453: object(), 42161: object(), 10: object()}
            self.balance_responses: dict[tuple[str, int], int] = {}
            self.eth_call_responses: dict[tuple[str, int], str] = {}
            self.failure_targets: set[str] = set()

        def has_endpoint(self, chain_id):
            return chain_id in self._endpoints

        def get_balance(self, address, chain_id):
            key = (address.lower(), chain_id)
            if address.lower() in self.failure_targets:
                raise RpcError(-32000, "simulated", method="eth_getBalance")
            return self.balance_responses.get(key, 0)

        def eth_call(self, *, to, data, chain_id, block="latest"):
            key = (to.lower(), chain_id)
            if to.lower() in self.failure_targets:
                raise RpcError(-32000, "simulated", method="eth_call")
            return self.eth_call_responses.get(key, "0x0")

    fake = FakeRpc()
    monkeypatch.setattr(rpc_module, "_instance", fake)
    return fake


HOLDER = "0x" + "a" * 40
USDC_BASE = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
WETH_BASE = "0x4200000000000000000000000000000000000006"


class TestNativeAction:
    def test_happy_path(self, fake_rpc):
        fake_rpc.balance_responses[(HOLDER.lower(), 8453)] = 5 * 10**17  # 0.5 ETH
        out = json.loads(defi_balance({"action": "native", "address": HOLDER, "chain": "base"}))
        assert "isError" not in out
        assert out["details"]["chain"] == "base"
        assert out["details"]["native_balance"].startswith("0.5")

    def test_default_chain_is_base(self, fake_rpc):
        fake_rpc.balance_responses[(HOLDER.lower(), 8453)] = 0
        out = json.loads(defi_balance({"action": "native", "address": HOLDER}))
        assert out["details"]["chain"] == "base"

    def test_chain_by_id(self, fake_rpc):
        fake_rpc.balance_responses[(HOLDER.lower(), 1)] = 10**18
        out = json.loads(defi_balance({"action": "native", "address": HOLDER, "chain": "1"}))
        assert out["details"]["chain_id"] == 1

    def test_rpc_failure(self, fake_rpc):
        fake_rpc.failure_targets.add(HOLDER.lower())
        out = json.loads(defi_balance({"action": "native", "address": HOLDER, "chain": "base"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "rpc_error"


class TestTokenAction:
    def test_happy_path(self, fake_rpc):
        # 1000 USDC = 1000 * 10**6 = 0x3b9aca00 = 0x3b9aca00 (in 32-byte padded hex)
        amount = 1000 * 10**6
        hex_amt = "0x" + format(amount, "064x")
        fake_rpc.eth_call_responses[(USDC_BASE.lower(), 8453)] = hex_amt
        out = json.loads(
            defi_balance(
                {"action": "token", "address": HOLDER, "chain": "base", "token": USDC_BASE}
            )
        )
        assert "isError" not in out
        assert out["details"]["balance"] == "1000"
        assert out["details"]["decimals"] == 6  # USDC seeded

    def test_invalid_token_address(self, fake_rpc):
        out = json.loads(
            defi_balance({"action": "token", "address": HOLDER, "token": "not-an-address"})
        )
        assert out["isError"] is True
        assert out["details"]["error_code"] == "invalid_token"

    def test_rpc_failure(self, fake_rpc):
        fake_rpc.failure_targets.add(USDC_BASE.lower())
        out = json.loads(
            defi_balance(
                {"action": "token", "address": HOLDER, "chain": "base", "token": USDC_BASE}
            )
        )
        assert out["isError"] is True
        assert out["details"]["error_code"] == "rpc_error"


class TestSummaryAction:
    def test_returns_native_plus_tokens(self, fake_rpc):
        fake_rpc.balance_responses[(HOLDER.lower(), 8453)] = 10**18  # 1 ETH
        # USDC: 100, WETH: 0 (zero balance — should be skipped)
        fake_rpc.eth_call_responses[(USDC_BASE.lower(), 8453)] = "0x" + format(100 * 10**6, "064x")
        fake_rpc.eth_call_responses[(WETH_BASE.lower(), 8453)] = "0x" + format(0, "064x")
        out = json.loads(defi_balance({"action": "summary", "address": HOLDER, "chain": "base"}))
        symbols = [b["symbol"] for b in out["details"]["balances"]]
        assert "ETH" in symbols
        assert "USDC" in symbols
        assert "WETH" not in symbols  # zero — skipped

    def test_no_balances(self, fake_rpc):
        fake_rpc.balance_responses[(HOLDER.lower(), 8453)] = 0
        # All tokens return 0
        out = json.loads(defi_balance({"action": "summary", "address": HOLDER, "chain": "base"}))
        text = out["content"][0]["text"]
        assert "no balances" in text.lower()

    def test_summary_attaches_portfolio_preview(self, fake_rpc):
        # Desktop UI: a portfolio card is written and surfaced under the
        # ``preview`` key so the desktop auto-opens it in the side rail.
        import os

        fake_rpc.balance_responses[(HOLDER.lower(), 8453)] = 10**18
        out = json.loads(defi_balance({"action": "summary", "address": HOLDER, "chain": "base"}))
        preview = out["details"]["preview"]
        assert preview.endswith(".html")
        assert "portfolio" in preview
        assert os.path.exists(preview)

    def test_summary_card_failure_is_swallowed(self, fake_rpc, monkeypatch):
        # If card rendering blows up, the balance read must still succeed and
        # simply omit the preview (UI is best-effort).
        import clawmes.lib.ui_cards as ui_cards

        def _boom(*_a, **_k):
            raise RuntimeError("disk full")

        monkeypatch.setattr(ui_cards, "write_card", _boom)
        fake_rpc.balance_responses[(HOLDER.lower(), 8453)] = 10**18
        out = json.loads(defi_balance({"action": "summary", "address": HOLDER, "chain": "base"}))
        assert "preview" not in out["details"]
        assert any(b["symbol"] == "ETH" for b in out["details"]["balances"])

    def test_native_rpc_error(self, fake_rpc):
        fake_rpc.failure_targets.add(HOLDER.lower())
        out = json.loads(defi_balance({"action": "summary", "address": HOLDER, "chain": "base"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "rpc_error"

    def test_token_rpc_failure_skipped(self, fake_rpc):
        # Native succeeds, one token fails — that token is silently skipped
        fake_rpc.balance_responses[(HOLDER.lower(), 8453)] = 10**18
        fake_rpc.failure_targets.add(USDC_BASE.lower())
        # WETH succeeds with non-zero
        fake_rpc.eth_call_responses[(WETH_BASE.lower(), 8453)] = "0x" + format(10**18, "064x")
        out = json.loads(defi_balance({"action": "summary", "address": HOLDER, "chain": "base"}))
        symbols = [b["symbol"] for b in out["details"]["balances"]]
        assert "ETH" in symbols
        assert "USDC" not in symbols  # skipped (rpc failure)
        assert "WETH" in symbols

    def test_chain_with_no_summary_tokens(self, fake_rpc):
        # zksync (chain id 324) isn't in _SUMMARY_TOKENS — but it's also
        # not in the rpc endpoint set, so we get rpc_unconfigured first.
        # Use chain 137 (Polygon) which is in endpoints but not in
        # _SUMMARY_TOKENS for this test. Wait — Polygon IS in default
        # endpoints. Add a balance for it and verify summary works.
        fake_rpc._endpoints[137] = object()
        fake_rpc.balance_responses[(HOLDER.lower(), 137)] = 10**18
        out = json.loads(defi_balance({"action": "summary", "address": HOLDER, "chain": "137"}))
        # Native MATIC present, no curated tokens (not in _SUMMARY_TOKENS for 137)
        symbols = [b["symbol"] for b in out["details"]["balances"]]
        assert symbols == ["MATIC"]


class TestErrors:
    def test_invalid_address(self, fake_rpc):
        out = json.loads(defi_balance({"action": "native", "address": "not-an-address"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "invalid_address"

    def test_unknown_chain(self, fake_rpc):
        out = json.loads(
            defi_balance({"action": "native", "address": HOLDER, "chain": "lunarchain"})
        )
        assert out["isError"] is True
        assert out["details"]["error_code"] == "invalid_chain"

    def test_rpc_unconfigured(self, fake_rpc):
        # Remove an endpoint then try to use it
        fake_rpc._endpoints.pop(8453)
        out = json.loads(defi_balance({"action": "native", "address": HOLDER, "chain": "base"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "rpc_unconfigured"


class TestRegister:
    def test_register_calls_ctx(self):
        from clawmes.tools import defi_balance as mod

        recorded = []

        class FakeCtx:
            def register_tool(self, **kw):
                recorded.append(kw)

        mod.register(FakeCtx())
        assert len(recorded) == 1
        assert recorded[0]["name"] == "defi_balance"
        assert recorded[0]["toolset"] == "clawmes-trading"
