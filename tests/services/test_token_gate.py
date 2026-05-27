"""Tests for the token-gate service."""

from __future__ import annotations

import pytest

from clawmes.services import token_gate


@pytest.fixture(autouse=True)
def _disable_default_holder_fixture(monkeypatch):
    """Opt OUT of the conftest autouse HOLDER-tier patch for these tests.

    The conftest fixture monkeypatches ``check_tier_or_error`` and
    ``check_cap_or_error`` to always-pass for the existing test suite.
    These tests specifically want to exercise the real gate, so we
    restore the originals.
    """
    # The conftest fixture has already run by the time we get here — undo it.
    monkeypatch.undo()
    token_gate._reset_for_tests()
    yield
    token_gate._reset_for_tests()


# ── singleton + lifecycle ──────────────────────────────────────────


class TestSingleton:
    def test_same_instance(self):
        a = token_gate.get_token_gate_service()
        b = token_gate.get_token_gate_service()
        assert a is b

    def test_reset(self):
        a = token_gate.get_token_gate_service()
        token_gate._reset_for_tests()
        b = token_gate.get_token_gate_service()
        assert a is not b


class TestLifecycle:
    def test_start_stop(self):
        svc = token_gate.get_token_gate_service()
        assert svc._running is False
        svc.start()
        assert svc._running is True
        svc.stop()
        assert svc._running is False

    def test_health(self):
        svc = token_gate.get_token_gate_service()
        h = svc.health()
        assert h["id"] == "clawmes.token_gate"
        assert h["threshold_clawnch"] == token_gate.HOLDER_THRESHOLD
        assert h["status"] == "stopped"
        svc.start()
        assert svc.health()["status"] == "running"


# ── resolve_tier ────────────────────────────────────────────────────


class TestResolveTier:
    def test_no_address_is_free(self):
        svc = token_gate.get_token_gate_service()
        tier, bal = svc.resolve_tier(None)
        assert tier == token_gate.Tier.FREE
        assert bal == 0

    def test_empty_address_is_free(self):
        svc = token_gate.get_token_gate_service()
        tier, bal = svc.resolve_tier("")
        assert tier == token_gate.Tier.FREE

    def test_holder_above_threshold(self, monkeypatch):
        # Mock balance reader to return 11M $CLAWNCH (above the 10M threshold).
        monkeypatch.setattr(token_gate, "_read_clawnch_balance", lambda a: 11_000_000 * (10**18))
        svc = token_gate.get_token_gate_service()
        tier, bal = svc.resolve_tier("0x" + "a" * 40)
        assert tier == token_gate.Tier.HOLDER
        assert bal == 11_000_000 * (10**18)

    def test_free_below_threshold(self, monkeypatch):
        monkeypatch.setattr(token_gate, "_read_clawnch_balance", lambda a: 5_000_000 * (10**18))
        svc = token_gate.get_token_gate_service()
        tier, _ = svc.resolve_tier("0x" + "a" * 40)
        assert tier == token_gate.Tier.FREE

    def test_cache_hit_skips_rpc(self, monkeypatch):
        calls = {"n": 0}

        def _spy(_addr):
            calls["n"] += 1
            return 11_000_000 * (10**18)

        monkeypatch.setattr(token_gate, "_read_clawnch_balance", _spy)
        svc = token_gate.get_token_gate_service()
        svc.resolve_tier("0x" + "a" * 40)
        svc.resolve_tier("0x" + "A" * 40)  # different casing, same key
        assert calls["n"] == 1

    def test_invalidate_drops_cache(self, monkeypatch):
        balances = iter([11_000_000 * (10**18), 9_000_000 * (10**18)])

        def _read(_addr):
            return next(balances)

        monkeypatch.setattr(token_gate, "_read_clawnch_balance", _read)
        svc = token_gate.get_token_gate_service()
        tier1, _ = svc.resolve_tier("0x" + "a" * 40)
        svc.invalidate("0x" + "A" * 40)
        tier2, _ = svc.resolve_tier("0x" + "a" * 40)
        assert tier1 == token_gate.Tier.HOLDER
        assert tier2 == token_gate.Tier.FREE

    def test_invalidate_none_address_is_noop(self):
        svc = token_gate.get_token_gate_service()
        # Should not raise.
        svc.invalidate(None)
        svc.invalidate("")


# ── _read_clawnch_balance ──────────────────────────────────────────


class TestReadBalance:
    def test_rpc_error_returns_zero(self, monkeypatch):
        import clawmes.services.rpc as rpc_mod

        class _Boom:
            def eth_call(self, **kw):
                raise RuntimeError("rpc down")

        monkeypatch.setattr(rpc_mod, "get_rpc_service", lambda: _Boom())
        assert token_gate._read_clawnch_balance("0x" + "a" * 40) == 0

    def test_success(self, monkeypatch):
        import clawmes.services.rpc as rpc_mod
        from clawmes.lib.abi import encode_uint

        class _Fake:
            def eth_call(self, **kw):
                # 100 tokens at 18 decimals.
                return "0x" + encode_uint(100 * (10**18))

        monkeypatch.setattr(rpc_mod, "get_rpc_service", lambda: _Fake())
        assert token_gate._read_clawnch_balance("0x" + "a" * 40) == 100 * (10**18)

    def test_bad_response_returns_zero(self, monkeypatch):
        import clawmes.services.rpc as rpc_mod

        class _Bad:
            def eth_call(self, **kw):
                return "not-a-hex-uint"

        monkeypatch.setattr(rpc_mod, "get_rpc_service", lambda: _Bad())
        assert token_gate._read_clawnch_balance("0x" + "a" * 40) == 0


# ── check_tier_or_error ────────────────────────────────────────────


class TestCheckTierOrError:
    def test_free_tier_always_passes(self):
        assert token_gate.check_tier_or_error(token_gate.Tier.FREE, feature="x") is None

    def test_holder_required_no_wallet(self, monkeypatch):
        # No wallet → free → error with the "no wallet" hint.
        monkeypatch.setattr(token_gate, "_active_wallet_address", lambda: None)
        out = token_gate.check_tier_or_error(token_gate.Tier.HOLDER, feature="thing")
        assert "thing requires" in out
        assert "No wallet connected" in out
        assert "/connect" in out

    def test_holder_required_below_threshold(self, monkeypatch):
        monkeypatch.setattr(token_gate, "_active_wallet_address", lambda: "0x" + "a" * 40)
        monkeypatch.setattr(token_gate, "_read_clawnch_balance", lambda a: 5_000_000 * (10**18))
        out = token_gate.check_tier_or_error(token_gate.Tier.HOLDER, feature="thing")
        assert "5,000,000 $CLAWNCH" in out
        assert "5,000,000 more" in out

    def test_holder_above_threshold_passes(self, monkeypatch):
        monkeypatch.setattr(token_gate, "_active_wallet_address", lambda: "0x" + "a" * 40)
        monkeypatch.setattr(token_gate, "_read_clawnch_balance", lambda a: 50_000_000 * (10**18))
        assert token_gate.check_tier_or_error(token_gate.Tier.HOLDER, feature="thing") is None


# ── check_cap_or_error ─────────────────────────────────────────────


class TestCheckCapOrError:
    def test_unknown_command_passes(self):
        out = token_gate.check_cap_or_error("garbage", active_count=99, feature="x")
        assert out is None

    def test_below_cap_passes(self):
        out = token_gate.check_cap_or_error("dca", active_count=0, feature="schedule")
        assert out is None

    def test_at_cap_holder_passes(self, monkeypatch):
        monkeypatch.setattr(token_gate, "_active_wallet_address", lambda: "0x" + "a" * 40)
        monkeypatch.setattr(token_gate, "_read_clawnch_balance", lambda a: 11_000_000 * (10**18))
        assert token_gate.check_cap_or_error("dca", active_count=1, feature="schedule") is None

    def test_at_cap_free_blocked(self, monkeypatch):
        monkeypatch.setattr(token_gate, "_active_wallet_address", lambda: None)
        out = token_gate.check_cap_or_error("dca", active_count=1, feature="schedule")
        assert "Free tier allows 1 active schedule" in out
        assert "10,000,000+ $CLAWNCH" in out


# ── free_tier_cap ──────────────────────────────────────────────────


class TestFreeTierCap:
    def test_known(self):
        assert token_gate.free_tier_cap("dca") == 1
        assert token_gate.free_tier_cap("copy") == 1
        assert token_gate.free_tier_cap("alerts") == 3

    def test_unknown(self):
        assert token_gate.free_tier_cap("garbage") is None


# ── _active_wallet_address ─────────────────────────────────────────


class TestActiveWalletAddress:
    def test_no_wallet_returns_none(self, monkeypatch):
        import clawmes.services.wallet as wallet_mod

        class _Disc:
            connected = False
            address = ""

        monkeypatch.setattr(wallet_mod, "get_wallet_state", lambda: _Disc())
        assert token_gate._active_wallet_address() is None

    def test_connected_returns_address(self, monkeypatch):
        import clawmes.services.wallet as wallet_mod

        class _Conn:
            connected = True
            address = "0xabc"

        monkeypatch.setattr(wallet_mod, "get_wallet_state", lambda: _Conn())
        assert token_gate._active_wallet_address() == "0xabc"

    def test_exception_returns_none(self, monkeypatch):
        import clawmes.services.wallet as wallet_mod

        def _boom():
            raise RuntimeError("wallet service down")

        monkeypatch.setattr(wallet_mod, "get_wallet_state", _boom)
        assert token_gate._active_wallet_address() is None
