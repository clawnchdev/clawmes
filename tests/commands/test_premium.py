"""Tests for clawmes.commands.premium — /premium /verify /burn_and_call."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from clawmes.commands import premium as cmd
from clawmes.lib import clawnch as clawnch_const
from clawmes.services import clawnch_premium as cp_mod
from clawmes.wallet.state import WalletState

ADDR = "0x" + "a" * 40


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(cp_mod, "_instance", None)


# ──────────────────────────────────────────────────────────────────────
#  /premium  (status)
# ──────────────────────────────────────────────────────────────────────


class TestPremiumStatus:
    async def test_free_status_lists_upgrade_paths(self):
        with patch("clawmes.services.wallet.get_wallet_state") as get_state:
            get_state.return_value = WalletState.disconnected()
            out = await cmd.handle_premium("")
        assert "FREE" in out
        assert "Upgrade paths" in out
        assert "https://clawn.ch/stake" in out
        assert "/premium quote" in out

    async def test_pro_status_mentions_max_upgrade(self):
        max_wei = clawnch_const.to_wei(clawnch_const.PRO_THRESHOLD)
        mock_rpc = type("R", (), {})()
        mock_rpc.eth_call = lambda **k: "0x" + format(max_wei, "064x")
        with (
            patch("clawmes.services.wallet.get_wallet_state") as get_state,
            patch("clawmes.services.rpc.get_rpc_service", return_value=mock_rpc),
        ):
            get_state.return_value = WalletState.for_chain(
                mode="walletconnect", address=ADDR, chain_id=8453
            )
            out = await cmd.handle_premium("")
        assert "PRO" in out
        # Mentions upgrade to Max somewhere.
        assert "Max" in out or "MAX" in out

    async def test_max_status_lists_max_benefits(self):
        max_wei = clawnch_const.to_wei(clawnch_const.MAX_THRESHOLD)
        mock_rpc = type("R", (), {})()
        mock_rpc.eth_call = lambda **k: "0x" + format(max_wei, "064x")
        with (
            patch("clawmes.services.wallet.get_wallet_state") as get_state,
            patch("clawmes.services.rpc.get_rpc_service", return_value=mock_rpc),
        ):
            get_state.return_value = WalletState.for_chain(
                mode="walletconnect", address=ADDR, chain_id=8453
            )
            out = await cmd.handle_premium("")
        assert "MAX" in out
        assert "EAS attestations" in out


class TestPremiumFeatures:
    async def test_lists_all(self):
        out = await cmd.handle_premium("features")
        for fid in clawnch_const.FEATURES:
            assert fid in out

    async def test_empty_catalog(self, monkeypatch):
        monkeypatch.setattr(clawnch_const, "FEATURES", {})
        out = await cmd.handle_premium("features")
        assert "No premium features" in out


class TestPremiumQuote:
    async def test_missing_feature_id(self):
        out = await cmd.handle_premium("quote")
        assert "Usage: /premium quote" in out

    async def test_unknown_feature(self):
        out = await cmd.handle_premium("quote does_not_exist")
        assert "error" in out.lower() or "unknown" in out.lower()

    async def test_unsupported_feature(self, monkeypatch):
        monkeypatch.setitem(
            clawnch_const.FEATURES,
            "stake_only_x",
            {"tier": "max", "label": "Stake-only feature"},
        )
        out = await cmd.handle_premium("quote stake_only_x")
        assert "no published burn price" in out

    async def test_quote_renders(self):
        out = await cmd.handle_premium("quote bv7x_oracle_premium")
        assert "Burn quote" in out
        assert "100,000 CLAWNCH" in out
        assert "/burn_and_call" in out
        assert "0xa9059cbb" in out  # transfer selector


class TestPremiumUnknownArg:
    async def test_unknown(self):
        out = await cmd.handle_premium("explode")
        assert "Unknown" in out


# ──────────────────────────────────────────────────────────────────────
#  /verify
# ──────────────────────────────────────────────────────────────────────


class TestVerify:
    async def test_no_args_shows_usage(self):
        out = await cmd.handle_verify("")
        assert "Usage" in out
        assert "/verify" in out

    async def test_missing_colon(self):
        out = await cmd.handle_verify("just-some-text")
        assert ":" in out  # error mentions the separator

    async def test_empty_after_colon(self):
        out = await cmd.handle_verify(":")
        assert "address" in out.lower() or "signature" in out.lower()

    async def test_empty_address(self):
        out = await cmd.handle_verify(":somesig")
        assert "Both" in out

    async def test_verifier_error_propagates(self, monkeypatch):
        def _post(url, json, timeout):  # noqa: A002
            return {"error": "bad signature"}

        monkeypatch.setattr("clawmes.lib.http.http_post", _post)
        out = await cmd.handle_verify(f"{ADDR}:0xsig")
        assert "rejected" in out.lower()

    async def test_missing_jwt_in_response(self, monkeypatch):
        def _post(url, json, timeout):  # noqa: A002
            return {"tier": "pro"}  # no jwt

        monkeypatch.setattr("clawmes.lib.http.http_post", _post)
        out = await cmd.handle_verify(f"{ADDR}:0xsig")
        assert "missing" in out.lower()

    async def test_invalid_tier_in_response(self, monkeypatch):
        def _post(url, json, timeout):  # noqa: A002
            return {"jwt": "tok", "tier": "platinum"}

        monkeypatch.setattr("clawmes.lib.http.http_post", _post)
        out = await cmd.handle_verify(f"{ADDR}:0xsig")
        assert "missing" in out.lower()

    async def test_successful_verify_caches_jwt(self, monkeypatch):
        def _post(url, json, timeout):  # noqa: A002
            return {"jwt": "abc123", "tier": "max", "ttl_seconds": 3600}

        monkeypatch.setattr("clawmes.lib.http.http_post", _post)
        out = await cmd.handle_verify(f"{ADDR}:0xsig")
        assert "Verified" in out
        assert "MAX" in out
        # Verify the JWT actually cached.
        svc = cp_mod.get_clawnch_premium_service()
        assert svc.get_jwt(ADDR) == "abc123"

    async def test_http_exception_handled(self, monkeypatch):
        def _post(url, json, timeout):  # noqa: A002
            raise RuntimeError("connection refused")

        monkeypatch.setattr("clawmes.lib.http.http_post", _post)
        out = await cmd.handle_verify(f"{ADDR}:0xsig")
        assert "rejected" in out.lower() or "transport" in out.lower()


# ──────────────────────────────────────────────────────────────────────
#  /burn_and_call
# ──────────────────────────────────────────────────────────────────────


class TestBurnAndCall:
    async def test_no_args_shows_usage(self):
        out = await cmd.handle_burn_and_call("")
        assert "Usage" in out

    async def test_missing_tx_hash(self):
        out = await cmd.handle_burn_and_call("bv7x_oracle_premium")
        assert "Usage" in out

    async def test_unknown_feature(self):
        out = await cmd.handle_burn_and_call("does_not_exist 0xhash")
        assert "Unknown feature_id" in out

    async def test_verifier_error(self, monkeypatch):
        def _post(url, json, timeout):  # noqa: A002
            return {"error": "burn too old"}

        monkeypatch.setattr("clawmes.lib.http.http_post", _post)
        out = await cmd.handle_burn_and_call("bv7x_oracle_premium 0xhash")
        assert "rejected" in out.lower()

    async def test_successful_redeem(self, monkeypatch):
        def _post(url, json, timeout):  # noqa: A002
            return {"ok": True}

        monkeypatch.setattr("clawmes.lib.http.http_post", _post)
        with patch("clawmes.services.wallet.get_wallet_state") as get_state:
            get_state.return_value = WalletState.disconnected()
            out = await cmd.handle_burn_and_call("bv7x_oracle_premium 0xhash")
        assert "One-shot grant" in out
        # Verify the one-shot is recorded.
        svc = cp_mod.get_clawnch_premium_service()
        with patch("clawmes.services.wallet.get_wallet_state") as get_state:
            get_state.return_value = WalletState.disconnected()
            assert svc.has_access("bv7x_oracle_premium")

    async def test_redeem_failure_when_record_returns_false(self, monkeypatch):
        def _post(url, json, timeout):  # noqa: A002
            return {"ok": True}

        monkeypatch.setattr("clawmes.lib.http.http_post", _post)

        class _FakeSvc:
            def redeem_burn(self, feature_id, tx_hash, address=None):
                return False

        monkeypatch.setattr(
            "clawmes.services.clawnch_premium.get_clawnch_premium_service",
            lambda: _FakeSvc(),
        )
        out = await cmd.handle_burn_and_call("bv7x_oracle_premium 0xhash")
        assert "Could not record" in out


# ──────────────────────────────────────────────────────────────────────
#  Registration
# ──────────────────────────────────────────────────────────────────────


class TestRegister:
    def test_registers_three_commands(self):
        captured = []

        class FakeCtx:
            def register_command(self, **kw):
                captured.append(kw["name"])

        cmd.register(FakeCtx())
        assert set(captured) == {"premium", "verify", "burn_and_call"}


# ──────────────────────────────────────────────────────────────────────
#  Command-history recording
# ──────────────────────────────────────────────────────────────────────


class TestCommandHistoryRecording:
    """The commands opt into command_history; ensure recording is best-effort."""

    async def test_recording_swallows_failure(self, monkeypatch):
        from clawmes.services import command_history as ch_mod

        def _boom(*a, **kw):
            raise RuntimeError("ring blew up")

        monkeypatch.setattr(ch_mod, "record_command_call", _boom)
        with patch("clawmes.services.wallet.get_wallet_state") as get_state:
            get_state.return_value = WalletState.disconnected()
            out = await cmd.handle_premium("")
        assert isinstance(out, str)
