"""Tests for the ``analytics`` tool."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from clawmes.tools.analytics import analytics


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    from clawmes.policy import storage as policy_storage
    from clawmes.services import coingecko as cg_mod

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(cg_mod, "_instance", None)
    policy_storage.save_policies([])


@pytest.fixture
def fake_cg(monkeypatch):
    from clawmes.services import coingecko as cg_mod

    svc = MagicMock()
    # 50 days of synthetic price data — gentle uptrend with some noise
    prices = [[i * 86_400_000, 1000 + i * 5 + (i % 7 - 3) * 10] for i in range(50)]
    volumes = [[i * 86_400_000, 5_000_000_000 + i * 1_000_000] for i in range(50)]
    svc.get_market_chart.return_value = {
        "prices": prices,
        "market_caps": [],
        "total_volumes": volumes,
    }
    monkeypatch.setattr(cg_mod, "_instance", svc)
    return svc


class TestRSI:
    def test_basic(self, fake_cg):
        out = json.loads(analytics({"action": "rsi", "token": "ETH"}))
        assert "isError" not in out
        details = out["details"]
        assert details["indicator"] == "rsi"
        assert details["period"] == 14
        assert 0 <= details["latest"] <= 100

    def test_custom_period(self, fake_cg):
        out = json.loads(analytics({"action": "rsi", "token": "ETH", "period": 7}))
        assert out["details"]["period"] == 7

    def test_insufficient_data(self, fake_cg):
        # Override prices to be too short
        fake_cg.get_market_chart.return_value = {
            "prices": [[0, 100], [1, 101]],
            "market_caps": [],
            "total_volumes": [],
        }
        out = json.loads(analytics({"action": "rsi", "token": "ETH"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "insufficient_data"

    def test_overbought_signal(self, fake_cg):
        # Construct a strict uptrend → RSI should be very high
        fake_cg.get_market_chart.return_value = {
            "prices": [[i, 100 * (1 + i / 1000)] for i in range(50)],
            "market_caps": [],
            "total_volumes": [],
        }
        out = json.loads(analytics({"action": "rsi", "token": "ETH"}))
        assert out["details"]["signal"] in ("overbought", "neutral")

    def test_oversold_signal(self, fake_cg):
        # Strict downtrend → RSI very low
        fake_cg.get_market_chart.return_value = {
            "prices": [[i, 1000 * (1 - i / 200)] for i in range(50)],
            "market_caps": [],
            "total_volumes": [],
        }
        out = json.loads(analytics({"action": "rsi", "token": "ETH"}))
        assert out["details"]["signal"] in ("oversold", "neutral")

    def test_empty_prices(self, fake_cg):
        fake_cg.get_market_chart.return_value = {
            "prices": [],
            "market_caps": [],
            "total_volumes": [],
        }
        out = json.loads(analytics({"action": "rsi", "token": "nonexistent"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "not_found"


class TestMACD:
    def test_basic(self, fake_cg):
        out = json.loads(analytics({"action": "macd", "token": "ETH"}))
        assert "isError" not in out
        details = out["details"]
        assert "macd" in details
        assert "signal" in details
        assert "histogram" in details
        assert details["trend"] in ("bullish", "bearish")

    def test_insufficient_data(self, fake_cg):
        fake_cg.get_market_chart.return_value = {
            "prices": [[i, 100 + i] for i in range(20)],
            "market_caps": [],
            "total_volumes": [],
        }
        out = json.loads(analytics({"action": "macd", "token": "ETH"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "insufficient_data"


class TestBollinger:
    def test_basic(self, fake_cg):
        out = json.loads(analytics({"action": "bollinger", "token": "ETH"}))
        assert "isError" not in out
        details = out["details"]
        assert details["upper"] > details["middle"]
        assert details["middle"] > details["lower"]
        assert details["band_width"] >= 0

    def test_custom_period(self, fake_cg):
        out = json.loads(analytics({"action": "bollinger", "token": "ETH", "period": 30}))
        assert out["details"]["period"] == 30

    def test_insufficient_data(self, fake_cg):
        fake_cg.get_market_chart.return_value = {
            "prices": [[i, 100] for i in range(10)],
            "market_caps": [],
            "total_volumes": [],
        }
        out = json.loads(analytics({"action": "bollinger", "token": "ETH"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "insufficient_data"


class TestVolume:
    def test_basic(self, fake_cg):
        out = json.loads(analytics({"action": "volume", "token": "ETH"}))
        assert "isError" not in out
        details = out["details"]
        assert details["latest_volume_usd"] > 0
        assert details["avg_volume_usd"] > 0
        assert details["classification"] in ("elevated", "depressed", "normal")

    def test_no_volume_data(self, fake_cg):
        fake_cg.get_market_chart.return_value = {
            "prices": [[1, 100]],
            "market_caps": [],
            "total_volumes": [],
        }
        out = json.loads(analytics({"action": "volume", "token": "ETH"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "not_found"

    def test_volume_api_error_becomes_market_error(self, fake_cg):
        """After refactor, CG failure affects all actions equally - the
        volume path inherits _fetch_prices error handling."""
        fake_cg.get_market_chart.side_effect = RuntimeError("boom")
        out = json.loads(analytics({"action": "volume", "token": "ETH"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "api_error"

    def test_volume_empty_after_filter(self, fake_cg):
        # Volume entries that fail the len >= 2 filter
        fake_cg.get_market_chart.return_value = {
            "prices": [[i, 100] for i in range(50)],
            "market_caps": [],
            "total_volumes": [[1]],  # malformed: only one element
        }
        out = json.loads(analytics({"action": "volume", "token": "ETH"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "insufficient_data"

    def test_elevated_classification(self, fake_cg):
        # Spike at the end
        volumes = [[i, 1_000_000_000] for i in range(49)] + [[49, 5_000_000_000]]
        fake_cg.get_market_chart.return_value = {
            "prices": [[i, 100 + i] for i in range(50)],
            "market_caps": [],
            "total_volumes": volumes,
        }
        out = json.loads(analytics({"action": "volume", "token": "ETH"}))
        assert out["details"]["classification"] == "elevated"

    def test_depressed_classification(self, fake_cg):
        volumes = [[i, 5_000_000_000] for i in range(49)] + [[49, 100_000_000]]
        fake_cg.get_market_chart.return_value = {
            "prices": [[i, 100 + i] for i in range(50)],
            "market_caps": [],
            "total_volumes": volumes,
        }
        out = json.loads(analytics({"action": "volume", "token": "ETH"}))
        assert out["details"]["classification"] == "depressed"


class TestFunding:
    def test_not_implemented(self):
        out = json.loads(analytics({"action": "funding", "token": "ETH"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "not_implemented"


class TestTokenAliases:
    def test_eth_alias(self, fake_cg):
        analytics({"action": "rsi", "token": "ETH"})
        # The CG service was called with the canonical slug
        kwargs = fake_cg.get_market_chart.call_args
        assert kwargs.args[0] == "ethereum"

    def test_btc_alias(self, fake_cg):
        analytics({"action": "rsi", "token": "BTC"})
        kwargs = fake_cg.get_market_chart.call_args
        assert kwargs.args[0] == "bitcoin"

    def test_unknown_passes_through(self, fake_cg):
        analytics({"action": "rsi", "token": "made-up-coin"})
        kwargs = fake_cg.get_market_chart.call_args
        assert kwargs.args[0] == "made-up-coin"


class TestApiError:
    def test_cg_failure(self, fake_cg):
        fake_cg.get_market_chart.side_effect = RuntimeError("network")
        out = json.loads(analytics({"action": "rsi", "token": "ETH"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "api_error"


class TestMathHelpers:
    def test_rsi_zero_avg_loss_returns_100(self):
        from clawmes.tools.analytics import _compute_rsi

        # Strict uptrend with zero losses → RSI ~= 100
        prices = [100 + i for i in range(20)]
        rsi = _compute_rsi(prices, 14)
        assert rsi[-1] == 100.0

    def test_rsi_short_input(self):
        from clawmes.tools.analytics import _compute_rsi

        # Less than period+1 deltas → empty result
        assert _compute_rsi([100, 101], 14) == []

    def test_ema_short_input(self):
        from clawmes.tools.analytics import _ema

        assert _ema([1, 2, 3], 14) == []

    def test_bollinger_single_point_window(self):
        # period=1: pstdev of single element is 0, so upper=middle=lower
        from clawmes.tools.analytics import _compute_bollinger

        m, u, low = _compute_bollinger([100, 101, 102], 1)
        assert m == [100, 101, 102]
        assert u == m == low


class TestRegister:
    def test_register_calls_ctx(self):
        from clawmes.tools import analytics as analytics_mod

        recorded = []

        class FakeCtx:
            def register_tool(self, **kw):
                recorded.append(kw)

        analytics_mod.register(FakeCtx())
        assert len(recorded) == 1
        assert recorded[0]["name"] == "analytics"
