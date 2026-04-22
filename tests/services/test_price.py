"""Tests for clawmes.services.price (router + symbol resolution)."""

from __future__ import annotations

from typing import Any

import pytest

from clawmes.services import coingecko as cg_module
from clawmes.services import price as price_module
from clawmes.services.price import PriceService, resolve_symbol


@pytest.fixture(autouse=True)
def _isolate_singletons(monkeypatch):
    monkeypatch.setattr(cg_module, "_instance", None)
    monkeypatch.setattr(price_module, "_instance", None)


@pytest.fixture
def fake_http(monkeypatch):
    class FakeHttp:
        def __init__(self):
            self.calls: list[dict[str, Any]] = []
            self.response: dict[str, Any] = {}

        def __call__(self, url, *, params=None, headers=None, timeout=30.0, **kw):
            self.calls.append({"url": url, "params": params})
            return self.response

    fake = FakeHttp()
    monkeypatch.setattr(cg_module, "http_get", fake)
    return fake


class TestResolveSymbol:
    def test_known_ticker(self):
        assert resolve_symbol("ETH") == "ethereum"
        assert resolve_symbol("eth") == "ethereum"
        assert resolve_symbol("USDC") == "usd-coin"
        assert resolve_symbol("BTC") == "bitcoin"

    def test_unknown_passthrough(self):
        # Unknown symbol → lowercase passthrough (assumes it's already a CG ID)
        assert resolve_symbol("unknown-token") == "unknown-token"
        assert resolve_symbol("Some-Long-CG-ID") == "some-long-cg-id"

    def test_strips_whitespace(self):
        assert resolve_symbol("  ETH  ") == "ethereum"


class TestPriceServiceSingle:
    def test_known_ticker_resolved(self, fake_http):
        fake_http.response = {"ethereum": {"usd": 3500}}
        svc = PriceService()
        svc.start()

        result = svc.get_price("ETH", "usd")
        assert result == 3500.0
        # Resolved before HTTP call
        assert fake_http.calls[0]["params"]["ids"] == "ethereum"

    def test_passthrough_id(self, fake_http):
        fake_http.response = {"some-rare-token": {"usd": 0.5}}
        svc = PriceService()
        svc.start()

        result = svc.get_price("some-rare-token", "usd")
        assert result == 0.5

    def test_missing_returns_none(self, fake_http):
        fake_http.response = {}
        svc = PriceService()
        svc.start()
        assert svc.get_price("ETH", "usd") is None


class TestPriceServiceMulti:
    def test_response_keyed_by_caller_symbol(self, fake_http):
        # CoinGecko response is keyed by their IDs, not the user's tickers.
        # The router must rekey back so callers don't have to know about
        # the resolution.
        fake_http.response = {
            "ethereum": {"usd": 3500},
            "usd-coin": {"usd": 1.0},
        }
        svc = PriceService()
        svc.start()

        result = svc.get_prices(["ETH", "USDC"], "usd")
        assert result == {"ETH": 3500.0, "USDC": 1.0}

    def test_mixed_known_and_unknown(self, fake_http):
        fake_http.response = {
            "ethereum": {"usd": 3500},
            "rando-id": {"usd": 0.42},
        }
        svc = PriceService()
        svc.start()

        result = svc.get_prices(["ETH", "rando-id"], "usd")
        assert result == {"ETH": 3500.0, "rando-id": 0.42}

    def test_empty_input(self, fake_http):
        svc = PriceService()
        svc.start()
        assert svc.get_prices([], "usd") == {}
        assert fake_http.calls == []

    def test_missing_tokens_omitted(self, fake_http):
        fake_http.response = {"ethereum": {"usd": 3500}}  # USDC missing
        svc = PriceService()
        svc.start()

        result = svc.get_prices(["ETH", "USDC"], "usd")
        assert result == {"ETH": 3500.0}


class TestSingleton:
    def test_singleton(self):
        a = price_module.get_price_service()
        b = price_module.get_price_service()
        assert a is b
