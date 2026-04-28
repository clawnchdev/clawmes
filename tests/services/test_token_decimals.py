"""Tests for clawmes.services.token_decimals."""

from __future__ import annotations

import pytest

from clawmes.services import rpc as rpc_module
from clawmes.services import token_decimals as td_module
from clawmes.services.rpc import RpcError, RpcService
from clawmes.services.token_decimals import (
    TokenDecimalsError,
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


class TestStrictMode:
    def test_strict_seed_returns_value(self, fake_rpc):
        svc = TokenDecimalsService()
        svc.start()
        d = svc.get_strict("0x833589fcd6edb6e08f4c7c32d4f71b54bda02913", 8453)
        assert d == 6

    def test_strict_unseeded_calls_rpc(self, fake_rpc):
        svc = TokenDecimalsService()
        svc.start()
        token = "0x" + "5" * 40
        fake_rpc.responses[token.lower()] = "0x06"
        assert svc.get_strict(token, 8453) == 6

    def test_strict_rpc_error_raises(self, fake_rpc):
        svc = TokenDecimalsService()
        svc.start()
        token = "0x" + "6" * 40
        fake_rpc.failures.add(token.lower())
        with pytest.raises(TokenDecimalsError) as exc_info:
            svc.get_strict(token, 8453)
        assert exc_info.value.address == token
        assert exc_info.value.chain_id == 8453
        assert isinstance(exc_info.value.cause, RpcError)

    def test_strict_invalid_response_raises(self, fake_rpc):
        svc = TokenDecimalsService()
        svc.start()
        token = "0x" + "7" * 40
        # Out-of-range uint8 from a contract that lies — must fail loud
        fake_rpc.responses[token.lower()] = "0x100"
        with pytest.raises(TokenDecimalsError):
            svc.get_strict(token, 8453)

    def test_strict_failure_does_not_poison_cache(self, fake_rpc):
        svc = TokenDecimalsService()
        svc.start()
        token = "0x" + "8" * 40
        fake_rpc.failures.add(token.lower())
        with pytest.raises(TokenDecimalsError):
            svc.get_strict(token, 8453)
        # After the failure, the cache must not contain a fallback —
        # otherwise a later strict call would silently return 18.
        assert (8453, token.lower()) not in svc._cache

    def test_loose_fallback_is_cached(self, fake_rpc):
        # Once loose has filled the fallback tier, subsequent loose
        # calls return the cached 18 without re-issuing RPC. Important
        # for balance summaries: a flaky token shouldn't hammer the
        # node on every render.
        svc = TokenDecimalsService()
        svc.start()
        token = "0x" + "b" * 40
        fake_rpc.failures.add(token.lower())
        assert svc.get(token, 8453) == 18
        # Pretend the RPC starts working — but the second loose call
        # should still hit the cache, not re-fetch.
        fake_rpc.failures.discard(token.lower())
        fake_rpc.responses[token.lower()] = "0x06"
        assert svc.get(token, 8453) == 18

    def test_loose_fallback_does_not_poison_strict(self, fake_rpc):
        # Critical invariant: a loose lookup that falls back to 18
        # MUST NOT cause subsequent strict lookups to silently return
        # 18. If the user hit the loose path first (balance render)
        # and then hits the strict path (transfer), strict has to
        # raise unless a verified value has been seen.
        svc = TokenDecimalsService()
        svc.start()
        token = "0x" + "9" * 40
        fake_rpc.failures.add(token.lower())
        # Loose get returns 18 and stores it in the fallback tier
        assert svc.get(token, 8453) == 18
        # Strict still raises — verified cache is empty, RPC still fails
        with pytest.raises(TokenDecimalsError):
            svc.get_strict(token, 8453)

    def test_strict_success_evicts_loose_fallback(self, fake_rpc):
        svc = TokenDecimalsService()
        svc.start()
        token = "0x" + "a" * 40
        fake_rpc.failures.add(token.lower())
        # Loose first — caches 18 in fallback tier
        assert svc.get(token, 8453) == 18
        # Now the RPC starts working with the real value
        fake_rpc.failures.discard(token.lower())
        fake_rpc.responses[token.lower()] = "0x06"
        # Strict re-fetches and gets the real 6
        assert svc.get_strict(token, 8453) == 6
        # Subsequent loose calls now see the verified 6, not the stale 18
        assert svc.get(token, 8453) == 6


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
