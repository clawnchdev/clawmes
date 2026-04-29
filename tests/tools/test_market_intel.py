"""Tests for the ``market_intel`` tool."""

from __future__ import annotations

import json

import pytest

from clawmes.tools.market_intel import market_intel


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
    monkeypatch.setattr("clawmes.tools.market_intel.http_get", fake)
    return fake


class TestTrending:
    def test_basic(self, fake_http):
        fake_http.responses.append(
            {
                "coins": [
                    {
                        "item": {
                            "id": "bitcoin",
                            "symbol": "btc",
                            "name": "Bitcoin",
                            "market_cap_rank": 1,
                            "score": 0,
                        }
                    },
                    {
                        "item": {
                            "id": "ethereum",
                            "symbol": "eth",
                            "name": "Ethereum",
                            "market_cap_rank": 2,
                            "score": 1,
                        }
                    },
                ]
            }
        )
        out = json.loads(market_intel({"action": "trending"}))
        assert "isError" not in out
        details = out["details"]
        assert details["count"] == 2
        assert details["trending"][0]["symbol"] == "btc"

    def test_limit(self, fake_http):
        fake_http.responses.append(
            {
                "coins": [
                    {
                        "item": {
                            "id": f"c{i}",
                            "symbol": f"s{i}",
                            "name": f"n{i}",
                            "market_cap_rank": i,
                            "score": 0,
                        }
                    }
                    for i in range(20)
                ]
            }
        )
        out = json.loads(market_intel({"action": "trending", "limit": 5}))
        assert out["details"]["count"] == 5

    def test_api_error(self, fake_http):
        fake_http.responses.append(RuntimeError("network"))
        out = json.loads(market_intel({"action": "trending"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "api_error"

    def test_non_dict_response(self, fake_http):
        fake_http.responses.append("not a dict")
        out = json.loads(market_intel({"action": "trending"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "api_error"

    def test_no_coins_field(self, fake_http):
        fake_http.responses.append({})
        out = json.loads(market_intel({"action": "trending"}))
        assert "isError" not in out
        assert out["details"]["count"] == 0


class TestTopMovers:
    def test_gainers(self, fake_http):
        fake_http.responses.append(
            [
                {
                    "id": "btc",
                    "symbol": "btc",
                    "name": "Bitcoin",
                    "current_price": 100000,
                    "price_change_percentage_24h": 5.5,
                    "market_cap": 2_000_000_000_000,
                },
                {
                    "id": "eth",
                    "symbol": "eth",
                    "name": "Ethereum",
                    "current_price": 4000,
                    "price_change_percentage_24h": 3.2,
                    "market_cap": 500_000_000_000,
                },
            ]
        )
        out = json.loads(market_intel({"action": "top_movers"}))
        assert "isError" not in out
        details = out["details"]
        assert details["direction"] == "gainers"
        assert details["count"] == 2
        params = fake_http.calls[0]["params"]
        assert params["order"] == "price_change_percentage_24h_desc"

    def test_losers(self, fake_http):
        fake_http.responses.append([])
        market_intel({"action": "top_movers", "direction": "losers"})
        params = fake_http.calls[0]["params"]
        assert params["order"] == "price_change_percentage_24h_asc"

    def test_filters_non_dict_entries(self, fake_http):
        fake_http.responses.append(["not-a-dict", {"id": "btc", "symbol": "btc"}])
        out = json.loads(market_intel({"action": "top_movers"}))
        # Non-dict entries dropped
        assert out["details"]["count"] == 1

    def test_api_error(self, fake_http):
        fake_http.responses.append(RuntimeError("rate limit"))
        out = json.loads(market_intel({"action": "top_movers"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "api_error"

    def test_non_list_response(self, fake_http):
        fake_http.responses.append({"unexpected": "shape"})
        out = json.loads(market_intel({"action": "top_movers"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "api_error"


class TestNotImplemented:
    def test_whales(self):
        out = json.loads(market_intel({"action": "whales"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "not_implemented"

    def test_flows(self):
        out = json.loads(market_intel({"action": "flows"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "not_implemented"


class TestRegister:
    def test_register_calls_ctx(self):
        from clawmes.tools import market_intel as mi_mod

        recorded = []

        class FakeCtx:
            def register_tool(self, **kw):
                recorded.append(kw)

        mi_mod.register(FakeCtx())
        assert len(recorded) == 1
        assert recorded[0]["name"] == "market_intel"
