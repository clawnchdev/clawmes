"""Tests for clawmes.lib.http (allowlist + transport).

The transport itself is mocked — we only assert on the allowlist guard
and the request shape. Real HTTP calls would slow CI dramatically and
introduce flakiness on every cloud-provider hiccup.
"""

from __future__ import annotations

import pytest

from clawmes.lib.http import (
    DEFAULT_TIMEOUT_SECONDS,
    USER_AGENT,
    NetworkAllowlistError,
    _check_allowlist,
)


class TestAllowlist:
    def test_allows_known_host(self):
        # Should not raise
        _check_allowlist("https://api.0x.org/swap/v1/quote")
        _check_allowlist("https://api.coingecko.com/api/v3/simple/price")
        _check_allowlist("https://api.basescan.org/api")

    def test_allows_clawnch_apex_and_www(self):
        # The apex 307-redirects to the www canonical host; both must be
        # allowed since the client doesn't follow cross-host redirects.
        _check_allowlist("https://clawn.ch/api/agents/register")
        _check_allowlist("https://www.clawn.ch/api/agents/register")

    def test_allows_llm_inference_gateways(self):
        # OpenAI-compatible inference providers (services.opengateway / venice).
        _check_allowlist("https://opengateway.gitlawb.com/v1/chat/completions")
        _check_allowlist("https://api.venice.ai/api/v1/chat/completions")

    def test_rejects_unknown_host(self):
        with pytest.raises(NetworkAllowlistError, match="not on the clawmes network allowlist"):
            _check_allowlist("https://evil.example.com/whatever")

    def test_extra_hosts(self):
        # Extra hosts let users punch holes
        _check_allowlist(
            "https://my-private-rpc.example.com/foo",
            extra_hosts=frozenset({"my-private-rpc.example.com"}),
        )

    def test_extra_host_does_not_match_subdomain(self):
        # Allowlist match is exact host — no implicit suffix matching
        with pytest.raises(NetworkAllowlistError):
            _check_allowlist(
                "https://attacker.example.com/x",
                extra_hosts=frozenset({"example.com"}),
            )

    def test_url_without_scheme_or_host(self):
        with pytest.raises(NetworkAllowlistError, match="has no host"):
            _check_allowlist("not-a-url-at-all")

    def test_case_insensitive_host(self):
        # Hostnames are case-insensitive per RFC 3986
        _check_allowlist("https://API.0X.ORG/swap")

    def test_port_does_not_break_allowlist(self):
        # Port number is stripped from hostname before lookup
        _check_allowlist("https://api.0x.org:443/swap")


class TestUserAgentConstant:
    def test_format(self):
        assert USER_AGENT.startswith("clawmes/")


class TestDefaults:
    def test_timeout(self):
        assert DEFAULT_TIMEOUT_SECONDS == 30.0
