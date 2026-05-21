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
        fake_http.responses.append(RuntimeError("requires BV7X token holdings"))
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
