"""Tests for clawmes.tools.defi_price."""

from __future__ import annotations

import json
from typing import Any

import pytest

from clawmes.services import coingecko as cg_module
from clawmes.services import price as price_module
from clawmes.tools.defi_price import defi_price


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
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


class TestQuote:
    def test_happy_path(self, fake_http):
        fake_http.response = {"ethereum": {"usd": 3500}}
        result = json.loads(defi_price({"action": "quote", "symbol": "ETH"}))

        assert "isError" not in result
        assert result["details"]["symbol"] == "ETH"
        assert result["details"]["price"] == 3500.0
        assert result["details"]["vs"] == "usd"
        assert "ETH" in result["content"][0]["text"]
        assert "3,500" in result["content"][0]["text"]

    def test_default_vs_currency_is_usd(self, fake_http):
        fake_http.response = {"ethereum": {"usd": 3500}}
        result = json.loads(defi_price({"action": "quote", "symbol": "ETH"}))
        assert result["details"]["vs"] == "usd"

    def test_explicit_vs_currency(self, fake_http):
        fake_http.response = {"ethereum": {"eur": 3200}}
        result = json.loads(defi_price({"action": "quote", "symbol": "ETH", "vs_currency": "EUR"}))
        assert result["details"]["vs"] == "eur"
        assert result["details"]["price"] == 3200.0

    def test_missing_symbol(self, fake_http):
        result = json.loads(defi_price({"action": "quote"}))
        assert result["isError"] is True
        assert result["details"]["error_code"] == "param_error"

    def test_unknown_symbol(self, fake_http):
        fake_http.response = {}  # CoinGecko returns nothing
        result = json.loads(defi_price({"action": "quote", "symbol": "TOTALLY_FAKE"}))
        assert result["isError"] is True
        assert result["details"]["error_code"] == "price_unavailable"


class TestMulti:
    def test_happy_path(self, fake_http):
        fake_http.response = {
            "ethereum": {"usd": 3500},
            "usd-coin": {"usd": 1.0},
        }
        result = json.loads(defi_price({"action": "multi", "tokens": ["ETH", "USDC"]}))

        assert "isError" not in result
        assert result["details"]["prices"] == {"ETH": 3500.0, "USDC": 1.0}
        assert result["details"]["missing"] == []

    def test_partial_missing_listed(self, fake_http):
        fake_http.response = {"ethereum": {"usd": 3500}}  # USDC missing
        result = json.loads(defi_price({"action": "multi", "tokens": ["ETH", "USDC"]}))

        assert result["details"]["prices"] == {"ETH": 3500.0}
        assert result["details"]["missing"] == ["USDC"]
        # The summary text mentions both
        text = result["content"][0]["text"]
        assert "ETH" in text
        assert "USDC" in text
        assert "unavailable" in text

    def test_empty_tokens(self, fake_http):
        result = json.loads(defi_price({"action": "multi", "tokens": []}))
        assert result["isError"] is True
        assert result["details"]["error_code"] == "param_error"

    def test_no_tokens_field(self, fake_http):
        result = json.loads(defi_price({"action": "multi"}))
        assert result["isError"] is True
        assert result["details"]["error_code"] == "param_error"

    def test_all_missing(self, fake_http):
        fake_http.response = {}
        result = json.loads(defi_price({"action": "multi", "tokens": ["fake1", "fake2"]}))
        assert result["isError"] is True
        assert result["details"]["error_code"] == "price_unavailable"

    def test_string_tokens_split_on_comma(self, fake_http):
        # read_list accepts CSV strings
        fake_http.response = {
            "ethereum": {"usd": 3500},
            "bitcoin": {"usd": 65000},
        }
        result = json.loads(defi_price({"action": "multi", "tokens": "ETH, BTC"}))
        assert result["details"]["prices"] == {"ETH": 3500.0, "BTC": 65000.0}


class TestActionValidation:
    def test_invalid_action(self, fake_http):
        result = json.loads(defi_price({"action": "delete-everything"}))
        assert result["isError"] is True
        # read_enum raises ParamError → caught by @read_tool gate → param_error
        assert result["details"]["error_code"] == "param_error"

    def test_missing_action(self, fake_http):
        result = json.loads(defi_price({}))
        assert result["isError"] is True
        assert result["details"]["error_code"] == "param_error"


class TestCacheBehavior:
    def test_repeat_quote_does_not_hit_network_twice(self, fake_http):
        fake_http.response = {"ethereum": {"usd": 3500}}
        defi_price({"action": "quote", "symbol": "ETH"})
        defi_price({"action": "quote", "symbol": "ETH"})
        assert len(fake_http.calls) == 1
