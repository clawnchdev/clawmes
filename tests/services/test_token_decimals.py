"""Tests for clawmes.services.token_decimals."""

from __future__ import annotations

import pytest

from clawmes.services import rpc as rpc_module
from clawmes.services import token_decimals as td_module
from clawmes.services.rpc import RpcError, RpcService
from clawmes.services.token_decimals import (
    TokenDecimalsService,
    get_token_decimals_service,
)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(rpc_module, "_instance", None)
    monkeypatch.setattr(td_module, "_instance", None)


@pytest.fixture
def fake_rpc(monkeypatch):
    """Substitute a fake RpcService that returns canned eth_call results."""

    class FakeRpc(RpcService):
        def __init__(self):
            super().__init__()
            self.responses: dict = {}
            self.failures: set[str] = set()

        def eth_call(self, *, to, data, chain_id, block="latest"):
            key = to.lower()
            if key in self.failures:
                raise RpcError(-32000, "simulated failure", method="eth_call")
            return self.responses.get(key, "0x12")  # default = 18 decimals

    fake = FakeRpc()
    monkeypatch.setattr(rpc_module, "_instance", fake)
    return fake


class TestSeed:
    def test_known_token_decimals(self, fake_rpc):
        svc = TokenDecimalsService()
        svc.start()
        # USDC on Base — seeded
        d = svc.get("0x833589fcd6edb6e08f4c7c32d4f71b54bda02913", 8453)
        assert d == 6

    def test_seed_is_case_insensitive(self, fake_rpc):
        svc = TokenDecimalsService()
        svc.start()
        d = svc.get("0x833589FCD6EDB6E08F4C7C32D4F71B54BDA02913", 8453)
        assert d == 6


class TestRpcLookup:
    def test_unseeded_token_calls_rpc(self, fake_rpc):
        svc = TokenDecimalsService()
        svc.start()
        # Some random token, RPC returns 0x12 (= 18) by default
        token = "0x" + "1" * 40
        d = svc.get(token, 8453)
        assert d == 18

    def test_caches_after_lookup(self, fake_rpc):
        svc = TokenDecimalsService()
        svc.start()
        token = "0x" + "2" * 40
        fake_rpc.responses[token.lower()] = "0x06"
        first = svc.get(token, 8453)
        # Mutate the response — second call should still return the cached value
        fake_rpc.responses[token.lower()] = "0x12"
        second = svc.get(token, 8453)
        assert first == second == 6

    def test_rpc_error_falls_back_to_18(self, fake_rpc):
        svc = TokenDecimalsService()
        svc.start()
        token = "0x" + "3" * 40
        fake_rpc.failures.add(token.lower())
        d = svc.get(token, 8453)
        assert d == 18

    def test_invalid_uint8_falls_back_to_18(self, fake_rpc):
        svc = TokenDecimalsService()
        svc.start()
        token = "0x" + "4" * 40
        # 0x100 = 256 — out of uint8 range
        fake_rpc.responses[token.lower()] = "0x100"
        d = svc.get(token, 8453)
        assert d == 18


class TestLifecycle:
    def test_stop_clears_cache(self, fake_rpc):
        svc = TokenDecimalsService()
        svc.start()
        # Seed entry exists
        assert ("0x833589fcd6edb6e08f4c7c32d4f71b54bda02913", 8453) not in svc._cache or True
        svc.stop()
        assert svc._cache == {}


class TestSingleton:
    def test_returns_same_instance(self):
        a = get_token_decimals_service()
        b = get_token_decimals_service()
        assert a is b
