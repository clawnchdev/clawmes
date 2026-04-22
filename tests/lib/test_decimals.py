"""Tests for clawmes.lib.decimals."""

from __future__ import annotations

from decimal import Decimal

import pytest

from clawmes.lib.decimals import format_human, from_base_units, to_base_units


class TestToBaseUnits:
    def test_simple_eth(self):
        assert to_base_units("1.5", 18) == 1_500_000_000_000_000_000

    def test_usdc(self):
        assert to_base_units("0.5", 6) == 500_000

    def test_zero(self):
        assert to_base_units("0", 18) == 0

    def test_int_input(self):
        assert to_base_units(2, 6) == 2_000_000

    def test_decimal_input(self):
        assert to_base_units(Decimal("3.14"), 6) == 3_140_000

    def test_float_input(self):
        # Floats are accepted but convert through str() to avoid IEEE-754 noise
        assert to_base_units(0.5, 6) == 500_000

    def test_truncation_below_decimals(self):
        # 0.0000001 with 6 decimals → 0.0 (truncates, never rounds up)
        assert to_base_units("0.0000001", 6) == 0
        # 0.999999999 with 6 decimals → 999_999 (truncated, not rounded to 1_000_000)
        assert to_base_units("0.9999999", 6) == 999_999

    def test_negative_rejected(self):
        with pytest.raises(ValueError, match="Negative amount"):
            to_base_units("-1", 18)

    def test_decimals_zero(self):
        assert to_base_units("1.5", 0) == 1  # truncates the .5

    def test_very_large(self):
        assert to_base_units("1000000", 18) == 10**24


class TestFromBaseUnits:
    def test_simple_eth(self):
        assert from_base_units(1_500_000_000_000_000_000, 18) == "1.5"

    def test_zero(self):
        assert from_base_units(0, 18) == "0"

    def test_strips_trailing_zeros(self):
        assert from_base_units(1_000_000, 6) == "1"
        assert from_base_units(1_500_000, 6) == "1.5"

    def test_full_precision_default(self):
        # 999_999 with 6 decimals = 0.999999 (no precision arg means full)
        assert from_base_units(999_999, 6) == "0.999999"

    def test_with_precision(self):
        # 999_999 with 6 decimals and precision 3 → 0.999 (truncates)
        assert from_base_units(999_999, 6, precision=3) == "0.999"

    def test_precision_zero(self):
        assert from_base_units(1_500_000, 6, precision=0) == "1"  # truncates

    def test_negative_precision_raises(self):
        with pytest.raises(ValueError, match="precision must be >= 0"):
            from_base_units(1, 18, precision=-1)

    def test_string_input(self):
        # int(...) coercion path
        assert from_base_units("500000", 6) == "0.5"


class TestFormatHuman:
    def test_with_symbol(self):
        assert format_human(1_500_000, 6, "USDC") == "1.5 USDC"

    def test_without_symbol(self):
        assert format_human(1_500_000, 6) == "1.5"

    def test_default_precision_is_6(self):
        # 1234567890 wei, 18 decimals = 0.00000000123456789 → precision=6 → 0
        # Hmm, 1.23..e-9 truncated to 6 decimals is 0
        assert format_human(1_234_567_890, 18) == "0"

    def test_zero(self):
        assert format_human(0, 18, "ETH") == "0 ETH"


class TestRoundtrip:
    @pytest.mark.parametrize(
        "amount,decimals",
        [
            ("1.5", 18),
            ("0.5", 6),
            ("0.000001", 6),  # right at the resolution boundary
            ("1000000", 18),
        ],
    )
    def test_roundtrip(self, amount, decimals):
        wei = to_base_units(amount, decimals)
        back = from_base_units(wei, decimals)
        # Both should normalize to the same form
        assert Decimal(back) == Decimal(amount)
