"""Tests for clawmes.services.aave."""

from __future__ import annotations

import pytest

from clawmes.services.aave import (
    SELECTOR_BORROW,
    SELECTOR_REPAY,
    SELECTOR_SUPPLY,
    SELECTOR_USER_ACCOUNT_DATA,
    SELECTOR_WITHDRAW,
    AaveError,
    decode_user_account_data,
    encode_borrow,
    encode_get_user_account_data,
    encode_repay,
    encode_supply,
    encode_withdraw,
    pool_address,
    supports_chain,
)


class TestPoolAddress:
    def test_known_chains(self):
        assert pool_address(1).lower() == "0x87870bca3f3fd6335c3f4ce8392d69350b4fa4e2"
        assert pool_address(8453).lower() == "0xa238dd80c259a72e81d7e4664a9801593f98d1c5"
        # Optimism + Arbitrum + Polygon share the same address
        assert pool_address(42161) == pool_address(10) == pool_address(137)

    def test_unsupported_chain(self):
        with pytest.raises(AaveError):
            pool_address(56)  # BSC

    def test_supports_chain(self):
        for cid in (1, 8453, 42161, 10, 137):
            assert supports_chain(cid)
        assert not supports_chain(56)


class TestEncoders:
    def test_supply(self):
        out = encode_supply(
            asset="0x" + "a" * 40,
            amount=10**18,
            on_behalf_of="0x" + "b" * 40,
        )
        assert out.startswith(SELECTOR_SUPPLY)
        # selector + 4 args × 32 bytes = 8 + 256 hex chars + "0x"
        assert len(out) == 2 + 8 + 64 * 4
        # Asset + on_behalf_of addresses present
        assert "a" * 40 in out
        assert "b" * 40 in out
        # Referral code (last slot) is zero
        assert out.endswith("0" * 64)

    def test_withdraw(self):
        out = encode_withdraw(asset="0x" + "a" * 40, amount=10**18, to="0x" + "c" * 40)
        assert out.startswith(SELECTOR_WITHDRAW)
        assert len(out) == 2 + 8 + 64 * 3
        assert "a" * 40 in out
        assert "c" * 40 in out

    def test_borrow(self):
        out = encode_borrow(asset="0x" + "a" * 40, amount=10**18, on_behalf_of="0x" + "d" * 40)
        assert out.startswith(SELECTOR_BORROW)
        # Variable rate mode (2) is in the middle slot
        assert "0" * 63 + "2" in out

    def test_repay(self):
        out = encode_repay(asset="0x" + "a" * 40, amount=10**18, on_behalf_of="0x" + "d" * 40)
        assert out.startswith(SELECTOR_REPAY)

    def test_get_user_account_data(self):
        out = encode_get_user_account_data("0x" + "a" * 40)
        assert out.startswith(SELECTOR_USER_ACCOUNT_DATA)
        assert "a" * 40 in out


class TestDecodeUserAccountData:
    def test_basic(self):
        # Build a deterministic 6-tuple response: 6 × uint256 = 192 bytes
        chunks = [
            10**8 * 1500,  # total_collateral_base = $1500
            10**8 * 500,  # total_debt_base = $500
            10**8 * 1000,  # available_borrows_base = $1000
            8500,  # liquidation threshold = 85%
            7500,  # ltv = 75%
            int(2.5 * 10**18),  # health factor = 2.5 (in ray)
        ]
        body = "".join(format(c, "064x") for c in chunks)
        result = decode_user_account_data("0x" + body)
        assert result["total_collateral_base"] == chunks[0]
        assert result["total_debt_base"] == chunks[1]
        assert result["available_borrows_base"] == chunks[2]
        assert result["current_liquidation_threshold"] == chunks[3]
        assert result["ltv"] == chunks[4]
        assert result["health_factor"] == chunks[5]

    def test_truncated_returns_zeros(self):
        # Less than 6 × 64 hex chars
        result = decode_user_account_data("0x1234")
        assert result["total_collateral_base"] == 0
        assert result["total_debt_base"] == 0
        assert result["health_factor"] == 0

    def test_handles_no_prefix(self):
        chunks = [0, 0, 0, 0, 0, 10**18]  # health = 1.0
        body = "".join(format(c, "064x") for c in chunks)
        result = decode_user_account_data(body)
        assert result["health_factor"] == 10**18
