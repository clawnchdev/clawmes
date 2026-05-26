"""Tests for clawmes.lib.x402."""

from __future__ import annotations

from clawmes.lib.x402 import (
    X402Challenge,
    format_challenge,
    is_x402_response,
    parse_challenge,
)


class TestIsX402Response:
    def test_status_402(self):
        assert is_x402_response({}, status_code=402)

    def test_accepts_in_body(self):
        assert is_x402_response({"accepts": [{"network": "base"}]})

    def test_not_a_dict(self):
        assert not is_x402_response("string", status_code=200)
        assert not is_x402_response(None, status_code=200)

    def test_no_accepts(self):
        assert not is_x402_response({"foo": "bar"}, status_code=200)

    def test_empty_accepts(self):
        assert not is_x402_response({"accepts": []}, status_code=200)


class TestParseChallenge:
    def test_filters_non_dict_options(self):
        body = {"accepts": [{"network": "base", "asset": "USDC"}, "garbage", None]}
        challenge = parse_challenge(body)
        assert len(challenge.accepts) == 1

    def test_preserves_raw(self):
        body = {"accepts": [], "extra": "field"}
        challenge = parse_challenge(body)
        assert challenge.raw is body


class TestPrimary:
    def test_no_options(self):
        challenge = X402Challenge(accepts=(), raw={})
        assert challenge.primary() is None

    def test_prefers_base_usdc(self):
        challenge = X402Challenge(
            accepts=(
                {"network": "ethereum", "asset": "ETH"},
                {"network": "base", "asset": "USDC", "amount": "1"},
            ),
            raw={},
        )
        primary = challenge.primary()
        assert primary["asset"] == "USDC"

    def test_falls_back_to_first(self):
        challenge = X402Challenge(
            accepts=({"network": "ethereum", "asset": "ETH"},),
            raw={},
        )
        primary = challenge.primary()
        assert primary["asset"] == "ETH"


class TestFormatChallenge:
    def test_no_options(self):
        challenge = X402Challenge(accepts=(), raw={})
        out = format_challenge(challenge)
        assert "no acceptable payment options" in out

    def test_basic(self):
        challenge = X402Challenge(
            accepts=(
                {
                    "network": "base",
                    "asset": "USDC",
                    "maxAmountRequired": "1.50",
                    "payTo": "0x" + "a" * 40,
                },
            ),
            raw={},
        )
        out = format_challenge(challenge)
        assert "1.50" in out
        assert "USDC" in out
        assert "base" in out
        assert "0xaaaaaaaa" in out

    def test_with_description_and_alternates(self):
        challenge = X402Challenge(
            accepts=(
                {
                    "network": "base",
                    "asset": "USDC",
                    "amount": "1.00",
                    "recipient": "0xabc",
                    "description": "Premium oracle access",
                },
                {"network": "ethereum", "asset": "ETH", "amount": "0.001"},
            ),
            raw={},
        )
        out = format_challenge(challenge)
        assert "Premium oracle access" in out
        assert "1 alternate" in out

    def test_missing_fields_render_question_marks(self):
        # A dict with only a network key still counts as a payment option;
        # missing fields show up as "?" rather than crashing.
        challenge = X402Challenge(accepts=({"network": "base"},), raw={})
        out = format_challenge(challenge)
        assert "?" in out
        assert "base" in out

    def test_falsy_primary_renders_no_options(self):
        # Empty dict is falsy → primary() returns it, the formatter
        # treats it as "no options."
        challenge = X402Challenge(accepts=({},), raw={})
        out = format_challenge(challenge)
        assert "no acceptable payment options" in out
