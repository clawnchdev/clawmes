"""Tests for clawmes.services.coingecko.

The real HTTP call is mocked via monkeypatch on the module-local
``http_get`` reference. Tests verify the URL, params, headers, cache,
and error handling — never hits the network.
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from clawmes.services import coingecko as cg_module
from clawmes.services.coingecko import CoinGeckoService


@pytest.fixture(autouse=True)
def _isolate_singleton(monkeypatch):
    """Reset the module-level singleton between tests."""
    monkeypatch.setattr(cg_module, "_instance", None)


@pytest.fixture
def fake_http(monkeypatch):
    """Replace ``http_get`` with a recorder.

    Tests inject the desired response by setting ``fake_http.response``
    before triggering the service call.
    """

    class FakeHttp:
        def __init__(self):
            self.calls: list[dict[str, Any]] = []
            self.response: dict[str, Any] = {}

        def __call__(self, url, *, params=None, headers=None, timeout=30.0, **kw):
            self.calls.append(
                {"url": url, "params": params, "headers": headers, "timeout": timeout}
            )
            return self.response

    fake = FakeHttp()
    monkeypatch.setattr(cg_module, "http_get", fake)
    return fake


class TestStartStop:
    def test_start_picks_up_api_key(self, monkeypatch):
        monkeypatch.setenv("COINGECKO_API_KEY", "test-pro-key")
        svc = CoinGeckoService()
        svc.start()
        assert svc._api_key == "test-pro-key"

    def test_start_no_key(self, monkeypatch):
        monkeypatch.delenv("COINGECKO_API_KEY", raising=False)
        svc = CoinGeckoService()
        svc.start()
        assert svc._api_key is None

    def test_stop_clears_cache(self):
        svc = CoinGeckoService()
        svc.start()
        # Seed cache directly
        svc._cache[("ethereum", "usd")] = cg_module._CacheEntry(
            value=3500.0, expires_at=time.monotonic() + 60
        )
        svc.stop()
        assert svc._cache == {}


class TestGetPrices:
    def test_url_and_params(self, fake_http):
        fake_http.response = {"ethereum": {"usd": 3500}}
        svc = CoinGeckoService()
        svc.start()
        svc.get_prices(["ethereum"], "usd")

        assert len(fake_http.calls) == 1
        call = fake_http.calls[0]
        assert call["url"] == "https://api.coingecko.com/api/v3/simple/price"
        assert call["params"] == {"ids": "ethereum", "vs_currencies": "usd"}

    def test_pro_api_key_in_header(self, fake_http, monkeypatch):
        monkeypatch.setenv("COINGECKO_API_KEY", "key-123")
        fake_http.response = {"ethereum": {"usd": 3500}}
        svc = CoinGeckoService()
        svc.start()
        svc.get_prices(["ethereum"], "usd")

        assert fake_http.calls[0]["headers"] == {"x-cg-pro-api-key": "key-123"}

    def test_no_header_when_no_key(self, fake_http, monkeypatch):
        monkeypatch.delenv("COINGECKO_API_KEY", raising=False)
        fake_http.response = {"ethereum": {"usd": 3500}}
        svc = CoinGeckoService()
        svc.start()
        svc.get_prices(["ethereum"], "usd")
        assert fake_http.calls[0]["headers"] == {}

    def test_response_parsed(self, fake_http):
        fake_http.response = {
            "ethereum": {"usd": 3500.5},
            "bitcoin": {"usd": 65000},
        }
        svc = CoinGeckoService()
        svc.start()
        out = svc.get_prices(["ethereum", "bitcoin"], "usd")
        assert out == {"ethereum": 3500.5, "bitcoin": 65000.0}

    def test_token_lowercased_in_request(self, fake_http):
        fake_http.response = {"ethereum": {"usd": 3500}}
        svc = CoinGeckoService()
        svc.start()
        svc.get_prices(["Ethereum"], "USD")
        assert fake_http.calls[0]["params"]["ids"] == "ethereum"
        assert fake_http.calls[0]["params"]["vs_currencies"] == "usd"

    def test_missing_token_omitted(self, fake_http):
        fake_http.response = {"ethereum": {"usd": 3500}}  # bitcoin not in response
        svc = CoinGeckoService()
        svc.start()
        out = svc.get_prices(["ethereum", "bitcoin"], "usd")
        assert out == {"ethereum": 3500.0}

    def test_empty_input(self, fake_http):
        svc = CoinGeckoService()
        svc.start()
        assert svc.get_prices([], "usd") == {}
        assert fake_http.calls == []  # no HTTP call made

    def test_exception_returns_partial_hits(self, fake_http, monkeypatch):
        # Set up: one cached, one fresh fetch that errors.
        svc = CoinGeckoService()
        svc.start()
        svc._cache[("ethereum", "usd")] = cg_module._CacheEntry(
            value=3500.0, expires_at=time.monotonic() + 60
        )

        def boom(*a, **kw):
            raise RuntimeError("network down")

        monkeypatch.setattr(cg_module, "http_get", boom)
        out = svc.get_prices(["ethereum", "bitcoin"], "usd")
        assert out == {"ethereum": 3500.0}  # cache hit returned, fresh failed gracefully

    def test_non_dict_response_returns_empty(self, fake_http):
        fake_http.response = ["not", "a", "dict"]  # type: ignore[assignment]
        svc = CoinGeckoService()
        svc.start()
        out = svc.get_prices(["ethereum"], "usd")
        assert out == {}

    def test_per_token_non_dict_skipped(self, fake_http):
        """Cover line 133 — `continue` when one token's value isn't a dict.

        Real-world scenario: CoinGecko occasionally returns a string or
        null for a token that's been delisted mid-request.
        """
        fake_http.response = {
            "ethereum": {"usd": 3500},
            "delisted": "not-a-dict-anymore",
        }
        svc = CoinGeckoService()
        svc.start()
        out = svc.get_prices(["ethereum", "delisted"], "usd")
        # ethereum present, delisted skipped silently
        assert out == {"ethereum": 3500.0}


class TestCache:
    def test_repeat_call_does_not_hit_network(self, fake_http):
        fake_http.response = {"ethereum": {"usd": 3500}}
        svc = CoinGeckoService()
        svc.start()

        svc.get_prices(["ethereum"], "usd")
        svc.get_prices(["ethereum"], "usd")
        svc.get_prices(["ethereum"], "usd")

        assert len(fake_http.calls) == 1  # Only the first call hit network

    def test_cache_expires(self, fake_http, monkeypatch):
        fake_http.response = {"ethereum": {"usd": 3500}}
        svc = CoinGeckoService(ttl_seconds=1)
        svc.start()
        svc.get_prices(["ethereum"], "usd")

        # Fast-forward past TTL
        real_monotonic = time.monotonic
        offset = [0.0]
        monkeypatch.setattr(
            "clawmes.services.coingecko.time.monotonic",
            lambda: real_monotonic() + offset[0],
        )
        offset[0] = 5.0

        svc.get_prices(["ethereum"], "usd")
        assert len(fake_http.calls) == 2  # Refetched after expiry

    def test_partial_cache_hit_only_fetches_missing(self, fake_http):
        svc = CoinGeckoService()
        svc.start()
        svc._cache[("ethereum", "usd")] = cg_module._CacheEntry(
            value=3500.0, expires_at=time.monotonic() + 60
        )
        fake_http.response = {"bitcoin": {"usd": 65000}}

        out = svc.get_prices(["ethereum", "bitcoin"], "usd")
        assert out == {"ethereum": 3500.0, "bitcoin": 65000.0}

        # The fetch should only ask for bitcoin
        assert fake_http.calls[0]["params"]["ids"] == "bitcoin"

    def test_different_vs_currency_separate_cache(self, fake_http):
        fake_http.response = {"ethereum": {"usd": 3500}}
        svc = CoinGeckoService()
        svc.start()
        svc.get_prices(["ethereum"], "usd")

        fake_http.response = {"ethereum": {"eur": 3200}}
        svc.get_prices(["ethereum"], "eur")

        assert len(fake_http.calls) == 2  # USD and EUR cached separately


class TestSingleton:
    def test_get_coingecko_service_returns_singleton(self):
        a = cg_module.get_coingecko_service()
        b = cg_module.get_coingecko_service()
        assert a is b


class TestSinglePrice:
    def test_get_price_single(self, fake_http):
        fake_http.response = {"ethereum": {"usd": 3500}}
        svc = CoinGeckoService()
        svc.start()
        assert svc.get_price("ethereum", "usd") == 3500.0

    def test_get_price_missing(self, fake_http):
        fake_http.response = {}
        svc = CoinGeckoService()
        svc.start()
        assert svc.get_price("ethereum", "usd") is None
