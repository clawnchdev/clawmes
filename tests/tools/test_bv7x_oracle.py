"""Tests for the ``bv7x_oracle`` tool."""

from __future__ import annotations

import json

import pytest

from clawmes.services import bv7x as bv7x_svc
from clawmes.tools.bv7x_oracle import bv7x_oracle, register


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    from clawmes.policy import storage as policy_storage

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(bv7x_svc, "_instance", None)
    monkeypatch.delenv("BV7X_API_KEY", raising=False)
    policy_storage.save_policies([])


@pytest.fixture
def fake_http(monkeypatch):
    class FakeHttp:
        responses: list = []
        calls: list = []

        def __call__(self, url, *, headers=None, timeout=30.0, **kw):
            self.calls.append({"url": url, "headers": headers})
            if not self.responses:
                raise AssertionError("no fake response queued")
            r = self.responses.pop(0)
            if isinstance(r, Exception):
                raise r
            return r

    fake = FakeHttp()
    monkeypatch.setattr(bv7x_svc, "http_get", fake)
    return fake


def _call(action, **kw):
    return json.loads(bv7x_oracle({"action": action, **kw}))


# --- free actions ------------------------------------------------------


class TestScorecard:
    def test_basic(self, fake_http):
        fake_http.responses.append(
            {
                "success": True,
                "summary": {
                    "totalPredictions": 83,
                    "accuracy": 61.4,
                    "streak": {"count": 1, "type": "LOSS"},
                },
            }
        )
        out = _call("scorecard")
        assert "83 prediction" in out["content"][0]["text"]
        assert "61.4%" in out["content"][0]["text"]
        assert "1L" in out["content"][0]["text"]

    def test_custom_horizon(self, fake_http):
        fake_http.responses.append({"success": True, "summary": {}})
        _call("scorecard", horizon=2)
        assert "horizon=2" in fake_http.calls[0]["url"]


class TestSignalMetadata:
    def test_basic(self, fake_http):
        fake_http.responses.append(
            {
                "signal": "GATED",
                "market_context": {"btc_price": 76754, "fear_greed": 25},
            }
        )
        out = _call("signal_metadata")
        assert "GATED" in out["content"][0]["text"]
        assert "76754" in out["content"][0]["text"]
        assert "F&G=25" in out["content"][0]["text"]

    def test_custom_horizon_str(self, fake_http):
        fake_http.responses.append({"signal": "?", "market_context": {}})
        _call("signal_metadata", horizon_str="3d")
        assert "horizon=3d" in fake_http.calls[0]["url"]


class TestOnchainLatest:
    def test_basic(self, fake_http):
        fake_http.responses.append({"direction": "UP", "uid": "0x" + "ab" * 16})
        out = _call("onchain_latest")
        assert "UP" in out["content"][0]["text"]
        assert "..." in out["content"][0]["text"]  # uid truncated

    def test_short_uid(self, fake_http):
        fake_http.responses.append({"direction": "DOWN", "uid": "0xshort"})
        out = _call("onchain_latest")
        # Short uids aren't truncated.
        assert "0xshort" in out["content"][0]["text"]


class TestOnchainHistory:
    def test_basic(self, fake_http):
        fake_http.responses.append({"attestations": [{"uid": "0x1"}, {"uid": "0x2"}]})
        out = _call("onchain_history")
        assert "2 on-chain attestation" in out["content"][0]["text"]

    def test_custom_limit(self, fake_http):
        fake_http.responses.append({"attestations": []})
        _call("onchain_history", limit=5)
        assert "limit=5" in fake_http.calls[0]["url"]

    def test_alternate_items_key(self, fake_http):
        # Some payloads use 'items' instead of 'attestations'.
        fake_http.responses.append({"items": [{"uid": "x"}]})
        out = _call("onchain_history")
        assert "1 on-chain" in out["content"][0]["text"]


class TestOnchainStats:
    def test_basic(self, fake_http):
        fake_http.responses.append({"total": 83, "accuracy": 61.4})
        out = _call("onchain_stats")
        assert "83 total" in out["content"][0]["text"]
        assert "61.4" in out["content"][0]["text"]


class TestVerifyUid:
    def test_valid(self, fake_http):
        fake_http.responses.append({"valid": True, "uid": "0x" + "aa" * 32})
        out = _call("verify_uid", uid="0x" + "aa" * 32)
        assert "VALID" in out["content"][0]["text"]
        assert "INVALID" not in out["content"][0]["text"]

    def test_invalid(self, fake_http):
        fake_http.responses.append({"valid": False})
        out = _call("verify_uid", uid="0x" + "00" * 32)
        assert "INVALID" in out["content"][0]["text"]

    def test_missing_uid(self):
        out = _call("verify_uid")
        assert out["details"]["error_code"] == "param_error"


# --- token-gated actions -----------------------------------------------


class TestPremiumWithoutKey:
    """Without BV7X_API_KEY the gated actions surface a clear error."""

    def test_oracle_blocked(self, fake_http):
        out = _call("oracle")
        assert out["details"]["error_code"] == "no_credentials"

    def test_oracle_premium_blocked(self, fake_http):
        out = _call("oracle_premium")
        assert out["details"]["error_code"] == "no_credentials"

    def test_copy_trade_next_blocked(self, fake_http):
        out = _call("copy_trade_next")
        assert out["details"]["error_code"] == "no_credentials"

    def test_copy_trade_history_blocked(self, fake_http):
        out = _call("copy_trade_history")
        assert out["details"]["error_code"] == "no_credentials"


class TestPremiumWithKey:
    @pytest.fixture(autouse=True)
    def _set_key(self, monkeypatch):
        monkeypatch.setenv("BV7X_API_KEY", "test-token")
        # Re-bind the singleton so it picks up the env var via start().
        from clawmes.services import bv7x as bv7x_svc_mod

        bv7x_svc_mod._instance = None
        svc = bv7x_svc_mod.get_bv7x_service()
        svc.start()

    def test_oracle(self, fake_http):
        fake_http.responses.append({"direction": "UP", "confidence": 0.7})
        out = _call("oracle")
        assert "UP" in out["content"][0]["text"]
        assert "0.7" in out["content"][0]["text"]

    def test_oracle_premium(self, fake_http):
        fake_http.responses.append({"direction": "DOWN", "confidence": 0.5})
        out = _call("oracle_premium")
        assert "DOWN" in out["content"][0]["text"]
        assert "premium" in out["content"][0]["text"]

    def test_copy_trade_next(self, fake_http):
        fake_http.responses.append({"market": "BTC100k", "side": "YES"})
        out = _call("copy_trade_next")
        assert "YES on BTC100k" in out["content"][0]["text"]

    def test_copy_trade_history(self, fake_http):
        fake_http.responses.append({"trades": [{"id": 1}, {"id": 2}]})
        out = _call("copy_trade_history")
        assert "2 trade(s)" in out["content"][0]["text"]


# --- dispatch / register -----------------------------------------------


class TestDispatch:
    def test_missing_action(self):
        out = json.loads(bv7x_oracle({}))
        assert out["details"]["error_code"] == "param_error"

    def test_unknown_action(self):
        out = json.loads(bv7x_oracle({"action": "explode"}))
        assert out["details"]["error_code"] == "param_error"


class TestRegister:
    def test_register(self):
        captured = []

        class FakeCtx:
            def register_tool(self, **kw):
                captured.append(kw)

        register(FakeCtx())
        assert captured[0]["name"] == "bv7x_oracle"
