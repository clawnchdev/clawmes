"""Tests for /allowlist, /allow, /disallow slash commands."""

from __future__ import annotations

import pytest

from clawmes.commands import allowlist as allowlist_cmd
from clawmes.services import endpoint_allowlist as ea_mod


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(ea_mod, "_instance", None)


# --- /allowlist ---------------------------------------------------------


class TestHandleAllowlist:
    async def test_shows_defaults_when_empty(self):
        out = await allowlist_cmd.handle_allowlist("")
        assert "Defaults" in out
        assert "api.coingecko.com" in out  # one of the defaults
        assert "No blocked attempts recorded" in out

    async def test_shows_user_added(self):
        ea_mod.get_endpoint_allowlist_service().add_host("user.example.com")
        out = await allowlist_cmd.handle_allowlist("")
        assert "User-added" in out
        assert "user.example.com" in out

    async def test_shows_recent_blocks(self):
        svc = ea_mod.get_endpoint_allowlist_service()
        svc.record_block("https://evil.example.com/exfil", "evil.example.com")
        out = await allowlist_cmd.handle_allowlist("")
        assert "Recent blocked attempts" in out
        assert "evil.example.com" in out

    async def test_long_url_truncated(self):
        svc = ea_mod.get_endpoint_allowlist_service()
        long_url = "https://evil.example.com/" + "a" * 200
        svc.record_block(long_url, "evil.example.com")
        out = await allowlist_cmd.handle_allowlist("")
        # The URL should be truncated; ellipsis present.
        assert "..." in out


# --- /allow -------------------------------------------------------------


class TestHandleAllow:
    async def test_usage_message(self):
        out = await allowlist_cmd.handle_allow("")
        assert "Usage:" in out
        assert "/allow <host>" in out

    async def test_adds_host(self):
        out = await allowlist_cmd.handle_allow("new.example.com")
        assert "Added" in out
        assert "new.example.com" in out
        assert "new.example.com" in ea_mod.get_endpoint_allowlist_service().list_user_hosts()

    async def test_already_added(self):
        ea_mod.get_endpoint_allowlist_service().add_host("dup.example.com")
        out = await allowlist_cmd.handle_allow("dup.example.com")
        assert "already" in out

    async def test_invalid_host(self):
        # Whitespace-only after strip → still empty → triggers usage,
        # NOT the ValueError path. So we test with a non-string in
        # the service's add path via a stub.
        from clawmes.services.endpoint_allowlist import EndpointAllowlistService

        # Stub add_host to raise ValueError.
        def explode(self, host):
            raise ValueError("host has illegal chars")

        # Patch the singleton's class instance to force the error path.
        monkeypatched = EndpointAllowlistService()
        monkeypatched.add_host = explode.__get__(monkeypatched, EndpointAllowlistService)  # type: ignore[method-assign]
        ea_mod._instance = monkeypatched

        out = await allowlist_cmd.handle_allow("bad host")
        assert "Cannot add host" in out
        assert "illegal chars" in out


# --- /disallow ----------------------------------------------------------


class TestHandleDisallow:
    async def test_usage_message(self):
        out = await allowlist_cmd.handle_disallow("")
        assert "Usage:" in out
        assert "/disallow <host>" in out

    async def test_removes_user_host(self):
        svc = ea_mod.get_endpoint_allowlist_service()
        svc.add_host("temp.example.com")
        out = await allowlist_cmd.handle_disallow("temp.example.com")
        assert "Removed" in out
        assert "temp.example.com" not in svc.list_user_hosts()

    async def test_remove_unknown(self):
        out = await allowlist_cmd.handle_disallow("never-added.example.com")
        assert "not in the user allowlist" in out

    async def test_remove_default_not_supported(self):
        # Defaults aren't in the user set, so /disallow on them
        # returns the same "not in user allowlist" message.
        out = await allowlist_cmd.handle_disallow("api.coingecko.com")
        assert "not in the user allowlist" in out


# --- registration -------------------------------------------------------


class TestRegister:
    def test_registers_three_commands(self):
        captured = []

        class FakeCtx:
            def register_command(self, **kw):
                captured.append(kw["name"])

        allowlist_cmd.register(FakeCtx())
        assert set(captured) == {"allowlist", "allow", "disallow"}
