"""Tests for the /trending slash command."""

from __future__ import annotations

import pytest

from clawmes.commands import trending as trending_mod
from clawmes.lib import dexscreener


@pytest.fixture
def fake_clawnch_get(monkeypatch):
    state: dict = {"body": None, "raises": None}

    def _fake(url, *, params=None, timeout=None):  # noqa: ARG001
        if state["raises"]:
            raise state["raises"]
        return state["body"]

    monkeypatch.setattr(trending_mod, "http_get", _fake)
    return state


@pytest.fixture
def fake_top_pairs(monkeypatch):
    state: dict = {"pairs": [], "error": None}

    def _fake(chain="base", limit=10):  # noqa: ARG001
        return state["pairs"]

    def _fake_err():
        return state["error"]

    monkeypatch.setattr(dexscreener, "top_pairs", _fake)
    monkeypatch.setattr(dexscreener, "last_error", _fake_err)
    return state


# ── arg parsing ─────────────────────────────────────────────────────


class TestParseArgs:
    def test_defaults(self):
        assert trending_mod._parse_args("") == ("all", 10)

    def test_clawnch_flag(self):
        assert trending_mod._parse_args("--clawnch")[0] == "clawnch"

    def test_all_flag(self):
        assert trending_mod._parse_args("--clawnch --all")[0] == "all"

    def test_limit_parses(self):
        assert trending_mod._parse_args("5")[1] == 5

    def test_limit_clamped_high(self):
        _, lim = trending_mod._parse_args("999")
        assert lim == trending_mod._MAX_LIMIT

    def test_limit_clamped_low(self):
        _, lim = trending_mod._parse_args("0")
        assert lim == 1

    def test_unknown_token_ignored(self):
        assert trending_mod._parse_args("garbage --clawnch") == ("clawnch", 10)


# ── --all path (DexScreener) ────────────────────────────────────────


class TestRenderAll:
    async def test_no_pairs_no_error(self, fake_top_pairs):
        fake_top_pairs["pairs"] = []
        fake_top_pairs["error"] = None
        out = await trending_mod.handle_trending("")
        assert "No Base trending pairs" in out
        assert "(" not in out  # No error suffix in parens

    async def test_no_pairs_with_error(self, fake_top_pairs):
        fake_top_pairs["pairs"] = []
        fake_top_pairs["error"] = "upstream timeout"
        out = await trending_mod.handle_trending("")
        assert "upstream timeout" in out

    async def test_renders_pairs(self, fake_top_pairs):
        fake_top_pairs["pairs"] = [
            {
                "baseToken": {"symbol": "MNEME", "address": "0x" + "1" * 40},
                "priceUsd": "0.00001",
                "marketCap": 1_000_000,
                "volume": {"h24": 50_000},
            }
        ]
        out = await trending_mod.handle_trending("")
        assert "MNEME" in out
        assert "DexScreener" in out
        assert "--clawnch" in out


# ── --clawnch path (Clawnch API) ───────────────────────────────────


class TestRenderClawnch:
    async def test_http_error(self, fake_clawnch_get):
        fake_clawnch_get["raises"] = RuntimeError("connection refused")
        out = await trending_mod.handle_trending("--clawnch")
        assert "Could not fetch Clawnch trending" in out
        assert "connection refused" in out

    async def test_empty_response(self, fake_clawnch_get):
        fake_clawnch_get["body"] = {"tokens": []}
        out = await trending_mod.handle_trending("--clawnch")
        assert "No Clawnch tokens" in out
        assert "--all" in out

    async def test_renders_tokens(self, fake_clawnch_get):
        fake_clawnch_get["body"] = {
            "tokens": [
                {
                    "symbol": "MNEME",
                    "name": "MNEME",
                    "contractAddress": "0x" + "1" * 40,
                    "priceUsd": "0.00001",
                    "marketCap": 1_300_000,
                    "volume24h": 55_000,
                }
            ]
        }
        out = await trending_mod.handle_trending("--clawnch")
        assert "MNEME" in out
        assert "Top 1 Clawnch tokens" in out

    async def test_list_response_shape(self, fake_clawnch_get):
        # Some backends return the array directly
        fake_clawnch_get["body"] = [{"symbol": "A", "address": "0x" + "1" * 40, "name": "Alpha"}]
        out = await trending_mod.handle_trending("--clawnch")
        assert "Alpha" in out


# ── shape extractors / formatters ───────────────────────────────────


class TestExtractClawnchTokens:
    def test_none(self):
        assert trending_mod._extract_clawnch_tokens(None) == []

    def test_list(self):
        assert trending_mod._extract_clawnch_tokens([{"a": 1}, "junk"]) == [{"a": 1}]

    def test_dict_data_key(self):
        assert trending_mod._extract_clawnch_tokens({"data": [{"a": 1}]}) == [{"a": 1}]

    def test_dict_results_key(self):
        assert trending_mod._extract_clawnch_tokens({"results": [{"a": 1}]}) == [{"a": 1}]

    def test_dict_no_known_key(self):
        assert trending_mod._extract_clawnch_tokens({"weird": [1]}) == []


class TestFormatClawnchToken:
    def test_bare(self):
        out = trending_mod._format_clawnch_token({})
        assert "?" in out

    def test_skips_redundant_name(self):
        out = trending_mod._format_clawnch_token({"symbol": "X", "name": "X"})
        # "X" appears once (no "(X)" parenthetical)
        assert out.count("X") == 1

    def test_full(self):
        out = trending_mod._format_clawnch_token(
            {
                "symbol": "X",
                "name": "Xtra",
                "price": "0.001",
                "marketCap": 1_200_000,
                "volume": 3_400,
                "address": "0x" + "1" * 40,
            }
        )
        assert "Xtra" in out
        assert "$0.001" in out
        assert "$1.20M" in out
        assert "$3.4k" in out


# ── _compact / _short ───────────────────────────────────────────────


class TestCompact:
    @pytest.mark.parametrize(
        ("inp", "want"),
        [
            (42, "$42.00"),
            (1500, "$1.5k"),
            (2_500_000, "$2.50M"),
            (7_300_000_000, "$7.30B"),
            ("bad", "$bad"),
        ],
    )
    def test_compact(self, inp, want):
        assert trending_mod._compact(inp) == want


class TestShort:
    def test_short_input(self):
        assert trending_mod._short("0xabc") == "0xabc"

    def test_truncates(self):
        out = trending_mod._short("0x" + "a" * 40)
        assert "…" in out


# ── command_history best-effort ────────────────────────────────────


class TestRecordingBestEffort:
    async def test_recording_failure_does_not_break(self, monkeypatch, fake_top_pairs):
        from clawmes.services import command_history as ch_mod

        def _boom(*a, **kw):
            raise RuntimeError("history broken")

        monkeypatch.setattr(ch_mod, "record_command_call", _boom)
        out = await trending_mod.handle_trending("")
        assert isinstance(out, str)


class TestRegister:
    def test_registers(self):
        captured = []

        class FakeCtx:
            def register_command(self, **kw):
                captured.append(kw["name"])

        trending_mod.register(FakeCtx())
        assert captured == ["trending"]
