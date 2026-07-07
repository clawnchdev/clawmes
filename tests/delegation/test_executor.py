"""Tests for clawmes.delegation.executor."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from clawmes.delegation import executor as ex
from clawmes.delegation.executor import (
    ExtractorContext,
    _extract_approvals,
    _extract_nft,
    _extract_transfer,
    find_matching_delegation,
    register_extractor,
    reset_rate_limiter,
    supported_tools,
    try_delegation_execution,
)
from clawmes.delegation.service import DelegationError, RedemptionResult
from clawmes.delegation.store import get_delegation_store
from clawmes.delegation.types import (
    ROOT_AUTHORITY,
    Caveat,
    DelegationRecord,
    SignedDelegation,
)
from clawmes.policy.types import ActionContext

_TOKEN_USDC = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"
_DELEGATOR = "0x" + "33" * 20
_TO = "0x" + "11" * 20


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("CLAWMES_DELEGATION_DISABLED", raising=False)
    import clawmes.delegation.store as store_mod

    monkeypatch.setattr(store_mod, "_instance", None)
    reset_rate_limiter()


def _ctx(chain_id=8453):
    return ExtractorContext(chain_id=chain_id, delegator=_DELEGATOR)


# ─── extractors ─────────────────────────────────────────────────────────


class TestTransferExtractor:
    def test_native(self):
        action = _extract_transfer({"action": "send", "to": _TO, "amount": "0.5"}, _ctx())
        assert action.target == _TO
        assert action.value == 5 * 10**17
        assert action.call_data == "0x"

    def test_native_explicit_eth_token(self):
        action = _extract_transfer(
            {"action": "send", "to": _TO, "amount": "1", "token": "ETH"}, _ctx()
        )
        assert action.value == 10**18

    def test_erc20_known_decimals(self, monkeypatch):
        monkeypatch.setattr(ex, "_peek_decimals", lambda t, c: 6)
        action = _extract_transfer(
            {"action": "send", "to": _TO, "amount": "100", "token": _TOKEN_USDC}, _ctx()
        )
        assert action.target == _TOKEN_USDC
        assert action.value == 0
        assert action.call_data.startswith("0xa9059cbb")

    def test_erc20_unknown_decimals_skips(self, monkeypatch):
        monkeypatch.setattr(ex, "_peek_decimals", lambda t, c: None)
        assert (
            _extract_transfer(
                {"action": "send", "to": _TO, "amount": "100", "token": _TOKEN_USDC}, _ctx()
            )
            is None
        )

    def test_erc20_bad_amount(self, monkeypatch):
        monkeypatch.setattr(ex, "_peek_decimals", lambda t, c: 6)
        assert (
            _extract_transfer(
                {"action": "send", "to": _TO, "amount": "-1", "token": _TOKEN_USDC}, _ctx()
            )
            is None
        )

    def test_native_bad_amount(self):
        assert _extract_transfer({"action": "send", "to": _TO, "amount": "0"}, _ctx()) is None

    def test_wrong_action(self):
        assert _extract_transfer({"action": "estimate"}, _ctx()) is None

    def test_missing_to(self):
        assert _extract_transfer({"action": "send", "amount": "1"}, _ctx()) is None

    def test_bad_to_address(self):
        assert _extract_transfer({"action": "send", "to": "0x123", "amount": "1"}, _ctx()) is None

    def test_unknown_symbol_token_not_addr(self):
        # A token that's neither an address nor eth/native → no action.
        assert (
            _extract_transfer({"action": "send", "to": _TO, "amount": "1", "token": "WBTC"}, _ctx())
            is None
        )


class TestApprovalsExtractor:
    def test_revoke(self):
        action = _extract_approvals(
            {"action": "revoke", "token": _TOKEN_USDC, "spender": _TO}, _ctx()
        )
        assert action.target == _TOKEN_USDC
        assert action.call_data.startswith("0x095ea7b3")
        # zero allowance
        assert action.call_data.endswith("0" * 64)

    def test_wrong_action(self):
        assert _extract_approvals({"action": "approve"}, _ctx()) is None

    def test_bad_addresses(self):
        assert (
            _extract_approvals({"action": "revoke", "token": "x", "spender": _TO}, _ctx()) is None
        )


class TestNftExtractor:
    def test_transfer(self):
        action = _extract_nft(
            {"action": "transfer", "contract": _TOKEN_USDC, "to": _TO, "token_id": "7"}, _ctx()
        )
        assert action.target == _TOKEN_USDC
        assert action.call_data.startswith("0x23b872dd")

    def test_wrong_action(self):
        assert _extract_nft({"action": "mint"}, _ctx()) is None

    def test_bad_token_id(self):
        assert (
            _extract_nft(
                {"action": "transfer", "contract": _TOKEN_USDC, "to": _TO, "token_id": "abc"},
                _ctx(),
            )
            is None
        )

    def test_missing_fields(self):
        assert _extract_nft({"action": "transfer", "contract": _TOKEN_USDC}, _ctx()) is None


class TestRegistry:
    def test_register_and_list(self):
        register_extractor("mytool", lambda a, c: None)
        assert "mytool" in supported_tools()


class TestInternalHelpers:
    def test_is_addr_rejects_42char_non_hex(self):
        # 42 chars, starts 0x, but contains non-hex → hits _is_hex except.
        assert (
            _extract_transfer({"action": "send", "to": "0x" + "g" * 40, "amount": "1"}, _ctx())
            is None
        )

    def test_peek_decimals_real(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        from clawmes.services import token_decimals as td_mod
        from clawmes.services.token_decimals import TokenDecimalsService

        monkeypatch.setattr(td_mod, "_instance", TokenDecimalsService())
        # Seeded token (USDC on Base = 6 decimals) resolves without RPC.
        assert ex._peek_decimals(_TOKEN_USDC, 8453) == 6

    def test_fill_delegator_noop_when_no_zero_word(self):
        assert ex._fill_delegator("0xabcdef", "0x" + "33" * 20) == "0xabcdef"

    def test_rpc_returns_singleton(self):
        from clawmes.services.rpc import get_rpc_service

        assert ex._rpc() is get_rpc_service()


# ─── matching ───────────────────────────────────────────────────────────


def _store_record(**kw):
    signed = SignedDelegation(
        delegate="0x" + "22" * 20,
        delegator=_DELEGATOR,
        authority=ROOT_AUTHORITY,
        caveats=(Caveat("0x" + "44" * 20, "0x64", "0x"),),
        salt=1,
        signature="0x" + "cd" * 65,
    )
    defaults = dict(id="r1", chain_id=8453, delegation=signed, status="signed", tools=("transfer",))
    defaults.update(kw)
    rec = DelegationRecord(**defaults)
    get_delegation_store().save(rec)
    return rec


class TestMatching:
    def test_match_by_tool(self):
        _store_record()
        ctx = ActionContext(tool_name="transfer", args={}, chain_id=8453)
        assert find_matching_delegation(ctx).id == "r1"

    def test_no_match_wrong_tool(self):
        _store_record(tools=("nft",))
        ctx = ActionContext(tool_name="transfer", args={}, chain_id=8453)
        assert find_matching_delegation(ctx) is None

    def test_empty_tools_matches_all(self):
        _store_record(tools=())
        ctx = ActionContext(tool_name="anything", args={}, chain_id=8453)
        assert find_matching_delegation(ctx) is not None

    def test_no_match_wrong_chain(self):
        _store_record(chain_id=1)
        ctx = ActionContext(tool_name="transfer", args={}, chain_id=8453)
        assert find_matching_delegation(ctx) is None

    def test_skip_revoked(self):
        _store_record(status="revoked")
        ctx = ActionContext(tool_name="transfer", args={}, chain_id=8453)
        assert find_matching_delegation(ctx) is None

    def test_chain_none_matches(self):
        _store_record()
        ctx = ActionContext(tool_name="transfer", args={}, chain_id=None)
        assert find_matching_delegation(ctx) is not None


# ─── end-to-end try_delegation_execution ────────────────────────────────


def _fake_rpc(code="0xffff"):
    return SimpleNamespace(get_code=lambda addr, cid: code)


class TestTryDelegationExecution:
    def _ctx(self, tool="transfer", chain_id=8453):
        return ActionContext(tool_name=tool, args={}, user_id="u", chain_id=chain_id)

    def test_kill_switch(self, monkeypatch):
        monkeypatch.setenv("CLAWMES_DELEGATION_DISABLED", "1")
        out = try_delegation_execution(self._ctx(), {})
        assert out.skip_reason and "disabled" in out.skip_reason

    def test_no_extractor(self):
        out = try_delegation_execution(self._ctx(tool="defi_swap"), {})
        assert "no delegation extractor" in out.skip_reason

    def test_no_matching_delegation(self):
        out = try_delegation_execution(self._ctx(), {"action": "send", "to": _TO, "amount": "1"})
        assert "no matching delegation" in out.skip_reason

    def test_unextractable_args(self):
        _store_record()
        out = try_delegation_execution(self._ctx(), {"action": "estimate"})
        assert "could not extract" in out.skip_reason

    def test_expired_delegation(self, monkeypatch):
        _store_record(expires_at="2000-01-01T00:00:00+00:00")
        out = try_delegation_execution(self._ctx(), {"action": "send", "to": _TO, "amount": "1"})
        assert out.error and "expired" in out.error

    def test_bad_expiry_string_ignored(self, monkeypatch):
        _store_record(expires_at="not-a-date")
        monkeypatch.setattr(ex, "_rpc", lambda: _fake_rpc())
        monkeypatch.setattr(
            "clawmes.delegation.service.redeem",
            lambda rec, action: RedemptionResult("0xok", 8453),
        )
        out = try_delegation_execution(self._ctx(), {"action": "send", "to": _TO, "amount": "1"})
        assert out.executed

    def test_eoa_delegator_skips(self, monkeypatch):
        _store_record()
        monkeypatch.setattr(ex, "_rpc", lambda: _fake_rpc(code="0x"))
        out = try_delegation_execution(self._ctx(), {"action": "send", "to": _TO, "amount": "1"})
        assert "plain EOA" in out.skip_reason

    def test_getcode_error_non_fatal(self, monkeypatch):
        _store_record()

        def _boom(addr, cid):
            raise RuntimeError("rpc down")

        monkeypatch.setattr(ex, "_rpc", lambda: SimpleNamespace(get_code=_boom))
        monkeypatch.setattr(
            "clawmes.delegation.service.redeem",
            lambda rec, action: RedemptionResult("0xok", 8453),
        )
        out = try_delegation_execution(self._ctx(), {"action": "send", "to": _TO, "amount": "1"})
        assert out.executed

    def test_success(self, monkeypatch):
        _store_record()
        monkeypatch.setattr(ex, "_rpc", lambda: _fake_rpc())
        monkeypatch.setattr(
            "clawmes.delegation.service.redeem",
            lambda rec, action: RedemptionResult("0xdead", 8453),
        )
        out = try_delegation_execution(self._ctx(), {"action": "send", "to": _TO, "amount": "1"})
        assert out.executed and out.tx_hash == "0xdead"

    def test_redemption_error(self, monkeypatch):
        _store_record()
        monkeypatch.setattr(ex, "_rpc", lambda: _fake_rpc())

        def _boom(rec, action):
            raise DelegationError("over cap")

        monkeypatch.setattr("clawmes.delegation.service.redeem", _boom)
        out = try_delegation_execution(self._ctx(), {"action": "send", "to": _TO, "amount": "1"})
        assert out.error == "over cap" and not out.executed

    def test_unexpected_redeem_error(self, monkeypatch):
        _store_record()
        monkeypatch.setattr(ex, "_rpc", lambda: _fake_rpc())

        def _boom(rec, action):
            raise RuntimeError("weird")

        monkeypatch.setattr("clawmes.delegation.service.redeem", _boom)
        out = try_delegation_execution(self._ctx(), {"action": "send", "to": _TO, "amount": "1"})
        assert "delegation error" in out.error

    def test_nft_placeholder_filled_with_delegator(self, monkeypatch):
        # NFT extractor emits a zero `from`; the executor fills the delegator.
        _store_record(tools=("nft",))
        monkeypatch.setattr(ex, "_rpc", lambda: _fake_rpc())
        captured = {}

        def _redeem(rec, action):
            captured["call_data"] = action.call_data
            return RedemptionResult("0xok", 8453)

        monkeypatch.setattr("clawmes.delegation.service.redeem", _redeem)
        out = try_delegation_execution(
            self._ctx(tool="nft"),
            {"action": "transfer", "contract": _TOKEN_USDC, "to": _TO, "token_id": "7"},
        )
        assert out.executed
        # The delegator address (33*20) now appears in the `from` slot.
        assert "33" * 20 in captured["call_data"]

    def test_rate_limited(self, monkeypatch):
        _store_record()
        monkeypatch.setattr(ex, "_rpc", lambda: _fake_rpc())

        def _boom(rec, action):
            raise DelegationError("revert")

        monkeypatch.setattr("clawmes.delegation.service.redeem", _boom)
        args = {"action": "send", "to": _TO, "amount": "1"}
        for _ in range(3):
            try_delegation_execution(self._ctx(), args)
        out = try_delegation_execution(self._ctx(), args)
        assert "rate-limited" in out.error

    def test_success_clears_rate_limit(self, monkeypatch):
        _store_record()
        monkeypatch.setattr(ex, "_rpc", lambda: _fake_rpc())
        monkeypatch.setattr(
            "clawmes.delegation.service.redeem",
            lambda rec, action: RedemptionResult("0xok", 8453),
        )
        args = {"action": "send", "to": _TO, "amount": "1"}
        for _ in range(5):
            out = try_delegation_execution(self._ctx(), args)
            assert out.executed  # never rate-limited because each success clears
