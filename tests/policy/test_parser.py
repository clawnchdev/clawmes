"""Tests for clawmes.policy.parser."""

from __future__ import annotations

import pytest

from clawmes.policy.parser import (
    ParseError,
    _resolve_decimals,
    _resolve_tool,
    _to_wei,
    parse_policy,
)

# Successful parses ---------------------------------------------------------


class TestAmountThreshold:
    def test_approve_under(self):
        p = parse_policy("approve transfers under 0.05 ETH")
        assert p.decision == "confirm"
        assert p.applies_to_tools == ("transfer",)
        assert p.max_amount_wei == 5 * 10**16

    def test_block_over(self):
        p = parse_policy("block swaps over 1 ETH")
        assert p.decision == "block"
        assert p.applies_to_tools == ("defi_swap",)
        assert p.max_amount_wei == 10**18

    def test_confirm_over(self):
        p = parse_policy("confirm transfers over 0.1 ETH")
        assert p.decision == "confirm"
        assert p.max_amount_wei == 10**17

    def test_usdc_decimals_resolved(self):
        p = parse_policy("approve transfers under 100 USDC")
        # 100 USDC = 100 * 10**6 = 100_000_000
        assert p.max_amount_wei == 100_000_000

    def test_below_alias(self):
        p = parse_policy("approve transfers below 0.05 ETH")
        assert p.decision == "confirm"

    def test_above_alias(self):
        p = parse_policy("block swaps above 1 ETH")
        assert p.decision == "block"

    def test_less_than_alias(self):
        p = parse_policy("approve transfers less than 1 ETH")
        assert p.decision == "confirm"

    def test_more_than_alias(self):
        p = parse_policy("block transfers more than 5 ETH")
        assert p.decision == "block"

    def test_case_insensitive(self):
        p = parse_policy("APPROVE TRANSFERS UNDER 0.05 ETH")
        assert p.decision == "confirm"

    def test_extra_whitespace(self):
        p = parse_policy("  approve   transfers   under   0.05   ETH  ")
        assert p.decision == "confirm"

    def test_unknown_unit_falls_back_to_18(self):
        # MOON isn't a known unit — assume 18 decimals (with debug log)
        p = parse_policy("approve transfers under 1 MOON")
        assert p.max_amount_wei == 10**18

    def test_unknown_tool_raises(self):
        with pytest.raises(ParseError, match="unknown tool"):
            parse_policy("approve foozlebars under 1 ETH")

    def test_invalid_verb_comparator_combo_raises(self):
        # "approve over" doesn't make sense — the gate fires above
        # threshold, so "approve over" would mean "allow above" which
        # would never trigger anything.
        with pytest.raises(ParseError, match="not supported"):
            parse_policy("approve transfers over 1 ETH")

    def test_block_under_raises(self):
        with pytest.raises(ParseError, match="not supported"):
            parse_policy("block transfers under 1 ETH")

    def test_confirm_under_raises(self):
        with pytest.raises(ParseError, match="not supported"):
            parse_policy("confirm transfers under 1 ETH")


class TestRateLimit:
    def test_max_per_hour(self):
        p = parse_policy("max 20 swaps per hour")
        assert p.decision == "confirm"
        assert p.applies_to_tools == ("defi_swap",)
        assert p.max_per_hour == 20

    def test_limit_alias(self):
        p = parse_policy("limit 5 transfers per hour")
        assert p.max_per_hour == 5

    def test_cap_alias(self):
        p = parse_policy("cap 10 transfers per hour")
        assert p.max_per_hour == 10

    def test_per_hr_alias(self):
        p = parse_policy("max 30 swaps per hr")
        assert p.max_per_hour == 30

    def test_per_h_alias(self):
        p = parse_policy("max 30 swaps per h")
        assert p.max_per_hour == 30

    def test_unknown_tool_raises(self):
        with pytest.raises(ParseError, match="unknown tool"):
            parse_policy("max 5 wibblefoozes per hour")


class TestCatchAll:
    def test_block_all(self):
        p = parse_policy("block all transfers")
        assert p.decision == "block"
        assert p.applies_to_tools == ("transfer",)
        assert p.max_amount_wei is None
        assert p.max_per_hour is None

    def test_block_any(self):
        p = parse_policy("block any swaps")
        assert p.decision == "block"
        assert p.applies_to_tools == ("defi_swap",)

    def test_unknown_tool_raises(self):
        with pytest.raises(ParseError, match="unknown tool"):
            parse_policy("block all whatevers")


# Failure paths -----------------------------------------------------------


class TestUnsupported:
    def test_empty_string_raises(self):
        with pytest.raises(ParseError, match="empty"):
            parse_policy("")

    def test_whitespace_only_raises(self):
        with pytest.raises(ParseError, match="empty"):
            parse_policy("   ")

    def test_no_pattern_match_raises(self):
        with pytest.raises(ParseError, match="could not parse"):
            parse_policy("do something fancy")

    def test_unsupported_complex_phrase(self):
        with pytest.raises(ParseError, match="could not parse"):
            parse_policy("if portfolio drops 5% then alert me")


# Helpers ------------------------------------------------------------------


class TestResolveTool:
    @pytest.mark.parametrize(
        "word,expected",
        [
            ("transfer", "transfer"),
            ("transfers", "transfer"),
            ("send", "transfer"),
            ("sends", "transfer"),
            ("swap", "defi_swap"),
            ("swaps", "defi_swap"),
            ("trade", "defi_swap"),
            ("approval", "approvals"),
            ("approvals", "approvals"),
            ("stake", "defi_stake"),
            ("staking", "defi_stake"),
            ("lend", "defi_lend"),
            ("lending", "defi_lend"),
            ("borrow", "defi_lend"),
            ("bridge", "bridge"),
            ("bridges", "bridge"),
        ],
    )
    def test_aliases(self, word, expected):
        assert _resolve_tool(word) == expected

    def test_unknown_returns_none(self):
        assert _resolve_tool("unknown") is None

    def test_case_insensitive(self):
        assert _resolve_tool("TRANSFER") == "transfer"


class TestResolveDecimals:
    @pytest.mark.parametrize(
        "unit,expected",
        [
            ("ETH", 18),
            ("eth", 18),
            ("WETH", 18),
            ("USDC", 6),
            ("usdc", 6),
            ("USDT", 6),
            ("DAI", 18),
            ("BTC", 8),
            ("WBTC", 8),
            ("MATIC", 18),
        ],
    )
    def test_known_units(self, unit, expected):
        assert _resolve_decimals(unit) == expected

    def test_unknown_falls_back_to_18(self):
        assert _resolve_decimals("MOON") == 18


class TestToWei:
    def test_eth_amount(self):
        assert _to_wei("1.5", 18) == 15 * 10**17

    def test_usdc_amount(self):
        assert _to_wei("100", 6) == 100_000_000

    def test_truncates(self):
        # 0.0000001 ETH at 6 decimals → 0
        assert _to_wei("0.0000001", 6) == 0
