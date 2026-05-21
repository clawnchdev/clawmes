"""Tests for clawmes.lib.premium — the @premium_feature decorator."""

from __future__ import annotations

import json

import pytest

from clawmes.lib import clawnch as clawnch_const
from clawmes.lib import premium as prem
from clawmes.services import clawnch_premium as cp_mod


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(cp_mod, "_instance", None)


# ──────────────────────────────────────────────────────────────────────
#  Registration
# ──────────────────────────────────────────────────────────────────────


class TestRegistration:
    def test_decorator_rejects_unknown_feature_id(self):
        with pytest.raises(ValueError, match="not in clawmes.lib.clawnch.FEATURES"):

            @prem.premium_feature(feature_id="not_a_real_feature")
            def f():
                pass

    def test_decorator_registers_feature_id(self):
        @prem.premium_feature(feature_id="bv7x_oracle_premium")
        def f():
            return "ok"

        assert "bv7x_oracle_premium" in prem.registered_features()


# ──────────────────────────────────────────────────────────────────────
#  Gating — sync functions, tool-shape output
# ──────────────────────────────────────────────────────────────────────


class TestSyncGate:
    def test_grants_when_access(self, monkeypatch):
        @prem.premium_feature(feature_id="bv7x_oracle_premium")
        def fn(x):
            return f"served-{x}"

        # Patch the service so has_access returns True.
        class FakeSvc:
            def has_access(self, feature_id):
                return True

        monkeypatch.setattr(cp_mod, "get_clawnch_premium_service", lambda: FakeSvc())
        assert fn(42) == "served-42"

    def test_denies_with_tool_envelope(self, monkeypatch):
        @prem.premium_feature(feature_id="bv7x_oracle_premium")
        def fn():
            return "should not be reached"

        class FakeSvc:
            def has_access(self, feature_id):
                return False

        monkeypatch.setattr(cp_mod, "get_clawnch_premium_service", lambda: FakeSvc())
        out = fn()
        # tool_shape=True -> JSON envelope
        data = json.loads(out)
        assert data["isError"] is True
        assert data["details"]["feature_id"] == "bv7x_oracle_premium"
        assert any(path["type"] == "stake" for path in data["details"]["unlock_paths"])

    def test_denies_with_text_envelope(self, monkeypatch):
        @prem.premium_feature(feature_id="bv7x_oracle_premium", tool_shape=False)
        def fn():
            return "should not be reached"

        class FakeSvc:
            def has_access(self, feature_id):
                return False

        monkeypatch.setattr(cp_mod, "get_clawnch_premium_service", lambda: FakeSvc())
        out = fn()
        # tool_shape=False -> plain text
        assert "requires Clawnch premium" in out
        assert "https://clawn.ch/stake" in out

    def test_fails_closed_on_service_error(self, monkeypatch):
        """If the premium service raises, the gate denies (never bypasses)."""

        @prem.premium_feature(feature_id="bv7x_oracle_premium")
        def fn():
            return "served"

        def _explode():
            raise RuntimeError("singleton broken")

        monkeypatch.setattr(cp_mod, "get_clawnch_premium_service", _explode)
        out = fn()
        data = json.loads(out)
        assert data["isError"] is True


# ──────────────────────────────────────────────────────────────────────
#  Gating — async functions
# ──────────────────────────────────────────────────────────────────────


class TestAsyncGate:
    async def test_async_grant(self, monkeypatch):
        @prem.premium_feature(feature_id="bv7x_oracle_premium")
        async def fn(x):
            return f"async-{x}"

        class FakeSvc:
            def has_access(self, feature_id):
                return True

        monkeypatch.setattr(cp_mod, "get_clawnch_premium_service", lambda: FakeSvc())
        result = await fn(7)
        assert result == "async-7"

    async def test_async_deny(self, monkeypatch):
        @prem.premium_feature(feature_id="bv7x_oracle_premium")
        async def fn():
            return "should not"

        class FakeSvc:
            def has_access(self, feature_id):
                return False

        monkeypatch.setattr(cp_mod, "get_clawnch_premium_service", lambda: FakeSvc())
        out = await fn()
        data = json.loads(out)
        assert data["isError"] is True


# ──────────────────────────────────────────────────────────────────────
#  Denial body
# ──────────────────────────────────────────────────────────────────────


class TestDenialBody:
    def test_body_contains_burn_path_when_priced(self):
        body = prem._gate_denial("bv7x_oracle_premium")
        types = [p["type"] for p in body["unlock_paths"]]
        assert "stake" in types
        assert "burn" in types
        burn = next(p for p in body["unlock_paths"] if p["type"] == "burn")
        assert burn["cost_clawnch"] == 100_000

    def test_body_omits_burn_path_when_unpriced(self, monkeypatch):
        monkeypatch.setitem(
            clawnch_const.FEATURES,
            "stake_only",
            {"tier": "max", "label": "Stake only"},
        )
        body = prem._gate_denial("stake_only")
        types = [p["type"] for p in body["unlock_paths"]]
        assert "stake" in types
        assert "burn" not in types

    def test_text_renders_paths(self):
        text = prem._gate_text("bv7x_oracle_premium")
        assert "Stake" in text
        assert "Burn" in text
        assert "https://clawn.ch/stake" in text

    def test_gate_grants(self, monkeypatch):
        class FakeSvc:
            def has_access(self, feature_id):
                return True

        monkeypatch.setattr(cp_mod, "get_clawnch_premium_service", lambda: FakeSvc())
        assert prem.gate("bv7x_oracle_premium") is None

    def test_gate_denies_returns_envelope(self, monkeypatch):
        class FakeSvc:
            def has_access(self, feature_id):
                return False

        monkeypatch.setattr(cp_mod, "get_clawnch_premium_service", lambda: FakeSvc())
        out = prem.gate("bv7x_oracle_premium")
        assert isinstance(out, str)
        data = json.loads(out)
        assert data["isError"] is True

    def test_gate_text_shape(self, monkeypatch):
        class FakeSvc:
            def has_access(self, feature_id):
                return False

        monkeypatch.setattr(cp_mod, "get_clawnch_premium_service", lambda: FakeSvc())
        out = prem.gate("bv7x_oracle_premium", tool_shape=False)
        assert isinstance(out, str)
        assert "requires Clawnch premium" in out

    def test_gate_rejects_unknown_feature(self):
        with pytest.raises(ValueError, match="not in clawmes.lib.clawnch.FEATURES"):
            prem.gate("does_not_exist")

    def test_text_omits_unknown_path_types(self, monkeypatch):
        # Force an unknown path type into the body so the renderer's
        # fallthrough is exercised.
        monkeypatch.setattr(
            prem,
            "_gate_denial",
            lambda fid: {
                "feature_id": fid,
                "label": "X",
                "required_tier": "pro",
                "unlock_paths": [{"type": "mystery", "summary": "?"}],
            },
        )
        text = prem._gate_text("bv7x_oracle_premium")
        # Renderer doesn't crash; unknown type is silently dropped.
        assert "requires Clawnch premium" in text
