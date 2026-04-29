"""Tests for the ``yield`` tool (module: yield_farming)."""

from __future__ import annotations

import json

import pytest

from clawmes.tools.yield_farming import yield_


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    from clawmes.policy import storage as policy_storage

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    policy_storage.save_policies([])


@pytest.fixture
def fake_http(monkeypatch):
    class FakeHttp:
        def __init__(self):
            self.calls: list[dict] = []
            self.responses: list = []

        def __call__(self, url, *, params=None, headers=None, timeout=30.0, **kw):
            self.calls.append({"url": url, "params": params})
            if not self.responses:
                raise AssertionError("no fake response queued")
            response = self.responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response

    fake = FakeHttp()
    monkeypatch.setattr("clawmes.tools.yield_farming.http_get", fake)
    return fake


_SAMPLE_POOLS = {
    "data": [
        {
            "pool": "pool-1",
            "project": "Aave",
            "chain": "Ethereum",
            "symbol": "USDC",
            "tvlUsd": 100_000_000,
            "apy": 4.5,
            "apyBase": 4.5,
            "apyReward": 0,
            "ilRisk": "no",
            "exposure": "single",
        },
        {
            "pool": "pool-2",
            "project": "Lido",
            "chain": "Ethereum",
            "symbol": "stETH",
            "tvlUsd": 30_000_000_000,
            "apy": 3.5,
            "ilRisk": "no",
        },
        {
            "pool": "pool-3",
            "project": "Convex",
            "chain": "Ethereum",
            "symbol": "USDC-DAI-USDT",
            "tvlUsd": 500_000,  # below default min_tvl
            "apy": 25,
        },
    ]
}


class TestFind:
    def test_basic(self, fake_http):
        fake_http.responses.append(_SAMPLE_POOLS)
        out = json.loads(yield_({"action": "find"}))
        assert "isError" not in out
        # Pool 3 filtered by min_tvl=1M default; pools 1 + 2 pass
        assert out["details"]["count"] == 2
        # Sorted by APY desc
        assert out["details"]["pools"][0]["apy"] == 4.5

    def test_chain_filter(self, fake_http):
        fake_http.responses.append(_SAMPLE_POOLS)
        out = json.loads(yield_({"action": "find", "chain": "ethereum"}))
        assert out["details"]["count"] == 2

    def test_chain_filter_no_match(self, fake_http):
        fake_http.responses.append(_SAMPLE_POOLS)
        out = json.loads(yield_({"action": "find", "chain": "Solana"}))
        assert out["details"]["count"] == 0

    def test_token_filter(self, fake_http):
        fake_http.responses.append(_SAMPLE_POOLS)
        out = json.loads(yield_({"action": "find", "token": "USDC"}))
        # pool-1 (USDC) matches
        assert out["details"]["count"] == 1

    def test_min_apy_filter(self, fake_http):
        fake_http.responses.append(_SAMPLE_POOLS)
        out = json.loads(yield_({"action": "find", "min_apy": 4.0}))
        # Only pool-1 (4.5%) passes
        assert out["details"]["count"] == 1

    def test_low_tvl_threshold(self, fake_http):
        fake_http.responses.append(_SAMPLE_POOLS)
        out = json.loads(yield_({"action": "find", "min_tvl": 100_000}))
        # All 3 now pass
        assert out["details"]["count"] == 3

    def test_api_error(self, fake_http):
        fake_http.responses.append(RuntimeError("network"))
        out = json.loads(yield_({"action": "find"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "api_error"

    def test_non_dict_response(self, fake_http):
        fake_http.responses.append("garbage")
        out = json.loads(yield_({"action": "find"}))
        assert out["isError"] is True

    def test_skips_non_dict_pools(self, fake_http):
        fake_http.responses.append({"data": ["not-a-dict", _SAMPLE_POOLS["data"][0]]})
        out = json.loads(yield_({"action": "find"}))
        assert out["details"]["count"] == 1

    def test_skips_malformed_apy(self, fake_http):
        bad = {"data": [{"chain": "Ethereum", "apy": "not-a-num", "tvlUsd": 1e9}]}
        fake_http.responses.append(bad)
        out = json.loads(yield_({"action": "find"}))
        assert out["details"]["count"] == 0


class TestInfo:
    def test_basic(self, fake_http):
        fake_http.responses.append({"data": [{"timestamp": 1, "tvlUsd": 1e6, "apy": 5.0}]})
        out = json.loads(yield_({"action": "info", "pool_id": "pool-1"}))
        assert "isError" not in out
        assert out["details"]["history_points"] == 1

    def test_no_history(self, fake_http):
        fake_http.responses.append({"data": []})
        out = json.loads(yield_({"action": "info", "pool_id": "missing"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "not_found"

    def test_api_error(self, fake_http):
        fake_http.responses.append(RuntimeError("rate limit"))
        out = json.loads(yield_({"action": "info", "pool_id": "x"}))
        assert out["isError"] is True

    def test_non_dict(self, fake_http):
        fake_http.responses.append("garbage")
        out = json.loads(yield_({"action": "info", "pool_id": "x"}))
        assert out["isError"] is True


class TestEnterExitNotImplemented:
    def test_enter(self):
        out = json.loads(yield_({"action": "enter"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "not_implemented"

    def test_exit(self):
        out = json.loads(yield_({"action": "exit"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "not_implemented"


class TestRegister:
    def test_register_calls_ctx(self):
        from clawmes.tools import yield_farming as y_mod

        recorded = []

        class FakeCtx:
            def register_tool(self, **kw):
                recorded.append(kw)

        y_mod.register(FakeCtx())
        assert len(recorded) == 1
        assert recorded[0]["name"] == "yield"
