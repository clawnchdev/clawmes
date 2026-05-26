"""Tests for clawmes.lib.dexscreener."""

from __future__ import annotations

import pytest

from clawmes.lib import dexscreener


@pytest.fixture(autouse=True)
def _reset_error():
    dexscreener._set_error(None)
    yield
    dexscreener._set_error(None)


@pytest.fixture
def fake_get(monkeypatch):
    """Patch ``http_get`` to return canned responses keyed by URL substring."""
    responses: dict[str, dict] = {}
    raises: dict[str, Exception] = {}

    def _fake(url, *, params=None, timeout=None):  # noqa: ARG001
        for needle, exc in raises.items():
            if needle in url:
                raise exc
        for needle, body in responses.items():
            if needle in url:
                return body
        return None

    monkeypatch.setattr(dexscreener, "http_get", _fake)
    return type("FH", (), {"responses": responses, "raises": raises})()


# ── _looks_like_address ─────────────────────────────────────────────


class TestLooksLikeAddress:
    def test_valid_address(self):
        assert dexscreener._looks_like_address("0x" + "a" * 40)
        assert dexscreener._looks_like_address("0xA1F72459dfA10BAD200Ac160eCd78C6b77a747be")

    def test_missing_prefix(self):
        assert not dexscreener._looks_like_address("a" * 40)

    def test_wrong_length(self):
        assert not dexscreener._looks_like_address("0x" + "a" * 39)
        assert not dexscreener._looks_like_address("0x" + "a" * 41)

    def test_non_hex(self):
        assert not dexscreener._looks_like_address("0x" + "z" * 40)


# ── search ──────────────────────────────────────────────────────────


class TestSearch:
    def test_empty_query(self, fake_get):
        assert dexscreener.search("") == []
        assert dexscreener.search("   ") == []

    def test_returns_pairs(self, fake_get):
        fake_get.responses["/latest/dex/search"] = {
            "pairs": [{"chainId": "base", "baseToken": {"symbol": "X"}}, "garbage"]
        }
        out = dexscreener.search("X")
        assert len(out) == 1
        assert out[0]["chainId"] == "base"

    def test_handles_none_body(self, fake_get):
        # http_get returns None (e.g. upstream gave non-dict)
        assert dexscreener.search("X") == []

    def test_records_error_on_exception(self, fake_get):
        fake_get.raises["/latest/dex/search"] = RuntimeError("boom")
        out = dexscreener.search("X")
        assert out == []
        assert "boom" in (dexscreener.last_error() or "")

    def test_non_dict_response_returns_empty(self, fake_get, monkeypatch):
        monkeypatch.setattr(dexscreener, "http_get", lambda *a, **k: ["not", "a", "dict"])
        assert dexscreener.search("X") == []


# ── find_token ──────────────────────────────────────────────────────


class TestFindToken:
    def test_empty_input(self, fake_get):
        assert dexscreener.find_token("") is None
        assert dexscreener.find_token("   ") is None

    def test_address_path_hits_tokens_endpoint(self, fake_get):
        addr = "0x" + "a" * 40
        fake_get.responses[f"/latest/dex/tokens/{addr}"] = {
            "pairs": [
                {"chainId": "ethereum", "baseToken": {"symbol": "X"}},
                {"chainId": "base", "baseToken": {"symbol": "X", "address": addr}},
            ]
        }
        out = dexscreener.find_token(addr)
        assert out is not None
        assert out["chainId"] == "base"

    def test_address_path_no_base_pair(self, fake_get):
        addr = "0x" + "a" * 40
        fake_get.responses[f"/latest/dex/tokens/{addr}"] = {
            "pairs": [{"chainId": "ethereum", "baseToken": {"symbol": "X"}}]
        }
        assert dexscreener.find_token(addr) is None

    def test_address_path_empty_body(self, fake_get):
        addr = "0x" + "a" * 40
        # No response configured -> http_get returns None
        assert dexscreener.find_token(addr) is None

    def test_symbol_no_match(self, fake_get):
        fake_get.responses["/latest/dex/search"] = {"pairs": []}
        assert dexscreener.find_token("UNKNOWN") is None

    def test_symbol_exact_match_preferred(self, fake_get):
        addr_x = "0x" + "1" * 40
        addr_y = "0x" + "2" * 40
        fake_get.responses["/latest/dex/search"] = {
            "pairs": [
                {"chainId": "base", "baseToken": {"symbol": "WRAPPED-X", "address": addr_y}},
                {"chainId": "base", "baseToken": {"symbol": "X", "address": addr_x}},
            ]
        }
        out = dexscreener.find_token("X")
        assert out["baseToken"]["address"] == addr_x

    def test_symbol_falls_back_to_first_chain_pair(self, fake_get):
        fake_get.responses["/latest/dex/search"] = {
            "pairs": [
                {"chainId": "ethereum", "baseToken": {"symbol": "X"}},
                {"chainId": "base", "baseToken": {"symbol": "X-LP"}},
            ]
        }
        out = dexscreener.find_token("X")
        # No exact "X" baseToken on base — falls back to first base pair
        assert out["baseToken"]["symbol"] == "X-LP"

    def test_symbol_no_base_pairs(self, fake_get):
        fake_get.responses["/latest/dex/search"] = {
            "pairs": [{"chainId": "ethereum", "baseToken": {"symbol": "X"}}]
        }
        assert dexscreener.find_token("X") is None


# ── top_pairs ───────────────────────────────────────────────────────


class TestTopPairs:
    def test_zero_limit(self, fake_get):
        assert dexscreener.top_pairs(limit=0) == []

    def test_negative_limit(self, fake_get):
        assert dexscreener.top_pairs(limit=-5) == []

    def test_filters_to_chain_and_caps(self, fake_get):
        fake_get.responses["/latest/dex/search"] = {
            "pairs": [
                {"chainId": "base", "baseToken": {"symbol": "A"}},
                {"chainId": "ethereum", "baseToken": {"symbol": "B"}},
                {"chainId": "base", "baseToken": {"symbol": "C"}},
                {"chainId": "base", "baseToken": {"symbol": "D"}},
            ]
        }
        out = dexscreener.top_pairs(chain="base", limit=2)
        assert [p["baseToken"]["symbol"] for p in out] == ["A", "C"]


# ── format_pair_summary ─────────────────────────────────────────────


class TestFormatPairSummary:
    def test_minimal_pair(self):
        out = dexscreener.format_pair_summary({})
        # Symbol "?" and address "?" with no other fields
        assert "?" in out

    def test_full_pair(self):
        pair = {
            "baseToken": {"symbol": "MNEME", "address": "0x" + "1" * 40},
            "priceUsd": "0.0000132",
            "marketCap": 1_300_000,
            "volume": {"h24": 55_000},
        }
        out = dexscreener.format_pair_summary(pair)
        assert "MNEME" in out
        assert "$0.0000132" in out
        assert "$1.30M" in out
        assert "$55.0k" in out

    def test_fdv_fallback_when_no_mc(self):
        pair = {
            "baseToken": {"symbol": "X", "address": "0x" + "1" * 40},
            "fdv": 2_400_000_000,
        }
        out = dexscreener.format_pair_summary(pair)
        assert "$2.40B" in out


# ── _compact_usd / _short_addr ──────────────────────────────────────


class TestCompactUsd:
    def test_under_1k(self):
        assert dexscreener._compact_usd(42) == "$42.00"

    def test_thousand_range(self):
        assert dexscreener._compact_usd(1500) == "$1.5k"

    def test_million_range(self):
        assert dexscreener._compact_usd(2_500_000) == "$2.50M"

    def test_billion_range(self):
        assert dexscreener._compact_usd(7_300_000_000) == "$7.30B"

    def test_non_numeric_passthrough(self):
        assert dexscreener._compact_usd("garbage") == "$garbage"


class TestShortAddr:
    def test_short_input_returned_verbatim(self):
        assert dexscreener._short_addr("0xabc") == "0xabc"

    def test_truncates(self):
        addr = "0x" + "a" * 40
        out = dexscreener._short_addr(addr)
        assert out.startswith("0xaaaa")
        assert out.endswith("aaaa")
        assert "…" in out


# ── last_error / _set_error ────────────────────────────────────────


class TestErrorState:
    def test_initial_none(self):
        assert dexscreener.last_error() is None

    def test_set_and_clear(self):
        dexscreener._set_error("oops")
        assert dexscreener.last_error() == "oops"
        dexscreener._set_error(None)
        assert dexscreener.last_error() is None
