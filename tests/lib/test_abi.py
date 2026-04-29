"""Tests for clawmes.lib.abi."""

from __future__ import annotations

import pytest

from clawmes.lib.abi import (
    APPROVAL_EVENT_TOPIC,
    SELECTOR_ALLOWANCE,
    SELECTOR_APPROVE,
    SELECTOR_BALANCE_OF,
    SELECTOR_DECIMALS,
    SELECTOR_NAME,
    SELECTOR_SYMBOL,
    SELECTOR_TRANSFER,
    UNLIMITED_ALLOWANCE,
    decode_uint,
    decode_uint8,
    encode_address,
    encode_allowance,
    encode_approve,
    encode_balance_of,
    encode_decimals_call,
    encode_transfer,
    encode_uint,
)


class TestSelectors:
    def test_constants_pinned(self):
        # Pinned to the exact 4-byte function selectors. If any of these
        # change we want a test failure to flag intentional vs accidental.
        assert SELECTOR_BALANCE_OF == "0x70a08231"
        assert SELECTOR_DECIMALS == "0x313ce567"
        assert SELECTOR_SYMBOL == "0x95d89b41"
        assert SELECTOR_NAME == "0x06fdde03"


class TestEncodeAddress:
    def test_lowercase(self):
        addr = "0x" + "a" * 40
        assert encode_address(addr) == "0" * 24 + "a" * 40

    def test_uppercase_normalized(self):
        addr = "0x" + "A" * 40
        assert encode_address(addr) == "0" * 24 + "a" * 40

    def test_no_prefix_accepted(self):
        addr = "a" * 40
        assert encode_address(addr) == "0" * 24 + "a" * 40

    def test_invalid_type_raises(self):
        with pytest.raises(ValueError, match="expected str"):
            encode_address(12345)  # type: ignore[arg-type]

    def test_too_short_raises(self):
        with pytest.raises(ValueError, match="not a hex address"):
            encode_address("0x" + "a" * 39)

    def test_non_hex_raises(self):
        with pytest.raises(ValueError, match="not a hex address"):
            encode_address("0x" + "g" * 40)


class TestEncodeBalanceOf:
    def test_full_calldata(self):
        addr = "0x" + "1" * 40
        data = encode_balance_of(addr)
        assert data.startswith(SELECTOR_BALANCE_OF)
        # Selector (10 chars including 0x) + 64-char address = 74 chars
        assert len(data) == 10 + 64


class TestEncodeDecimalsCall:
    def test_returns_selector(self):
        assert encode_decimals_call() == SELECTOR_DECIMALS


class TestDecodeUint:
    def test_basic_hex(self):
        assert decode_uint("0x10") == 16

    def test_no_prefix(self):
        assert decode_uint("ff") == 255

    def test_uint256_max(self):
        assert decode_uint("0x" + "f" * 64) == 2**256 - 1

    def test_empty_returns_zero(self):
        assert decode_uint("") == 0

    def test_just_prefix_returns_zero(self):
        assert decode_uint("0x") == 0


class TestDecodeUint8:
    def test_within_range(self):
        assert decode_uint8("0x12") == 18

    def test_max_value(self):
        assert decode_uint8("0xff") == 255

    def test_overflow_raises(self):
        with pytest.raises(ValueError, match="exceeds uint8 range"):
            decode_uint8("0x100")  # 256 — out of range


class TestEncodeUint:
    def test_zero(self):
        assert encode_uint(0) == "0" * 64

    def test_one(self):
        assert encode_uint(1) == "0" * 63 + "1"

    def test_uint256_max(self):
        max_v = (1 << 256) - 1
        assert encode_uint(max_v) == "f" * 64

    def test_large_value(self):
        # 1.5 * 10^18 wei (1.5 ETH)
        v = 15 * 10**17
        encoded = encode_uint(v)
        assert int(encoded, 16) == v
        assert len(encoded) == 64

    def test_negative_raises(self):
        with pytest.raises(ValueError, match="negative"):
            encode_uint(-1)

    def test_overflow_raises(self):
        with pytest.raises(ValueError, match="exceeds uint256"):
            encode_uint(1 << 256)

    def test_bool_rejected(self):
        with pytest.raises(ValueError, match="expected int"):
            encode_uint(True)  # type: ignore[arg-type]

    def test_non_int_rejected(self):
        with pytest.raises(ValueError, match="expected int"):
            encode_uint("0x1")  # type: ignore[arg-type]


class TestEncodeTransfer:
    def test_basic(self):
        # transfer(0xaaaa...aaaa, 1)
        addr = "0x" + "a" * 40
        out = encode_transfer(addr, 1)
        assert out.startswith(SELECTOR_TRANSFER)
        # selector (8 hex) + address (64 hex) + amount (64 hex) = 136 chars
        # plus '0x' prefix from selector = 138 total
        assert len(out) == 2 + 8 + 64 + 64
        # Address slot is right-padded
        assert out[10 : 10 + 64].endswith("a" * 40)
        # Amount slot encodes 1
        assert int(out[-64:], 16) == 1

    def test_zero_amount(self):
        # Encoding zero amount is legal (though usually a logic bug)
        addr = "0x" + "1" * 40
        out = encode_transfer(addr, 0)
        assert int(out[-64:], 16) == 0

    def test_invalid_address_propagates(self):
        with pytest.raises(ValueError):
            encode_transfer("not-an-address", 1)

    def test_negative_amount_propagates(self):
        with pytest.raises(ValueError):
            encode_transfer("0x" + "a" * 40, -1)


class TestApprovalConstants:
    def test_selectors_pinned(self):
        assert SELECTOR_APPROVE == "0x095ea7b3"
        assert SELECTOR_ALLOWANCE == "0xdd62ed3e"

    def test_approval_event_topic_pinned(self):
        # keccak256("Approval(address,address,uint256)") — well-known
        assert APPROVAL_EVENT_TOPIC == (
            "0x8c5be1e5ebec7d5bd14f71427d1e84f3dd0314c0f7b2291e5b200ac8c7c3b925"
        )

    def test_unlimited_constant(self):
        assert UNLIMITED_ALLOWANCE == (1 << 256) - 1


class TestEncodeApprove:
    def test_basic(self):
        out = encode_approve("0x" + "a" * 40, 1000)
        assert out.startswith(SELECTOR_APPROVE)
        assert int(out[-64:], 16) == 1000
        assert "a" * 40 in out

    def test_unlimited(self):
        out = encode_approve("0x" + "a" * 40, UNLIMITED_ALLOWANCE)
        assert int(out[-64:], 16) == UNLIMITED_ALLOWANCE
        assert out.endswith("f" * 64)

    def test_zero_for_revoke(self):
        out = encode_approve("0x" + "a" * 40, 0)
        assert out.endswith("0" * 64)


class TestEncodeAllowance:
    def test_basic(self):
        owner = "0x" + "a" * 40
        spender = "0x" + "b" * 40
        out = encode_allowance(owner, spender)
        assert out.startswith(SELECTOR_ALLOWANCE)
        # selector + 2 address slots = 8 + 128 hex chars + "0x"
        assert len(out) == 2 + 8 + 64 + 64
        assert "a" * 40 in out
        assert "b" * 40 in out
