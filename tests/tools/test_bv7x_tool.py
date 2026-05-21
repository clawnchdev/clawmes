"""Tests for the ``bv7x`` tool (wraps BV7XService)."""

from __future__ import annotations

import json

import pytest

from clawmes.services import bv7x as bv7x_svc
from clawmes.tools.bv7x import bv7x, register


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    from clawmes.policy import storage as policy_storage

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(bv7x_svc, "_instance", None)
    policy_storage.save_policies([])


@pytest.fixture
def fake_http(monkeypatch):
    class FakeHttp:
        responses: list = []

        def __call__(self, *args, **kw):
            if not self.responses:
                raise AssertionError("no fake response queued")
            response = self.responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response

    fake = FakeHttp()
    monkeypatch.setattr(bv7x_svc, "http_get", fake)
    return fake


def _call(action):
    return json.loads(bv7x({"action": action}))


class TestRegime:
    def test_basic(self, fake_http):
        fake_http.responses.append({"regime": "BEAR_TREND", "risk_level": "High", "fear_greed": 11})
        out = _call("regime")
        assert out["details"]["regime"] == "BEAR_TREND"
        assert "BEAR_TREND" in out["content"][0]["text"]
        assert "risk=High" in out["content"][0]["text"]

    def test_summary_without_risk_level(self, fake_http):
        fake_http.responses.append({"regime": "BULL"})
        out = _call("regime")
        assert "BV-7X regime: BULL" in out["content"][0]["text"]


class TestIdentity:
    def test_basic(self, fake_http):
        fake_http.responses.append({"agent_id": 28841, "reputation": 0.87})
        out = _call("identity")
        assert out["details"]["agent_id"] == 28841
        assert "28841" in out["content"][0]["text"]
        assert "reputation=0.87" in out["content"][0]["text"]

    def test_no_reputation(self, fake_http):
        fake_http.responses.append({"agent_id": 1})
        out = _call("identity")
        # Summary should mention the agent but not include "reputation=".
        assert "agent #1" in out["content"][0]["text"]
        assert "reputation=" not in out["content"][0]["text"]


class TestDiscover:
    def test_basic(self, fake_http):
        fake_http.responses.append(
            {
                "skills": ["get_market_context", "get_signal_summary"],
                "version": "0.3.0",
            }
        )
        out = _call("discover")
        assert "2 skill(s)" in out["content"][0]["text"]
        assert "v0.3.0" in out["content"][0]["text"]


class TestErrorPropagation:
    def test_rate_limit(self, fake_http):
        fake_http.responses.append(RuntimeError("HTTP 429"))
        out = _call("regime")
        assert out["isError"] is True
        assert out["details"]["error_code"] == "rate_limited"

    def test_token_gated(self, fake_http):
        fake_http.responses.append(RuntimeError("requires BV7X token"))
        out = _call("regime")
        assert out["details"]["error_code"] == "token_gated"


class TestInvalidAction:
    def test_unknown(self):
        out = json.loads(bv7x({"action": "explode"}))
        assert out["details"]["error_code"] == "param_error"

    def test_missing(self):
        out = json.loads(bv7x({}))
        assert out["details"]["error_code"] == "param_error"


class TestRegister:
    def test_register(self):
        captured = []

        class FakeCtx:
            def register_tool(self, **kw):
                captured.append(kw)

        register(FakeCtx())
        assert len(captured) == 1
        assert captured[0]["name"] == "bv7x"
