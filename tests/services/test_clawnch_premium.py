"""Tests for clawmes.services.clawnch_premium."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from clawmes.lib import clawnch as clawnch_const
from clawmes.services import clawnch_premium as cp_mod
from clawmes.services.clawnch_premium import ClawnchPremiumService
from clawmes.wallet.state import WalletState

ADDR = "0x" + "a" * 40


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(cp_mod, "_instance", None)


@pytest.fixture
def svc():
    return ClawnchPremiumService()


# ──────────────────────────────────────────────────────────────────────
#  Lifecycle
# ──────────────────────────────────────────────────────────────────────


class TestLifecycle:
    def test_start_logs_and_returns(self, svc):
        svc.start()  # no exception

    def test_stop_clears_state(self, svc):
        svc.set_jwt(ADDR, "jwt-x", "pro")
        svc.redeem_burn("bv7x_oracle_premium", "0xtxhash", address=ADDR)
        svc.stop()
        assert svc.get_jwt(ADDR) is None
        assert not svc.has_access("bv7x_oracle_premium", ADDR)

    def test_health_includes_state(self, svc):
        h = svc.health()
        assert h["id"] == "clawnch_premium"
        assert h["status"] == "ok"
        assert "escrow_deployed" in h


# ──────────────────────────────────────────────────────────────────────
#  Get tier — no wallet
# ──────────────────────────────────────────────────────────────────────


class TestGetTierNoAddress:
    def test_returns_free_when_no_address_and_no_active_wallet(self, svc):
        # No wallet connected, no address passed.
        with patch("clawmes.services.wallet.get_wallet_state") as get_state:
            get_state.return_value = WalletState.disconnected()
            assert svc.get_tier() == "free"

    def test_returns_free_when_wallet_service_raises(self, svc):
        with patch("clawmes.services.wallet.get_wallet_state", side_effect=RuntimeError("boom")):
            assert svc.get_tier() == "free"


# ──────────────────────────────────────────────────────────────────────
#  Get tier — on-chain reads
# ──────────────────────────────────────────────────────────────────────


def _eth_call_balance(return_balance_wei: int):
    """Builds a side-effect function for the rpc.eth_call mock that
    returns ``return_balance_wei`` (hex) for any call.
    """

    def _impl(*, to, data, chain_id, block="latest"):
        return "0x" + format(return_balance_wei, "064x")

    return _impl


class TestGetTierBalanceFallback:
    """Until ESCROW_ADDRESS is set, premium falls back to balance."""

    def test_free_when_balance_below_pro(self, svc):
        mock_rpc = type("R", (), {})()
        mock_rpc.eth_call = _eth_call_balance(0)
        with patch("clawmes.services.rpc.get_rpc_service", return_value=mock_rpc):
            assert svc.get_tier(ADDR) == "free"

    def test_pro_when_balance_meets_threshold(self, svc):
        pro_wei = clawnch_const.to_wei(clawnch_const.PRO_THRESHOLD)
        mock_rpc = type("R", (), {})()
        mock_rpc.eth_call = _eth_call_balance(pro_wei)
        with patch("clawmes.services.rpc.get_rpc_service", return_value=mock_rpc):
            assert svc.get_tier(ADDR) == "pro"

    def test_max_when_balance_meets_max(self, svc):
        max_wei = clawnch_const.to_wei(clawnch_const.MAX_THRESHOLD)
        mock_rpc = type("R", (), {})()
        mock_rpc.eth_call = _eth_call_balance(max_wei)
        with patch("clawmes.services.rpc.get_rpc_service", return_value=mock_rpc):
            assert svc.get_tier(ADDR) == "max"

    def test_rpc_failure_returns_free(self, svc):
        with patch("clawmes.services.rpc.get_rpc_service", side_effect=RuntimeError("rpc dead")):
            assert svc.get_tier(ADDR) == "free"

    def test_balance_decode_failure_returns_free(self, svc):
        mock_rpc = type("R", (), {})()

        def _explode(*, to, data, chain_id, block="latest"):
            raise RuntimeError("network blip")

        mock_rpc.eth_call = _explode
        with patch("clawmes.services.rpc.get_rpc_service", return_value=mock_rpc):
            assert svc.get_tier(ADDR) == "free"


# ──────────────────────────────────────────────────────────────────────
#  Get tier — stake reads
# ──────────────────────────────────────────────────────────────────────


def _eth_call_stake_setup(
    balance_wei: int,
    stake_ids: list[int],
    stakes_by_id: dict[int, tuple[int, int]],
):
    """Build an eth_call mock that returns balance/userStakes/stakes
    depending on the calldata's function selector.

    ``stakes_by_id`` maps stake id → (amount_wei, multiplier_bps).
    """

    def _impl(*, to, data, chain_id, block="latest"):
        selector = data[:10]
        if selector == "0x70a08231":  # balanceOf
            return "0x" + format(balance_wei, "064x")
        if selector == clawnch_const.SELECTOR_USER_STAKES:
            # ABI encoding: offset(0x20) || length || items
            offset = format(0x20, "064x")
            length = format(len(stake_ids), "064x")
            items = "".join(format(sid, "064x") for sid in stake_ids)
            return "0x" + offset + length + items
        if selector == clawnch_const.SELECTOR_STAKES:
            sid = int(data[10:], 16)
            amount, multiplier = stakes_by_id.get(sid, (0, 0))
            # 5 slots: user, amount(uint96), tierIndex, stakedAt, multiplierBps
            slot_user = "0" * 64
            slot_amount = format(amount, "064x")
            slot_tier = "0" * 64
            slot_at = "0" * 64
            slot_mult = format(multiplier, "064x")
            return "0x" + slot_user + slot_amount + slot_tier + slot_at + slot_mult
        return "0x"

    return _impl


class TestGetTierStakeBased:
    def test_pro_from_stake(self, svc, monkeypatch):
        # Pretend escrow is deployed at a fake address.
        fake_escrow = "0x" + "b" * 40
        monkeypatch.setattr(clawnch_const, "ESCROW_ADDRESS", fake_escrow)
        # 10M tokens × 1x multiplier = 10M weighted = pro threshold
        pro_amount = clawnch_const.to_wei(clawnch_const.PRO_THRESHOLD)
        mock_rpc = type("R", (), {})()
        mock_rpc.eth_call = _eth_call_stake_setup(
            balance_wei=0,
            stake_ids=[42],
            stakes_by_id={42: (pro_amount, 100)},  # 1x
        )
        with patch("clawmes.services.rpc.get_rpc_service", return_value=mock_rpc):
            assert svc.get_tier(ADDR) == "pro"

    def test_max_from_weighted_stake(self, svc, monkeypatch):
        fake_escrow = "0x" + "b" * 40
        monkeypatch.setattr(clawnch_const, "ESCROW_ADDRESS", fake_escrow)
        # 12.5M tokens × 4x (gold) = 50M weighted = max threshold
        amount = clawnch_const.to_wei(12_500_000)
        mock_rpc = type("R", (), {})()
        mock_rpc.eth_call = _eth_call_stake_setup(
            balance_wei=0,
            stake_ids=[7],
            stakes_by_id={7: (amount, 400)},  # 4x
        )
        with patch("clawmes.services.rpc.get_rpc_service", return_value=mock_rpc):
            assert svc.get_tier(ADDR) == "max"

    def test_aggregates_multiple_stakes(self, svc, monkeypatch):
        fake_escrow = "0x" + "b" * 40
        monkeypatch.setattr(clawnch_const, "ESCROW_ADDRESS", fake_escrow)
        # 5M × 1x + 5M × 1x = 10M weighted = pro
        amt = clawnch_const.to_wei(5_000_000)
        mock_rpc = type("R", (), {})()
        mock_rpc.eth_call = _eth_call_stake_setup(
            balance_wei=0,
            stake_ids=[1, 2],
            stakes_by_id={1: (amt, 100), 2: (amt, 100)},
        )
        with patch("clawmes.services.rpc.get_rpc_service", return_value=mock_rpc):
            assert svc.get_tier(ADDR) == "pro"

    def test_withdrawn_stakes_skipped(self, svc, monkeypatch):
        fake_escrow = "0x" + "b" * 40
        monkeypatch.setattr(clawnch_const, "ESCROW_ADDRESS", fake_escrow)
        # Stake 1 withdrawn (amount=0), stake 2 = 10M
        amt = clawnch_const.to_wei(clawnch_const.PRO_THRESHOLD)
        mock_rpc = type("R", (), {})()
        mock_rpc.eth_call = _eth_call_stake_setup(
            balance_wei=0,
            stake_ids=[1, 2],
            stakes_by_id={1: (0, 100), 2: (amt, 100)},
        )
        with patch("clawmes.services.rpc.get_rpc_service", return_value=mock_rpc):
            assert svc.get_tier(ADDR) == "pro"

    def test_user_stakes_call_failure_returns_zero(self, svc, monkeypatch):
        fake_escrow = "0x" + "b" * 40
        monkeypatch.setattr(clawnch_const, "ESCROW_ADDRESS", fake_escrow)
        mock_rpc = type("R", (), {})()
        call_count = [0]

        def _impl(*, to, data, chain_id, block="latest"):
            call_count[0] += 1
            selector = data[:10]
            if selector == "0x70a08231":
                return "0x" + format(0, "064x")
            # The getUserStakes call raises
            raise RuntimeError("stakes view reverted")

        mock_rpc.eth_call = _impl
        with patch("clawmes.services.rpc.get_rpc_service", return_value=mock_rpc):
            assert svc.get_tier(ADDR) == "free"

    def test_individual_stake_read_failure_skipped(self, svc, monkeypatch):
        """Per-stake read failures don't bring down the aggregate."""
        fake_escrow = "0x" + "b" * 40
        monkeypatch.setattr(clawnch_const, "ESCROW_ADDRESS", fake_escrow)
        amt = clawnch_const.to_wei(clawnch_const.PRO_THRESHOLD)
        mock_rpc = type("R", (), {})()

        def _impl(*, to, data, chain_id, block="latest"):
            selector = data[:10]
            if selector == "0x70a08231":
                return "0x" + format(0, "064x")
            if selector == clawnch_const.SELECTOR_USER_STAKES:
                offset = format(0x20, "064x")
                length = format(2, "064x")
                items = format(1, "064x") + format(2, "064x")
                return "0x" + offset + length + items
            if selector == clawnch_const.SELECTOR_STAKES:
                sid = int(data[10:], 16)
                if sid == 1:
                    raise RuntimeError("stake 1 reverted")
                slot_user = "0" * 64
                slot_amount = format(amt, "064x")
                slot_tier = "0" * 64
                slot_at = "0" * 64
                slot_mult = format(100, "064x")
                return "0x" + slot_user + slot_amount + slot_tier + slot_at + slot_mult
            return "0x"

        mock_rpc.eth_call = _impl
        with patch("clawmes.services.rpc.get_rpc_service", return_value=mock_rpc):
            assert svc.get_tier(ADDR) == "pro"

    def test_empty_user_stakes_array(self, svc, monkeypatch):
        """If the user has zero stakes, weighted stake is 0."""
        fake_escrow = "0x" + "b" * 40
        monkeypatch.setattr(clawnch_const, "ESCROW_ADDRESS", fake_escrow)
        mock_rpc = type("R", (), {})()
        mock_rpc.eth_call = _eth_call_stake_setup(
            balance_wei=0,
            stake_ids=[],
            stakes_by_id={},
        )
        with patch("clawmes.services.rpc.get_rpc_service", return_value=mock_rpc):
            assert svc.get_tier(ADDR) == "free"


# ──────────────────────────────────────────────────────────────────────
#  Tier cache
# ──────────────────────────────────────────────────────────────────────


class TestTierCache:
    def test_cached_result_reused(self, svc, monkeypatch):
        # Pretend escrow not deployed, balance is 0.
        mock_rpc = type("R", (), {})()
        call_count = [0]

        def _impl(*, to, data, chain_id, block="latest"):
            call_count[0] += 1
            return "0x" + format(0, "064x")

        mock_rpc.eth_call = _impl
        with patch("clawmes.services.rpc.get_rpc_service", return_value=mock_rpc):
            svc.get_tier(ADDR)
            svc.get_tier(ADDR)
        assert call_count[0] == 1  # second call hit cache, not RPC

    def test_set_jwt_invalidates_cache(self, svc, monkeypatch):
        # Prime the cache as "free".
        mock_rpc = type("R", (), {})()
        mock_rpc.eth_call = _eth_call_balance(0)
        with patch("clawmes.services.rpc.get_rpc_service", return_value=mock_rpc):
            assert svc.get_tier(ADDR) == "free"
        # Now set a JWT — should override the cache.
        svc.set_jwt(ADDR, "jwt-x", "max")
        assert svc.get_tier(ADDR) == "max"


# ──────────────────────────────────────────────────────────────────────
#  has_access
# ──────────────────────────────────────────────────────────────────────


class TestHasAccess:
    def test_unknown_feature_denied(self, svc):
        assert not svc.has_access("does_not_exist", ADDR)

    def test_free_tier_features_always_allowed(self, svc, monkeypatch):
        """Synthesize a free-tier feature into the catalog for this test."""
        monkeypatch.setitem(
            clawnch_const.FEATURES,
            "free_feature_x",
            {"tier": "free", "label": "free thing"},
        )
        # No on-chain reads needed; just the catalog gate.
        assert svc.has_access("free_feature_x", ADDR)

    def test_pro_feature_denied_when_free(self, svc):
        with patch("clawmes.services.wallet.get_wallet_state") as get_state:
            get_state.return_value = WalletState.disconnected()
            assert not svc.has_access("bv7x_oracle_premium", ADDR)

    def test_max_feature_allowed_when_max_tier(self, svc):
        max_wei = clawnch_const.to_wei(clawnch_const.MAX_THRESHOLD)
        mock_rpc = type("R", (), {})()
        mock_rpc.eth_call = _eth_call_balance(max_wei)
        with patch("clawmes.services.rpc.get_rpc_service", return_value=mock_rpc):
            assert svc.has_access("eas_attestation_write", ADDR)

    def test_one_shot_grants_access(self, svc):
        # Without one-shot, the call would be denied.
        with patch("clawmes.services.wallet.get_wallet_state") as get_state:
            get_state.return_value = WalletState.disconnected()
            svc.redeem_burn("bv7x_oracle_premium", "0xtxhash", address=ADDR)
            assert svc.has_access("bv7x_oracle_premium", ADDR)
            # One-shot consumed — second call denied
            assert not svc.has_access("bv7x_oracle_premium", ADDR)


# ──────────────────────────────────────────────────────────────────────
#  JWT
# ──────────────────────────────────────────────────────────────────────


class TestJwt:
    def test_set_and_get(self, svc):
        svc.set_jwt(ADDR, "jwt-abc", "pro")
        assert svc.get_jwt(ADDR) == "jwt-abc"

    def test_set_ignored_for_invalid_tier(self, svc):
        svc.set_jwt(ADDR, "jwt-x", "diamond")  # not in TIER_ORDER
        assert svc.get_jwt(ADDR) is None

    def test_set_ignored_for_empty_inputs(self, svc):
        svc.set_jwt("", "jwt-x", "pro")
        svc.set_jwt(ADDR, "", "pro")
        assert svc.get_jwt(ADDR) is None

    def test_jwt_expires(self, svc):
        svc.set_jwt(ADDR, "jwt-short", "pro", ttl=0.0)
        # Already expired (ttl=0). Subsequent reads see None.
        assert svc.get_jwt(ADDR) is None

    def test_expired_jwt_evicted_on_get_tier(self, svc):
        """``_jwt_tier`` evicts an expired JWT so the next read falls through."""
        svc.set_jwt(ADDR, "jwt-expired", "max", ttl=0.0)
        mock_rpc = type("R", (), {})()
        mock_rpc.eth_call = _eth_call_balance(0)
        with patch("clawmes.services.rpc.get_rpc_service", return_value=mock_rpc):
            # Should not return max (the cached JWT tier) — should fall through
            # to the balance-based check.
            assert svc.get_tier(ADDR) == "free"

    def test_get_with_no_jwt_returns_none(self, svc):
        with patch("clawmes.services.wallet.get_wallet_state") as get_state:
            get_state.return_value = WalletState.disconnected()
            assert svc.get_jwt() is None

    def test_get_jwt_via_wallet_state(self, svc):
        svc.set_jwt(ADDR, "jwt-y", "pro")
        # Patch wallet state to return our address.
        with patch("clawmes.services.wallet.get_wallet_state") as get_state:
            get_state.return_value = WalletState.for_chain(
                mode="walletconnect", address=ADDR, chain_id=8453
            )
            assert svc.get_jwt() == "jwt-y"


# ──────────────────────────────────────────────────────────────────────
#  Burn quotes
# ──────────────────────────────────────────────────────────────────────


class TestBurnQuote:
    def test_quote_for_known_feature(self, svc):
        q = svc.request_burn_quote("bv7x_oracle_premium")
        assert q["feature_id"] == "bv7x_oracle_premium"
        assert q["cost_clawnch"] == 100_000
        assert q["cost_wei"] == 100_000 * 10**18
        assert q["burn_address"] == clawnch_const.BURN_ADDRESS
        assert q["calldata"].startswith("0x")
        # transfer selector
        assert q["calldata"][2:10] == "a9059cbb"

    def test_quote_for_unknown(self, svc):
        q = svc.request_burn_quote("does_not_exist")
        assert q == {"error": "unknown_feature", "feature_id": "does_not_exist"}

    def test_quote_for_unsupported_feature(self, svc, monkeypatch):
        # Add a feature with no burn price.
        monkeypatch.setitem(
            clawnch_const.FEATURES,
            "stake_only_feature",
            {"tier": "max", "label": "Stake-only test feature"},
        )
        q = svc.request_burn_quote("stake_only_feature")
        assert q["unsupported"] is True
        assert q["required_tier"] == "max"


class TestRedeemBurn:
    def test_unknown_feature_rejected(self, svc):
        assert not svc.redeem_burn("does_not_exist", "0xhash", address=ADDR)

    def test_empty_hash_rejected(self, svc):
        assert not svc.redeem_burn("bv7x_oracle_premium", "", address=ADDR)

    def test_accepts_known_feature(self, svc):
        assert svc.redeem_burn("bv7x_oracle_premium", "0xhash", address=ADDR)


# ──────────────────────────────────────────────────────────────────────
#  Decode helpers
# ──────────────────────────────────────────────────────────────────────


class TestDecodeArray:
    def test_decode_empty(self):
        assert ClawnchPremiumService._decode_uint_array("") == []

    def test_decode_non_hex(self):
        assert ClawnchPremiumService._decode_uint_array("not hex at all") == []

    def test_decode_one_item(self):
        # offset(0x20) + length(1) + item(42)
        raw = "0x" + format(0x20, "064x") + format(1, "064x") + format(42, "064x")
        assert ClawnchPremiumService._decode_uint_array(raw) == [42]

    def test_decode_truncated(self):
        # Length says 5 items but only 2 are present — return what we can parse.
        raw = (
            "0x" + format(0x20, "064x") + format(5, "064x") + format(1, "064x") + format(2, "064x")
        )
        assert ClawnchPremiumService._decode_uint_array(raw) == [1, 2]

    def test_invalid_length_returns_empty(self):
        """When the length slot is not parseable as hex, return empty."""

        # Build a payload with an invalid length slot (use 'z' chars).
        bad = "0x" + format(0x20, "064x") + "z" * 64
        assert ClawnchPremiumService._decode_uint_array(bad) == []

    def test_invalid_item_stops_iteration(self):
        """When an item slot is malformed, return parsed items so far."""
        offset = format(0x20, "064x")
        length = format(2, "064x")
        items = format(1, "064x") + "z" * 64
        bad = "0x" + offset + length + items
        # First item parses, second doesn't — return [1].
        assert ClawnchPremiumService._decode_uint_array(bad) == [1]


class TestReadStakeMalformed:
    """If the stakes() return is shorter than expected, return (0,0)."""

    def test_short_return(self):
        mock_rpc = type("R", (), {})()
        mock_rpc.eth_call = lambda **k: "0xdead"
        assert ClawnchPremiumService._read_stake(mock_rpc, "0xabc", 0, 8453) == (0, 0)


# ──────────────────────────────────────────────────────────────────────
#  Singleton accessor
# ──────────────────────────────────────────────────────────────────────


class TestSingleton:
    def test_returns_same_instance(self):
        a = cp_mod.get_clawnch_premium_service()
        b = cp_mod.get_clawnch_premium_service()
        assert a is b
