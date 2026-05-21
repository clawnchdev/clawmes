"""Tests for the ``bv7x_market`` tool."""

from __future__ import annotations

import json

import pytest

from clawmes.services import bv7x as bv7x_svc
from clawmes.tools.bv7x_market import bv7x_market, register


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
            r = self.responses.pop(0)
            if isinstance(r, Exception):
                raise r
            return r

    fake = FakeHttp()
    monkeypatch.setattr(bv7x_svc, "http_get", fake)
    return fake


def _call(action):
    return json.loads(bv7x_market({"action": action}))


class TestBtcPrice:
    def test_positive_change(self, fake_http):
        fake_http.responses.append({"price": 76754, "change_24h": 2.5})
        out = _call("btc_price")
        assert "76754" in out["content"][0]["text"]
        assert "+2.5%" in out["content"][0]["text"]

    def test_negative_change(self, fake_http):
        fake_http.responses.append({"price": 76754, "change_24h": -2.1})
        out = _call("btc_price")
        assert "-2.1%" in out["content"][0]["text"]


class TestFearGreed:
    def test_with_label(self, fake_http):
        fake_http.responses.append({"value": 25, "classification": "Fear"})
        out = _call("fear_greed")
        assert "25" in out["content"][0]["text"]
        assert "Fear" in out["content"][0]["text"]

    def test_no_label(self, fake_http):
        fake_http.responses.append({"value": 50})
        out = _call("fear_greed")
        assert "50" in out["content"][0]["text"]


class TestEtfFlows:
    def test_basic(self, fake_http):
        fake_http.responses.append({"flow_7d": "-1.68B", "flow_30d": "2.5B"})
        out = _call("etf_flows")
        assert "-1.68B" in out["content"][0]["text"]
        assert "2.5B" in out["content"][0]["text"]


class TestErrorPropagation:
    def test_rate_limited(self, fake_http):
        fake_http.responses.append(RuntimeError("HTTP 429"))
        out = _call("btc_price")
        assert out["details"]["error_code"] == "rate_limited"


class TestDispatch:
    def test_unknown_action(self):
        out = json.loads(bv7x_market({"action": "explode"}))
        assert out["details"]["error_code"] == "param_error"

    def test_missing_action(self):
        out = json.loads(bv7x_market({}))
        assert out["details"]["error_code"] == "param_error"


class TestRegister:
    def test_register(self):
        captured = []

        class FakeCtx:
            def register_tool(self, **kw):
                captured.append(kw)

        register(FakeCtx())
        assert captured[0]["name"] == "bv7x_market"
