"""Tests for clawmes.lib.clawnch — the constants + helpers module."""

from __future__ import annotations

import importlib

import pytest

from clawmes.lib import clawnch as clawnch_const


class TestConstants:
    def test_token_address_lowercased(self):
        assert clawnch_const.TOKEN_ADDRESS == clawnch_const.TOKEN_ADDRESS.lower()
        assert len(clawnch_const.TOKEN_ADDRESS) == 42

    def test_burn_address_is_canonical_dead(self):
        assert clawnch_const.BURN_ADDRESS == "0x000000000000000000000000000000000000dead"

    def test_chain_id_is_base(self):
        assert clawnch_const.CHAIN_ID == 8453

    def test_decimals_is_18(self):
        assert clawnch_const.DECIMALS == 18

    def test_tier_order_starts_with_free(self):
        assert clawnch_const.TIER_ORDER[0] == "free"
        assert "pro" in clawnch_const.TIER_ORDER
        assert "max" in clawnch_const.TIER_ORDER


class TestEnvIntFallback:
    def test_uses_default_when_env_missing(self, monkeypatch):
        monkeypatch.delenv("CLAWNCH_TEST_VAR", raising=False)
        assert clawnch_const._env_int("CLAWNCH_TEST_VAR", 42) == 42

    def test_reads_env_when_set(self, monkeypatch):
        monkeypatch.setenv("CLAWNCH_TEST_VAR", "999")
        assert clawnch_const._env_int("CLAWNCH_TEST_VAR", 42) == 999

    def test_falls_back_on_non_integer(self, monkeypatch):
        monkeypatch.setenv("CLAWNCH_TEST_VAR", "not-a-number")
        assert clawnch_const._env_int("CLAWNCH_TEST_VAR", 42) == 42

    def test_falls_back_on_negative(self, monkeypatch):
        monkeypatch.setenv("CLAWNCH_TEST_VAR", "-100")
        assert clawnch_const._env_int("CLAWNCH_TEST_VAR", 42) == 42


class TestToWei:
    def test_scales_by_decimals(self):
        assert clawnch_const.to_wei(1) == 10**18
        assert clawnch_const.to_wei(0) == 0
        assert clawnch_const.to_wei(10_000_000) == 10_000_000 * 10**18


class TestBurnPrice:
    def test_returns_default_for_known_feature(self):
        assert clawnch_const.burn_price("bv7x_oracle_premium") == 100_000

    def test_returns_none_for_unknown(self):
        assert clawnch_const.burn_price("does_not_exist") is None

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("CLAWNCH_BURN_PRICE_BV7X_ORACLE_PREMIUM", "12345")
        assert clawnch_const.burn_price("bv7x_oracle_premium") == 12345

    def test_env_override_invalid_falls_back(self, monkeypatch):
        monkeypatch.setenv("CLAWNCH_BURN_PRICE_BV7X_ORACLE_PREMIUM", "garbage")
        assert clawnch_const.burn_price("bv7x_oracle_premium") == 100_000

    def test_env_override_negative_falls_back(self, monkeypatch):
        monkeypatch.setenv("CLAWNCH_BURN_PRICE_BV7X_ORACLE_PREMIUM", "-5")
        assert clawnch_const.burn_price("bv7x_oracle_premium") == 100_000


class TestTierAtLeast:
    def test_free_meets_free(self):
        assert clawnch_const.tier_at_least("free", "free")

    def test_pro_meets_free(self):
        assert clawnch_const.tier_at_least("pro", "free")

    def test_max_meets_pro(self):
        assert clawnch_const.tier_at_least("max", "pro")

    def test_free_does_not_meet_pro(self):
        assert not clawnch_const.tier_at_least("free", "pro")

    def test_pro_does_not_meet_max(self):
        assert not clawnch_const.tier_at_least("pro", "max")

    def test_unknown_tier_treated_as_free(self):
        assert not clawnch_const.tier_at_least("typo", "pro")
        assert clawnch_const.tier_at_least("pro", "typo")  # required typo → free


class TestModuleReload:
    """The threshold reads ENV at import time. Confirm reload picks up changes."""

    def test_reload_picks_up_env(self, monkeypatch):
        monkeypatch.setenv("CLAWNCH_PREMIUM_PRO_THRESHOLD", "7777")
        reloaded = importlib.reload(clawnch_const)
        try:
            assert reloaded.PRO_THRESHOLD == 7777
        finally:
            # Restore module state for downstream tests.
            monkeypatch.delenv("CLAWNCH_PREMIUM_PRO_THRESHOLD", raising=False)
            importlib.reload(clawnch_const)


class TestFeatureCatalog:
    def test_features_have_tier_and_label(self):
        for fid, meta in clawnch_const.FEATURES.items():
            assert isinstance(fid, str)
            assert meta["tier"] in clawnch_const.TIER_ORDER
            assert isinstance(meta["label"], str)
            assert meta["label"]

    def test_default_burn_prices_match_features(self):
        # Every feature with a default burn price is in FEATURES.
        for fid in clawnch_const.DEFAULT_BURN_PRICES:
            assert fid in clawnch_const.FEATURES


class TestSelectors:
    """Smoke-test that the selectors are 4-byte (8 hex char + 0x prefix) values."""

    @pytest.mark.parametrize(
        "selector",
        [
            clawnch_const.SELECTOR_USER_STAKE_COUNT,
            clawnch_const.SELECTOR_USER_STAKES,
            clawnch_const.SELECTOR_STAKES,
            clawnch_const.SELECTOR_TOTAL_STAKED,
        ],
    )
    def test_selector_format(self, selector):
        assert selector.startswith("0x")
        assert len(selector) == 10
        # All hex
        int(selector, 16)
