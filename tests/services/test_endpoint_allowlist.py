"""Tests for clawmes.services.endpoint_allowlist."""

from __future__ import annotations

import pytest

from clawmes.services import endpoint_allowlist as ea_mod
from clawmes.services.endpoint_allowlist import (
    EndpointAllowlistService,
    get_endpoint_allowlist_service,
)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(ea_mod, "_instance", None)


class TestLifecycle:
    def test_start_is_noop(self):
        EndpointAllowlistService().start()

    def test_stop_clears_state(self):
        svc = EndpointAllowlistService()
        svc.add_host("a.example.com")
        svc.record_block("https://b.example.com/x", "b.example.com")
        svc.stop()
        assert svc.list_user_hosts() == frozenset()
        assert svc.recent_blocks() == []


class TestAddRemove:
    def test_add_new(self):
        svc = EndpointAllowlistService()
        assert svc.add_host("api.example.com") is True
        assert "api.example.com" in svc.list_user_hosts()

    def test_add_idempotent(self):
        svc = EndpointAllowlistService()
        svc.add_host("api.example.com")
        assert svc.add_host("api.example.com") is False

    def test_add_normalizes_case_and_whitespace(self):
        svc = EndpointAllowlistService()
        svc.add_host("  API.Example.COM  ")
        assert svc.list_user_hosts() == frozenset({"api.example.com"})

    def test_add_empty_raises(self):
        svc = EndpointAllowlistService()
        with pytest.raises(ValueError, match="cannot be empty"):
            svc.add_host("")
        with pytest.raises(ValueError, match="cannot be empty"):
            svc.add_host("   ")

    def test_add_non_string_raises(self):
        svc = EndpointAllowlistService()
        with pytest.raises(ValueError, match="must be a string"):
            svc.add_host(123)  # type: ignore[arg-type]

    def test_remove_present(self):
        svc = EndpointAllowlistService()
        svc.add_host("api.example.com")
        assert svc.remove_host("api.example.com") is True
        assert svc.list_user_hosts() == frozenset()

    def test_remove_absent(self):
        svc = EndpointAllowlistService()
        assert svc.remove_host("api.example.com") is False

    def test_remove_normalizes(self):
        svc = EndpointAllowlistService()
        svc.add_host("api.example.com")
        assert svc.remove_host("  API.EXAMPLE.COM  ") is True


class TestIsAllowed:
    def test_added_host_is_allowed(self):
        svc = EndpointAllowlistService()
        svc.add_host("api.example.com")
        assert svc.is_allowed("api.example.com") is True
        assert svc.is_allowed("API.EXAMPLE.COM") is True

    def test_unknown_host_not_allowed(self):
        svc = EndpointAllowlistService()
        assert svc.is_allowed("evil.example.com") is False

    def test_empty_host_not_allowed(self):
        svc = EndpointAllowlistService()
        assert svc.is_allowed("") is False
        assert svc.is_allowed("   ") is False


class TestAuditRing:
    def test_record_and_retrieve(self):
        svc = EndpointAllowlistService()
        svc.record_block("https://a.example.com/x", "a.example.com")
        svc.record_block("https://b.example.com/y", "b.example.com")
        blocks = svc.recent_blocks()
        assert len(blocks) == 2
        # Newest first.
        assert blocks[0]["host"] == "b.example.com"
        assert blocks[1]["host"] == "a.example.com"

    def test_limit(self):
        svc = EndpointAllowlistService()
        for i in range(5):
            svc.record_block(f"https://h{i}.example.com", f"h{i}.example.com")
        blocks = svc.recent_blocks(limit=2)
        assert len(blocks) == 2
        # Newest first → h4, h3.
        assert blocks[0]["host"] == "h4.example.com"
        assert blocks[1]["host"] == "h3.example.com"

    def test_limit_zero_returns_empty(self):
        svc = EndpointAllowlistService()
        svc.record_block("https://x", "x")
        assert svc.recent_blocks(limit=0) == []
        assert svc.recent_blocks(limit=-3) == []

    def test_ring_evicts_old(self):
        svc = EndpointAllowlistService(ring_size=3)
        for i in range(5):
            svc.record_block(f"https://h{i}", f"h{i}")
        # Only the last 3 should survive.
        blocks = svc.recent_blocks()
        hosts = [b["host"] for b in blocks]
        assert hosts == ["h4", "h3", "h2"]

    def test_entry_fields(self):
        svc = EndpointAllowlistService()
        svc.record_block("https://api.example.com/data", "api.example.com")
        block = svc.recent_blocks()[0]
        assert set(block.keys()) == {"timestamp", "host", "url"}
        assert block["url"] == "https://api.example.com/data"
        assert block["host"] == "api.example.com"
        assert isinstance(block["timestamp"], float)


class TestSingleton:
    def test_returns_same_instance(self):
        a = get_endpoint_allowlist_service()
        b = get_endpoint_allowlist_service()
        assert a is b


# --- lib/http integration -----------------------------------------------


class TestLibHttpIntegration:
    """The lib/http _check_allowlist consults this service after defaults."""

    def test_service_host_allows_through(self):
        from clawmes.lib import http as http_mod

        svc = get_endpoint_allowlist_service()
        svc.add_host("user-allowed.example.com")
        # Should not raise.
        http_mod._check_allowlist("https://user-allowed.example.com/foo")

    def test_block_recorded_when_host_unknown(self):
        from clawmes.lib import http as http_mod
        from clawmes.lib.http import NetworkAllowlistError

        svc = get_endpoint_allowlist_service()
        assert svc.recent_blocks() == []
        with pytest.raises(NetworkAllowlistError):
            http_mod._check_allowlist("https://attacker.example.com/exfil")
        blocks = svc.recent_blocks()
        assert len(blocks) == 1
        assert blocks[0]["host"] == "attacker.example.com"

    def test_default_host_does_not_record_block(self):
        from clawmes.lib import http as http_mod

        svc = get_endpoint_allowlist_service()
        http_mod._check_allowlist("https://api.coingecko.com/v3")
        assert svc.recent_blocks() == []

    def test_extra_hosts_short_circuit_does_not_record(self):
        from clawmes.lib import http as http_mod

        svc = get_endpoint_allowlist_service()
        http_mod._check_allowlist(
            "https://short-circuit.example.com",
            extra_hosts=frozenset({"short-circuit.example.com"}),
        )
        assert svc.recent_blocks() == []

    def test_service_import_failure_falls_back(self, monkeypatch):
        """If the service import blows up (e.g. broken install), the
        existing default-only check still works — never let the
        allowlist plumbing kill a request."""
        # Simulate a broken import inside _check_allowlist.
        import builtins

        from clawmes.lib import http as http_mod
        from clawmes.lib.http import NetworkAllowlistError

        real_import = builtins.__import__

        def broken_import(name, *args, **kw):
            if name == "clawmes.services.endpoint_allowlist":
                raise RuntimeError("service module broken")
            return real_import(name, *args, **kw)

        monkeypatch.setattr(builtins, "__import__", broken_import)

        # Default-allowed host still works.
        http_mod._check_allowlist("https://api.0x.org/swap")
        # Unknown host still raises (just without recording).
        with pytest.raises(NetworkAllowlistError):
            http_mod._check_allowlist("https://evil.example.com/x")
