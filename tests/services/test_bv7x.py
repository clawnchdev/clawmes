"""Tests for clawmes.services.bv7x."""

from __future__ import annotations

import pytest

from clawmes.services import bv7x as bv7x_mod
from clawmes.services.bv7x import (
    BV7XError,
    BV7XService,
    get_bv7x_service,
)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(bv7x_mod, "_instance", None)


@pytest.fixture
def fake_http(monkeypatch):
    class FakeHttp:
        def __init__(self):
            self.calls: list[dict] = []
            self.responses: list = []

        def __call__(self, url, *, params=None, headers=None, timeout=30.0, **kw):
            self.calls.append(
                {"url": url, "params": params, "headers": headers, "timeout": timeout}
            )
            if not self.responses:
                raise AssertionError("no fake response queued")
            response = self.responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response

    fake = FakeHttp()
    monkeypatch.setattr(bv7x_mod, "http_get", fake)
    return fake


class TestLifecycle:
    def test_start_is_noop(self):
        BV7XService().start()

    def test_stop_clears_cache(self, fake_http):
        svc = BV7XService()
        fake_http.responses.append({"regime": "BULL"})
        svc.get_regime()
        svc.stop()
        # After stop, a fresh call should re-fetch (cache empty).
        fake_http.responses.append({"regime": "BEAR"})
        svc.get_regime()
        assert len(fake_http.calls) == 2


class TestGetRegime:
    def test_basic(self, fake_http):
        fake_http.responses.append({"regime": "BEAR_TREND", "risk_level": "High"})
        out = BV7XService().get_regime()
        assert out["regime"] == "BEAR_TREND"
        assert fake_http.calls[0]["url"].endswith("/api/bv7x/regime")


class TestGetAgentIdentity:
    def test_basic(self, fake_http):
        fake_http.responses.append({"agent_id": 28841, "reputation": 0.87})
        out = BV7XService().get_agent_identity()
        assert out["agent_id"] == 28841
        assert fake_http.calls[0]["url"].endswith("/api/bv7x/agent/identity")


class TestDiscoverA2A:
    def test_basic(self, fake_http):
        fake_http.responses.append({"skills": ["a", "b"], "version": "0.3.0"})
        out = BV7XService().discover_a2a()
        assert out["skills"] == ["a", "b"]


class TestCaching:
    def test_caches_within_ttl(self, fake_http):
        svc = BV7XService(ttl_seconds=60)
        fake_http.responses.append({"regime": "BULL"})
        svc.get_regime()
        svc.get_regime()  # Should hit cache
        assert len(fake_http.calls) == 1

    def test_refetches_after_ttl(self, fake_http, monkeypatch):
        import time as time_module

        svc = BV7XService(ttl_seconds=1)
        # Patch monotonic to control the clock.
        clock = [0.0]

        def fake_monotonic():
            return clock[0]

        monkeypatch.setattr(time_module, "monotonic", fake_monotonic)
        monkeypatch.setattr("clawmes.services.bv7x.time.monotonic", fake_monotonic)

        fake_http.responses.append({"regime": "BULL"})
        svc.get_regime()
        clock[0] = 2.0  # Past the 1-second TTL.
        fake_http.responses.append({"regime": "BEAR"})
        result = svc.get_regime()
        assert result["regime"] == "BEAR"
        assert len(fake_http.calls) == 2

    def test_clear_cache(self, fake_http):
        svc = BV7XService()
        fake_http.responses.append({"regime": "BULL"})
        svc.get_regime()
        svc.clear_cache()
        fake_http.responses.append({"regime": "BEAR"})
        svc.get_regime()
        assert len(fake_http.calls) == 2


class TestErrorClassification:
    def test_rate_limit(self, fake_http):
        fake_http.responses.append(RuntimeError("HTTP 429 Too Many"))
        with pytest.raises(BV7XError) as exc:
            BV7XService().get_regime()
        assert exc.value.code == "rate_limited"

    def test_not_found(self, fake_http):
        fake_http.responses.append(RuntimeError("HTTP 404 Not Found"))
        with pytest.raises(BV7XError) as exc:
            BV7XService().get_regime()
        assert exc.value.code == "not_found"

    def test_token_gated_402(self, fake_http):
        fake_http.responses.append(RuntimeError("HTTP 402 Payment Required"))
        with pytest.raises(BV7XError) as exc:
            BV7XService().get_regime()
        assert exc.value.code == "token_gated"

    def test_token_gated_keyword(self, fake_http):
        fake_http.responses.append(RuntimeError("HTTP 402 token gate active"))
        with pytest.raises(BV7XError) as exc:
            BV7XService().get_regime()
        assert exc.value.code == "token_gated"

    def test_generic_error(self, fake_http):
        fake_http.responses.append(RuntimeError("connection reset"))
        with pytest.raises(BV7XError) as exc:
            BV7XService().get_regime()
        assert exc.value.code == "api_error"

    def test_non_dict_response(self, fake_http):
        fake_http.responses.append("not a dict")
        with pytest.raises(BV7XError) as exc:
            BV7XService().get_regime()
        assert exc.value.code == "api_error"
        assert "non-dict" in exc.value.message


class TestSingleton:
    def test_singleton(self):
        a = get_bv7x_service()
        b = get_bv7x_service()
        assert a is b


class TestPublicEndpoints:
    """Coverage for every free / public endpoint method."""

    def test_btc_price(self, fake_http):
        fake_http.responses.append({"price": 76754, "change_24h": -2.1})
        out = BV7XService().get_btc_price()
        assert out["price"] == 76754
        assert fake_http.calls[0]["url"].endswith("/api/btc-price")

    def test_fear_greed(self, fake_http):
        fake_http.responses.append({"value": 25, "classification": "Fear"})
        out = BV7XService().get_fear_greed()
        assert out["value"] == 25

    def test_etf_flows(self, fake_http):
        fake_http.responses.append({"flow_7d": "-1.68B", "flow_30d": "2.5B"})
        out = BV7XService().get_etf_flows()
        assert out["flow_7d"] == "-1.68B"

    def test_signal_metadata_default_horizon(self, fake_http):
        fake_http.responses.append({"signal": "GATED", "market_context": {}})
        BV7XService().get_signal_metadata()
        assert "horizon=7d" in fake_http.calls[0]["url"]

    def test_signal_metadata_custom_horizon(self, fake_http):
        fake_http.responses.append({"signal": "GATED", "market_context": {}})
        BV7XService().get_signal_metadata(horizon="3d")
        assert "horizon=3d" in fake_http.calls[0]["url"]

    def test_scorecard_default(self, fake_http):
        fake_http.responses.append({"success": True, "summary": {}})
        BV7XService().get_scorecard()
        assert "horizon=7" in fake_http.calls[0]["url"]

    def test_scorecard_custom_horizon(self, fake_http):
        fake_http.responses.append({"success": True, "summary": {}})
        BV7XService().get_scorecard(horizon=2)
        assert "horizon=2" in fake_http.calls[0]["url"]

    def test_onchain_latest(self, fake_http):
        fake_http.responses.append({"uid": "0xaa", "direction": "UP"})
        out = BV7XService().get_onchain_latest()
        assert out["direction"] == "UP"

    def test_onchain_history_with_limit(self, fake_http):
        fake_http.responses.append({"attestations": []})
        BV7XService().get_onchain_history(limit=5)
        assert "limit=5" in fake_http.calls[0]["url"]

    def test_onchain_stats(self, fake_http):
        fake_http.responses.append({"total": 83, "accuracy": 61.4})
        out = BV7XService().get_onchain_stats()
        assert out["total"] == 83

    def test_verify_onchain_attestation(self, fake_http):
        fake_http.responses.append({"valid": True, "uid": "0xaa"})
        out = BV7XService().verify_onchain_attestation("0xaa")
        assert out["valid"] is True
        assert "/api/bv7x/onchain-oracle/verify/0xaa" in fake_http.calls[0]["url"]

    def test_agent_reputation(self, fake_http):
        fake_http.responses.append({"score": 0.87})
        out = BV7XService().get_agent_reputation()
        assert out["score"] == 0.87

    def test_get_a2a_task(self, fake_http):
        fake_http.responses.append({"id": "t-1", "status": "completed"})
        out = BV7XService().get_a2a_task("t-1")
        assert out["status"] == "completed"

    def test_commerce_offerings(self, fake_http):
        fake_http.responses.append({"offerings": [{"id": "o1"}]})
        out = BV7XService().get_commerce_offerings()
        assert len(out["offerings"]) == 1

    def test_copy_trade_status(self, fake_http):
        fake_http.responses.append({"status": "active"})
        out = BV7XService().get_copy_trade_status()
        assert out["status"] == "active"


class TestApiKeyAuth:
    """Auth path: BV7X_API_KEY forwards as Bearer; missing → no_credentials."""

    def test_no_key_blocks_premium(self, fake_http, monkeypatch):
        monkeypatch.delenv("BV7X_API_KEY", raising=False)
        svc = BV7XService()
        svc.start()
        with pytest.raises(BV7XError) as exc:
            svc.get_oracle()
        assert exc.value.code == "no_credentials"

    def test_key_in_env_forwards_bearer(self, fake_http, monkeypatch):
        monkeypatch.setenv("BV7X_API_KEY", "test-token-abc")
        fake_http.responses.append({"signal": "DOWN", "confidence": 0.6})
        svc = BV7XService()
        svc.start()
        svc.get_oracle()
        headers = fake_http.calls[0]["headers"]
        assert headers["Authorization"] == "Bearer test-token-abc"

    def test_premium_endpoints_with_key(self, fake_http, monkeypatch):
        monkeypatch.setenv("BV7X_API_KEY", "k")
        svc = BV7XService()
        svc.start()
        # Hit each premium endpoint.
        for _ in range(4):
            fake_http.responses.append({"ok": True})
        svc.get_oracle()
        svc.get_oracle_premium()
        svc.get_copy_trade_next()
        svc.get_copy_trade_history()
        assert len(fake_http.calls) == 4
        # Every call had Bearer auth.
        for call in fake_http.calls:
            assert call["headers"]["Authorization"] == "Bearer k"

    def test_unauthorized_after_key_set_classifies(self, fake_http, monkeypatch):
        monkeypatch.setenv("BV7X_API_KEY", "stale-token")
        fake_http.responses.append(RuntimeError("HTTP 401 Unauthorized"))
        svc = BV7XService()
        svc.start()
        with pytest.raises(BV7XError) as exc:
            svc.get_oracle()
        assert exc.value.code == "token_gated"
        assert "BV7X_API_KEY" in exc.value.message

    def test_has_api_key_reflects_state(self, fake_http, monkeypatch):
        monkeypatch.delenv("BV7X_API_KEY", raising=False)
        svc = BV7XService()
        svc.start()
        assert svc.has_api_key() is False

        monkeypatch.setenv("BV7X_API_KEY", "k")
        svc2 = BV7XService()
        svc2.start()
        assert svc2.has_api_key() is True

    def test_start_log_no_key(self, fake_http, monkeypatch):
        # Just exercises the start() log path with no key.
        monkeypatch.delenv("BV7X_API_KEY", raising=False)
        BV7XService().start()

    def test_stop_clears_key(self, monkeypatch):
        monkeypatch.setenv("BV7X_API_KEY", "k")
        svc = BV7XService()
        svc.start()
        assert svc.has_api_key() is True
        svc.stop()
        assert svc.has_api_key() is False
