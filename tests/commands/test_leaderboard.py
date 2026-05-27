"""Tests for the /leaderboard slash command."""

from __future__ import annotations

import pytest

from clawmes.commands import leaderboard as lb


@pytest.fixture
def fake_http(monkeypatch):
    """Swap out ``http_get`` to return canned bodies / errors."""
    state: dict = {"body": None, "raises": None}

    def _fake(url, *, params=None, timeout=None):  # noqa: ARG001
        if state["raises"]:
            raise state["raises"]
        return state["body"]

    monkeypatch.setattr(lb, "http_get", _fake)
    return state


# ── _parse_args ─────────────────────────────────────────────────────


class TestParseArgs:
    def test_defaults(self):
        assert lb._parse_args("") == ("tokens", 10)

    def test_launchers_alias(self):
        assert lb._parse_args("launchers")[0] == "launchers"

    def test_deployers_alias(self):
        assert lb._parse_args("deployers")[0] == "launchers"

    def test_burners_alias(self):
        assert lb._parse_args("burners")[0] == "burners"

    def test_burn_short_alias(self):
        assert lb._parse_args("burn")[0] == "burners"

    def test_tokens_explicit(self):
        assert lb._parse_args("tokens")[0] == "tokens"

    def test_top_alias(self):
        assert lb._parse_args("top")[0] == "tokens"

    def test_limit_int(self):
        _, lim = lb._parse_args("launchers 5")
        assert lim == 5

    def test_limit_clamped_high(self):
        _, lim = lb._parse_args("99")
        assert lim == lb._MAX_LIMIT

    def test_limit_clamped_low(self):
        _, lim = lb._parse_args("0")
        assert lim == 1

    def test_unknown_token_ignored(self):
        assert lb._parse_args("garbage launchers") == ("launchers", 10)


# ── _extract_list ───────────────────────────────────────────────────


class TestExtractList:
    def test_top_level_list(self):
        assert lb._extract_list([{"a": 1}, "junk", {"b": 2}], ("x",)) == [
            {"a": 1},
            {"b": 2},
        ]

    def test_dict_with_tokens_key(self):
        body = {"tokens": [{"a": 1}], "data": [{"b": 2}]}
        assert lb._extract_list(body, ("tokens", "data")) == [{"a": 1}]

    def test_dict_fallback_key(self):
        body = {"results": [{"a": 1}]}
        assert lb._extract_list(body, ("tokens", "data", "results")) == [{"a": 1}]

    def test_missing_returns_empty(self):
        assert lb._extract_list({}, ("tokens",)) == []
        assert lb._extract_list("not-a-dict", ("tokens",)) == []
        assert lb._extract_list({"tokens": "not-a-list"}, ("tokens",)) == []


# ── _compact_usd ────────────────────────────────────────────────────


class TestCompactUsd:
    def test_under_thousand(self):
        assert lb._compact_usd(42) == "$42.00"

    def test_thousands(self):
        assert lb._compact_usd(1500) == "$1.5k"

    def test_millions(self):
        assert lb._compact_usd(2_500_000) == "$2.50M"

    def test_billions(self):
        assert lb._compact_usd(7_300_000_000) == "$7.30B"

    def test_string_passthrough(self):
        # Non-numeric input falls through to ``$<raw>``.
        assert lb._compact_usd("bad") == "$bad"


# ── _format_token ───────────────────────────────────────────────────


class TestFormatToken:
    def test_minimal(self):
        out = lb._format_token({"symbol": "T"})
        assert out == "T"

    def test_full(self):
        out = lb._format_token(
            {
                "symbol": "MNEME",
                "name": "Mneme",
                "priceUsd": "0.001",
                "marketCap": 1_000_000,
                "volume24h": 50_000,
                "priceChange24h": 12.5,
            }
        )
        assert "MNEME" in out
        assert "(Mneme)" in out
        assert "$0.001" in out
        assert "mc $1.00M" in out
        assert "vol $50.0k" in out
        assert "+12.5%" in out

    def test_negative_change(self):
        out = lb._format_token({"symbol": "X", "priceChange24h": -3.4})
        assert "-3.4%" in out
        assert "+" not in out

    def test_alt_keys(self):
        # ticker / price / fdv / volume / volume24hUsd fallbacks.
        out = lb._format_token(
            {
                "ticker": "ALT",
                "price": "0.05",
                "fdv": 500_000_000,
                "volume24hUsd": 999,
            }
        )
        assert "ALT" in out
        assert "$500.00M" in out

    def test_volume_fallback_chain(self):
        # Hits the ``volume`` (final) fallback key.
        out = lb._format_token({"symbol": "Y", "volume": 1234})
        assert "vol $1.2k" in out

    def test_name_same_as_symbol_omitted(self):
        out = lb._format_token({"symbol": "T", "name": "T"})
        assert "(T)" not in out


# ── _render_tokens (default view) ──────────────────────────────────


class TestRenderTokens:
    def test_http_error(self, fake_http):
        fake_http["raises"] = RuntimeError("network down")
        out = lb._render_tokens(5)
        assert "Could not fetch" in out
        assert "network down" in out

    def test_empty_payload(self, fake_http):
        fake_http["body"] = {"tokens": []}
        out = lb._render_tokens(5)
        assert "No tokens found" in out

    def test_renders_tokens(self, fake_http):
        fake_http["body"] = {
            "tokens": [
                {"symbol": "A", "volume24h": 1000},
                {"symbol": "B", "volume24h": 500},
            ]
        }
        out = lb._render_tokens(5)
        assert "Top 2" in out
        assert " 1." in out
        assert " 2." in out
        assert "A" in out and "B" in out


# ── _render_launchers ──────────────────────────────────────────────


class TestRenderLaunchers:
    def test_http_error(self, fake_http):
        fake_http["raises"] = RuntimeError("down")
        out = lb._render_launchers(5)
        assert "Could not fetch launches" in out

    def test_empty(self, fake_http):
        fake_http["body"] = {"launches": []}
        out = lb._render_launchers(5)
        assert "No launches found" in out

    def test_aggregates_by_agent_and_source(self, fake_http):
        fake_http["body"] = {
            "launches": [
                {"agentName": "alpha", "source": "clawmes"},
                {"agentName": "alpha", "source": "clawmes"},
                {"agentName": "alpha", "source": "4claw"},
                {"agentName": "beta", "source": "clawmes"},
                # missing both keys → "unknown (unknown)"
                {},
            ]
        }
        out = lb._render_launchers(5)
        assert "alpha (clawmes)" in out
        assert "alpha (4claw)" in out
        assert "beta (clawmes)" in out
        # Counts encoded in the table
        assert "   2" in out  # alpha/clawmes has 2

    def test_agent_alias_field(self, fake_http):
        # Some payloads use ``agent`` instead of ``agentName``.
        fake_http["body"] = {"launches": [{"agent": "gamma", "source": "clawmes"}]}
        out = lb._render_launchers(5)
        assert "gamma (clawmes)" in out


# ── _render_burners (stub) ─────────────────────────────────────────


class TestRenderBurners:
    def test_returns_coming_soon(self):
        out = lb._render_burners(5)
        assert "coming soon" in out.lower()
        assert "0xa1F72459dfA10BAD200Ac160eCd78C6b77a747be" in out
        assert "0x000000000000000000000000000000000000dEaD" in out


# ── handle_leaderboard (top-level dispatch) ────────────────────────


class TestHandleLeaderboard:
    async def test_default_routes_to_tokens(self, fake_http):
        fake_http["body"] = {"tokens": [{"symbol": "A"}]}
        out = await lb.handle_leaderboard("")
        assert "Top 1" in out

    async def test_launchers_route(self, fake_http):
        fake_http["body"] = {"launches": [{"agentName": "a", "source": "s"}]}
        out = await lb.handle_leaderboard("launchers")
        assert "a (s)" in out

    async def test_burners_route(self):
        out = await lb.handle_leaderboard("burners")
        assert "coming soon" in out.lower()

    async def test_record_failure_swallowed(self, monkeypatch, fake_http):
        """Recording into command_history must never break the command."""

        def _boom(*args, **kwargs):
            raise RuntimeError("history broken")

        # The lazy import inside _record() resolves the real function;
        # monkeypatch it where it lives.
        import clawmes.services.command_history as ch

        monkeypatch.setattr(ch, "record_command_call", _boom)
        fake_http["body"] = {"tokens": []}
        # Should still complete without raising.
        out = await lb.handle_leaderboard("")
        assert "No tokens" in out


# ── register ───────────────────────────────────────────────────────


class TestRegister:
    def test_register_wires_command(self):
        registered: list[dict] = []

        class Ctx:
            def register_command(self, **kwargs):
                registered.append(kwargs)

        lb.register(Ctx())
        assert len(registered) == 1
        assert registered[0]["name"] == "leaderboard"
        assert callable(registered[0]["handler"])
