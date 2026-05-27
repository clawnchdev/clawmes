"""Tests for the /claim slash command."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from clawmes.commands import claim

# ── fakes ───────────────────────────────────────────────────────────


@dataclass
class _FakeWalletState:
    connected: bool = True
    address: str = "0x" + "1" * 40


class _FakeMode:
    """Records every send_transaction call."""

    def __init__(self, *, fail: Exception | None = None, tx_hash: str = "0xdead"):
        self.calls: list[dict[str, Any]] = []
        self.fail = fail
        self.tx_hash = tx_hash

    def send_transaction(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise self.fail
        return self.tx_hash


class _FakeWalletService:
    def __init__(self, mode):
        self.active_mode = mode


class _FakeClawnch:
    def __init__(self, *, launches=None, raises=None):
        self._launches = launches or []
        self._raises = raises

    def get_my_launches(self):
        if self._raises is not None:
            raise self._raises
        return {"launches": list(self._launches)}


@pytest.fixture
def fake_wallet(monkeypatch):
    """Patch in a connected wallet with a fake send_transaction."""
    mode = _FakeMode()
    state = _FakeWalletState()

    def _state():
        return state

    def _service():
        return _FakeWalletService(mode)

    import clawmes.services.wallet as wallet_mod

    monkeypatch.setattr(wallet_mod, "get_wallet_state", _state)
    monkeypatch.setattr(wallet_mod, "get_wallet_service", _service)
    return {"mode": mode, "state": state}


@pytest.fixture
def fake_clawnch(monkeypatch):
    """Patch the Clawnch service factory."""
    holder: dict[str, _FakeClawnch | None] = {"svc": None}

    def _factory():
        if holder["svc"] is None:
            # Default: no launches.
            holder["svc"] = _FakeClawnch()
        return holder["svc"]

    import clawmes.services.clawnch as clawnch_mod

    monkeypatch.setattr(clawnch_mod, "get_clawnch_service", _factory)
    return holder


# ── _extract_launches / _short ─────────────────────────────────────


class TestExtractLaunches:
    def test_top_level_list(self):
        assert claim._extract_launches([{"a": 1}, "junk"]) == [{"a": 1}]

    def test_dict_key(self):
        assert claim._extract_launches({"launches": [{"a": 1}]}) == [{"a": 1}]

    def test_fallback_key(self):
        assert claim._extract_launches({"tokens": [{"a": 1}]}) == [{"a": 1}]

    def test_empty(self):
        assert claim._extract_launches({}) == []
        assert claim._extract_launches("not-a-dict") == []
        assert claim._extract_launches({"launches": "not-a-list"}) == []


class TestShort:
    def test_short_unchanged(self):
        assert claim._short("0xabc") == "0xabc"

    def test_long_truncated(self):
        out = claim._short("0x" + "a" * 40)
        assert "…" in out
        assert out.startswith("0xaaaa")

    def test_non_str(self):
        assert claim._short(None) == "None"  # type: ignore[arg-type]


# ── _resolve_target ─────────────────────────────────────────────────


class TestResolveTarget:
    def test_full_address_passthrough(self, fake_clawnch):
        addr = "0x" + "A" * 40
        assert claim._resolve_target(addr) == addr.lower()

    def test_symbol_match(self, fake_clawnch):
        fake_clawnch["svc"] = _FakeClawnch(
            launches=[{"symbol": "MNEME", "contractAddress": "0x" + "b" * 40}]
        )
        assert claim._resolve_target("mneme") == "0x" + "b" * 40

    def test_symbol_no_match(self, fake_clawnch):
        fake_clawnch["svc"] = _FakeClawnch(launches=[{"symbol": "X"}])
        assert claim._resolve_target("MNEME") is None

    def test_clawnch_error_returns_none(self, fake_clawnch):
        from clawmes.services.clawnch import ClawnchError

        fake_clawnch["svc"] = _FakeClawnch(raises=ClawnchError("no_credentials", "n"))
        assert claim._resolve_target("MNEME") is None

    def test_short_non_address_falls_through(self, fake_clawnch):
        # "0xabc" looks like address-prefix but wrong length: falls to
        # symbol lookup → no match → None.
        fake_clawnch["svc"] = _FakeClawnch(launches=[])
        assert claim._resolve_target("0xabc") is None

    def test_address_alt_keys(self, fake_clawnch):
        fake_clawnch["svc"] = _FakeClawnch(
            launches=[{"ticker": "Z", "tokenAddress": "0x" + "c" * 40}]
        )
        assert claim._resolve_target("z") == "0x" + "c" * 40

    def test_address_third_alias(self, fake_clawnch):
        fake_clawnch["svc"] = _FakeClawnch(launches=[{"symbol": "Q", "address": "0x" + "d" * 40}])
        assert claim._resolve_target("Q") == "0x" + "d" * 40

    def test_address_missing_skipped(self, fake_clawnch):
        # Symbol matches but no address field → returns None.
        fake_clawnch["svc"] = _FakeClawnch(launches=[{"symbol": "R"}])
        assert claim._resolve_target("R") is None


# ── _render_preview ─────────────────────────────────────────────────


class TestRenderPreview:
    def test_clawnch_error_no_credentials(self, fake_clawnch):
        from clawmes.services.clawnch import ClawnchError

        fake_clawnch["svc"] = _FakeClawnch(raises=ClawnchError("no_credentials", "missing api key"))
        out = claim._render_preview()
        assert "Could not fetch" in out
        assert "/register_agent" in out

    def test_clawnch_error_other(self, fake_clawnch):
        from clawmes.services.clawnch import ClawnchError

        fake_clawnch["svc"] = _FakeClawnch(raises=ClawnchError("upstream", "boom"))
        out = claim._render_preview()
        assert "Could not fetch" in out
        assert "/register_agent" not in out

    def test_empty_launches(self, fake_clawnch):
        fake_clawnch["svc"] = _FakeClawnch(launches=[])
        out = claim._render_preview()
        assert "No launches found" in out

    def test_renders_launches(self, fake_clawnch):
        fake_clawnch["svc"] = _FakeClawnch(
            launches=[
                {"symbol": "A", "contractAddress": "0x" + "1" * 40},
                {"symbol": "B", "tokenAddress": "0x" + "2" * 40},
                {"address": "0x" + "3" * 40},  # missing symbol
            ]
        )
        out = claim._render_preview()
        assert "Your launches (3)" in out
        assert "A" in out and "B" in out
        assert "/claim <address>" in out
        assert "/claim all" in out


# ── _submit_claim ───────────────────────────────────────────────────


class TestSubmitClaim:
    async def test_no_wallet(self, fake_wallet):
        fake_wallet["state"].connected = False
        out = await claim._submit_claim("u", "0x" + "1" * 40)
        assert "No wallet connected" in out

    async def test_no_active_mode(self, fake_wallet, monkeypatch):
        import clawmes.services.wallet as wallet_mod

        monkeypatch.setattr(
            wallet_mod,
            "get_wallet_service",
            lambda: type("S", (), {"active_mode": None})(),
        )
        out = await claim._submit_claim("u", "0x" + "1" * 40)
        assert "No active wallet mode" in out

    async def test_tx_failure(self, fake_wallet):
        fake_wallet["mode"].fail = RuntimeError("rpc down")
        out = await claim._submit_claim("u", "0x" + "1" * 40)
        assert "Claim tx failed" in out
        assert "rpc down" in out

    async def test_tx_success(self, fake_wallet):
        fake_wallet["mode"].tx_hash = "0xfeed"
        out = await claim._submit_claim("u", "0x" + "1" * 40)
        assert "Claim submitted" in out
        assert "0xfeed" in out
        assert "basescan.org/tx/0xfeed" in out

        # Verify the calldata was built correctly: selector + padded token addr.
        call = fake_wallet["mode"].calls[0]
        assert call["to"] == claim.CLANKER_LOCKER
        assert call["value"] == 0
        assert call["chain_id"] == 8453
        # Calldata starts with the selector, then 32-byte padded token.
        data = call["data"]
        assert claim.SELECTOR_COLLECT_REWARDS in data
        assert "1" * 40 in data  # the token addr

    async def test_inline_no_wallet(self, fake_wallet):
        fake_wallet["state"].connected = False
        out = await claim._submit_claim("u", "0x" + "1" * 40, inline=True)
        assert "No wallet" in out

    async def test_inline_no_mode(self, fake_wallet, monkeypatch):
        import clawmes.services.wallet as wallet_mod

        monkeypatch.setattr(
            wallet_mod,
            "get_wallet_service",
            lambda: type("S", (), {"active_mode": None})(),
        )
        out = await claim._submit_claim("u", "0x" + "1" * 40, inline=True)
        assert "No active wallet" in out

    async def test_inline_failure(self, fake_wallet):
        fake_wallet["mode"].fail = RuntimeError("boom")
        out = await claim._submit_claim("u", "0x" + "1" * 40, inline=True)
        assert "boom" in out

    async def test_inline_success(self, fake_wallet):
        fake_wallet["mode"].tx_hash = "0xabcd1234567890"
        out = await claim._submit_claim("u", "0x" + "1" * 40, inline=True)
        assert "submitted" in out
        assert "0xabcd" in out


# ── _claim_one ──────────────────────────────────────────────────────


class TestClaimOne:
    async def test_unresolvable(self, fake_clawnch):
        fake_clawnch["svc"] = _FakeClawnch(launches=[])
        out = await claim._claim_one("u", "UNKNOWN")
        assert "Could not resolve" in out
        assert "0x… address" in out

    async def test_resolves_and_submits(self, fake_wallet, fake_clawnch):
        addr = "0x" + "5" * 40
        out = await claim._claim_one("u", addr)
        assert "Claim submitted" in out
        # Verify the resolved address (lowercased) was passed to send_transaction.
        data = fake_wallet["mode"].calls[0]["data"]
        assert "5" * 40 in data.lower()


# ── _sweep_all ──────────────────────────────────────────────────────


class TestSweepAll:
    async def test_clawnch_error(self, fake_clawnch):
        from clawmes.services.clawnch import ClawnchError

        fake_clawnch["svc"] = _FakeClawnch(raises=ClawnchError("upstream", "down"))
        out = await claim._sweep_all("u")
        assert "Could not fetch" in out

    async def test_empty(self, fake_clawnch):
        fake_clawnch["svc"] = _FakeClawnch(launches=[])
        out = await claim._sweep_all("u")
        assert "Nothing to sweep" in out

    async def test_sweeps_each_launch(self, fake_wallet, fake_clawnch):
        fake_clawnch["svc"] = _FakeClawnch(
            launches=[
                {"symbol": "A", "contractAddress": "0x" + "1" * 40},
                {"symbol": "B", "tokenAddress": "0x" + "2" * 40},
                {"symbol": "C"},  # no address → skipped
            ]
        )
        out = await claim._sweep_all("u")
        assert "Sweeping 3" in out
        assert "A" in out and "B" in out and "C" in out
        # C should be marked skipped, A and B should have hash output.
        assert "skipped" in out
        # Two send_transaction calls (A and B), not three.
        assert len(fake_wallet["mode"].calls) == 2


# ── handle_claim (top-level dispatch) ──────────────────────────────


class TestHandleClaim:
    async def test_empty_args_routes_to_preview(self, fake_clawnch):
        fake_clawnch["svc"] = _FakeClawnch(launches=[])
        out = await claim.handle_claim("")
        assert "No launches found" in out

    async def test_all_routes_to_sweep(self, fake_clawnch):
        fake_clawnch["svc"] = _FakeClawnch(launches=[])
        out = await claim.handle_claim("all")
        assert "Nothing to sweep" in out

    async def test_single_token_arg(self, fake_wallet, fake_clawnch):
        addr = "0x" + "7" * 40
        out = await claim.handle_claim(addr)
        assert "Claim submitted" in out

    async def test_record_swallows_exceptions(self, monkeypatch, fake_clawnch):
        import clawmes.services.command_history as ch

        def _boom(*a, **k):
            raise RuntimeError("hist broken")

        monkeypatch.setattr(ch, "record_command_call", _boom)
        fake_clawnch["svc"] = _FakeClawnch(launches=[])
        # Must not raise — _record swallows the error.
        out = await claim.handle_claim("")
        assert "No launches" in out


# ── register ───────────────────────────────────────────────────────


class TestRegister:
    def test_register_wires_command(self):
        registered: list[dict] = []

        class Ctx:
            def register_command(self, **kwargs):
                registered.append(kwargs)

        claim.register(Ctx())
        assert len(registered) == 1
        assert registered[0]["name"] == "claim"
