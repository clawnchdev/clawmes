"""Tests for clawmes.lib.abi."""

from __future__ import annotations

import pytest

from clawmes.lib.abi import (
    SELECTOR_BALANCE_OF,
    SELECTOR_DECIMALS,
    SELECTOR_NAME,
    SELECTOR_SYMBOL,
    decode_uint,
    decode_uint8,
    encode_address,
    encode_balance_of,
    encode_decimals_call,
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
