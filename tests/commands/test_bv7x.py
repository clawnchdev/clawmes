"""Tests for /bv7x and /btc slash commands."""

from __future__ import annotations

import pytest

from clawmes.commands import bv7x as bv7x_cmd
from clawmes.services import bv7x as bv7x_svc


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(bv7x_svc, "_instance", None)
    monkeypatch.delenv("BV7X_API_KEY", raising=False)


@pytest.fixture
def fake_http(monkeypatch):
    class FakeHttp:
        responses_by_url: dict = {}
        calls: list = []

        def __call__(self, url, *, headers=None, timeout=30.0, **kw):
            self.calls.append(url)
            # Match path suffix to pre-registered responses.
            for suffix, resp in self.responses_by_url.items():
                if suffix in url:
                    if isinstance(resp, Exception):
                        raise resp
                    return resp
            raise AssertionError(f"no fake response for {url}")

    fake = FakeHttp()
    monkeypatch.setattr(bv7x_svc, "http_get", fake)
    return fake


# --- /bv7x --------------------------------------------------------------


class TestHandleBv7x:
    async def test_basic(self, fake_http):
        fake_http.responses_by_url = {
            "/scorecard": {
                "success": True,
                "summary": {
                    "totalPredictions": 83,
                    "accuracy": 61.4,
                    "streak": {"count": 1, "type": "LOSS"},
                },
            },
            "/regime": {"regime": "BEAR_TREND", "risk_level": "High"},
            "/agent/identity": {"agent_id": 28841},
        }
        out = await bv7x_cmd.handle_bv7x("")
        assert "BV-7X status" in out
        assert "83 predictions" in out
        assert "61.4%" in out
        assert "BEAR_TREND" in out
        assert "#28841" in out
        # No API key → premium hint shown.
        assert "BV7X_API_KEY" in out

    async def test_with_api_key_hides_hint(self, fake_http, monkeypatch):
        monkeypatch.setenv("BV7X_API_KEY", "k")
        # Force the service singleton to pick up the new env var.
        from clawmes.services import bv7x as bv7x_svc_mod

        monkeypatch.setattr(bv7x_svc_mod, "_instance", None)
        bv7x_svc_mod.get_bv7x_service().start()

        fake_http.responses_by_url = {
            "/scorecard": {"success": True, "summary": {}},
            "/regime": {"regime": "BULL"},
            "/agent/identity": {"agent_id": 1},
        }
        out = await bv7x_cmd.handle_bv7x("")
        assert "BV7X_API_KEY" not in out

    async def test_scorecard_error(self, fake_http):
        fake_http.responses_by_url = {
            "/scorecard": RuntimeError("HTTP 429"),
            "/regime": {"regime": "BEAR"},
            "/agent/identity": {"agent_id": 1},
        }
        out = await bv7x_cmd.handle_bv7x("")
        assert "Scorecard error" in out

    async def test_regime_error(self, fake_http):
        fake_http.responses_by_url = {
            "/scorecard": {"success": True, "summary": {}},
            "/regime": RuntimeError("HTTP 429"),
            "/agent/identity": {"agent_id": 1},
        }
        out = await bv7x_cmd.handle_bv7x("")
        assert "Regime error" in out

    async def test_identity_error_silent(self, fake_http):
        # Identity errors should not appear in the output.
        fake_http.responses_by_url = {
            "/scorecard": {"success": True, "summary": {}},
            "/regime": {"regime": "BULL"},
            "/agent/identity": RuntimeError("HTTP 404"),
        }
        out = await bv7x_cmd.handle_bv7x("")
        assert "Identity" not in out
        assert "agent" not in out.lower() or "agent:" not in out.lower()


# --- /btc --------------------------------------------------------------


class TestHandleBtc:
    async def test_basic(self, fake_http):
        fake_http.responses_by_url = {
            "/btc-price": {"price": 76754, "change_24h": -2.1},
            "/fear-greed": {"value": 25, "classification": "Fear"},
            "/etf-flows": {"flow_7d": "-1.68B"},
        }
        out = await bv7x_cmd.handle_btc("")
        assert "76754" in out
        assert "-2.1%" in out
        assert "25 (Fear)" in out
        assert "-1.68B" in out

    async def test_positive_change(self, fake_http):
        fake_http.responses_by_url = {
            "/btc-price": {"price": 80000, "change_24h": 5.0},
            "/fear-greed": {"value": 50},
            "/etf-flows": {"flow_7d": "100M"},
        }
        out = await bv7x_cmd.handle_btc("")
        assert "+5.0%" in out

    async def test_price_error(self, fake_http):
        fake_http.responses_by_url = {
            "/btc-price": RuntimeError("HTTP 429"),
            "/fear-greed": {"value": 25},
            "/etf-flows": {"flow_7d": "0"},
        }
        out = await bv7x_cmd.handle_btc("")
        assert "Price error" in out

    async def test_fg_error(self, fake_http):
        fake_http.responses_by_url = {
            "/btc-price": {"price": 1, "change_24h": 0},
            "/fear-greed": RuntimeError("HTTP 404"),
            "/etf-flows": {"flow_7d": "0"},
        }
        out = await bv7x_cmd.handle_btc("")
        assert "F&G error" in out

    async def test_etf_error_silent(self, fake_http):
        # ETF errors are silent.
        fake_http.responses_by_url = {
            "/btc-price": {"price": 1, "change_24h": 0},
            "/fear-greed": {"value": 50},
            "/etf-flows": RuntimeError("HTTP 404"),
        }
        out = await bv7x_cmd.handle_btc("")
        assert "ETF" not in out


# --- record helper -----------------------------------------------------


class TestRecord:
    async def test_record_swallows_failure(self, monkeypatch):
        # When record_command_call raises, _record must swallow it
        # (covers the bare ``except: pass`` branch).
        from clawmes.services import command_history as ch_mod

        def _boom(*a, **kw):
            raise RuntimeError("simulated record failure")

        monkeypatch.setattr(ch_mod, "record_command_call", _boom)
        # No assertion needed — just verify _record doesn't raise.
        bv7x_cmd._record("test", "args", "result")

    async def test_record_when_present(self, monkeypatch):
        # Cover the happy path: real command_history with our fake recorder.
        from clawmes.services import command_history as ch_mod

        captured: list[tuple[str, str, str]] = []
        monkeypatch.setattr(
            ch_mod,
            "record_command_call",
            lambda n, a, r: captured.append((n, a, r)),
        )
        bv7x_cmd._record("x", "y", "z")
        assert captured == [("x", "y", "z")]


# --- registration ------------------------------------------------------


class TestRegister:
    def test_registers_two_commands(self):
        captured = []

        class FakeCtx:
            def register_command(self, **kw):
                captured.append(kw["name"])

        bv7x_cmd.register(FakeCtx())
        assert set(captured) == {"bv7x", "btc"}
