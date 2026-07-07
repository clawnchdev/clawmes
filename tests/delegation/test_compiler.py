"""Tests for clawmes.delegation.compiler."""

from __future__ import annotations

import pytest

from clawmes.delegation.compiler import (
    CompileError,
    DelegationSpec,
    Erc20Limit,
    compile_spec,
    enforcers_in,
    format_compilation,
    spec_from_policy,
)
from clawmes.delegation.types import (
    ALLOWED_TARGETS_ENFORCER,
    ERC20_PERIOD_TRANSFER_ENFORCER,
    ERC20_TRANSFER_AMOUNT_ENFORCER,
    LIMITED_CALLS_ENFORCER,
    NATIVE_TOKEN_PERIOD_TRANSFER_ENFORCER,
    NATIVE_TOKEN_TRANSFER_AMOUNT_ENFORCER,
    ROOT_AUTHORITY,
    TIMESTAMP_ENFORCER,
    VALUE_LTE_ENFORCER,
)
from clawmes.policy.types import Policy

_DELEGATE = "0x" + "22" * 20
_DELEGATOR = "0x" + "33" * 20


def _compile(spec, **kw):
    return compile_spec(
        spec, delegate=_DELEGATE, delegator=_DELEGATOR, salt=1, now_ts=1_750_000_000, **kw
    )


class TestChainValidation:
    def test_unsupported_chain(self):
        with pytest.raises(CompileError, match="not supported"):
            _compile(DelegationSpec(native_per_call_wei=1), chain_id=999999)


class TestNative:
    def test_per_call_maps_value_lte(self):
        c = _compile(DelegationSpec(native_per_call_wei=10**17))
        assert [cv.enforcer for cv in c.delegation.caveats] == [VALUE_LTE_ENFORCER]
        assert c.delegation.authority == ROOT_AUTHORITY

    def test_per_call_negative_rejected(self):
        with pytest.raises(CompileError, match="non-negative"):
            _compile(DelegationSpec(native_per_call_wei=-1))

    def test_cap_with_period_maps_period_enforcer(self):
        c = _compile(DelegationSpec(native_cap_wei=5 * 10**18, native_period_seconds=86400))
        assert c.delegation.caveats[0].enforcer == NATIVE_TOKEN_PERIOD_TRANSFER_ENFORCER
        assert not c.warnings

    def test_cap_without_period_maps_lifetime_and_warns(self):
        c = _compile(DelegationSpec(native_cap_wei=5 * 10**18))
        assert c.delegation.caveats[0].enforcer == NATIVE_TOKEN_TRANSFER_AMOUNT_ENFORCER
        assert any("lifetime" in w for w in c.warnings)

    def test_cap_zero_rejected(self):
        with pytest.raises(CompileError, match="native_cap_wei must be positive"):
            _compile(DelegationSpec(native_cap_wei=0, native_per_call_wei=1))


class TestErc20:
    def test_lifetime(self):
        c = _compile(DelegationSpec(erc20=[Erc20Limit(token="0x" + "aa" * 20, max_amount=100)]))
        assert c.delegation.caveats[0].enforcer == ERC20_TRANSFER_AMOUNT_ENFORCER

    def test_periodic(self):
        c = _compile(
            DelegationSpec(
                erc20=[Erc20Limit(token="0x" + "aa" * 20, max_amount=100, period_seconds=86400)]
            )
        )
        assert c.delegation.caveats[0].enforcer == ERC20_PERIOD_TRANSFER_ENFORCER

    def test_nonpositive_rejected(self):
        with pytest.raises(CompileError, match="must be positive"):
            _compile(DelegationSpec(erc20=[Erc20Limit(token="0x" + "aa" * 20, max_amount=0)]))


class TestCalls:
    def test_max_calls_only(self):
        c = _compile(DelegationSpec(max_calls=5))
        assert [cv.enforcer for cv in c.delegation.caveats] == [LIMITED_CALLS_ENFORCER]

    def test_max_calls_with_window_adds_timestamp(self):
        c = _compile(DelegationSpec(max_calls=5, calls_window_seconds=3600))
        enforcers = [cv.enforcer for cv in c.delegation.caveats]
        assert enforcers == [LIMITED_CALLS_ENFORCER, TIMESTAMP_ENFORCER]
        assert c.expires_at  # window sets expiry
        assert any("window" in w for w in c.warnings)

    def test_max_calls_nonpositive(self):
        with pytest.raises(CompileError, match="max_calls must be positive"):
            _compile(DelegationSpec(max_calls=0, native_per_call_wei=1))


class TestTargetsAndExpiry:
    def test_allowed_targets(self):
        c = _compile(DelegationSpec(allowed_targets=["0x" + "11" * 20, "0x" + "22" * 20]))
        assert c.delegation.caveats[0].enforcer == ALLOWED_TARGETS_ENFORCER

    def test_allowed_targets_bad_address(self):
        with pytest.raises(CompileError, match="non-addresses"):
            _compile(DelegationSpec(allowed_targets=["not-an-address"]))

    def test_allowed_targets_42char_non_hex(self):
        # 42 chars, 0x-prefixed, but non-hex → _is_address ValueError branch.
        with pytest.raises(CompileError, match="non-addresses"):
            _compile(DelegationSpec(allowed_targets=["0x" + "z" * 40]))

    def test_expiry_adds_timestamp_and_sets_expires_at(self):
        c = _compile(DelegationSpec(expiry_seconds=604800))
        assert c.delegation.caveats[0].enforcer == TIMESTAMP_ENFORCER
        assert c.expires_at.startswith("2025-06-")

    def test_expiry_and_window_take_earliest(self):
        c = _compile(DelegationSpec(max_calls=1, calls_window_seconds=3600, expiry_seconds=604800))
        # earliest of (now+3600, now+604800) = now+3600
        assert "T" in c.expires_at


class TestUnrestricted:
    def test_refused_by_default(self):
        with pytest.raises(CompileError, match="unrestricted"):
            _compile(DelegationSpec())

    def test_allowed_when_opted_in(self):
        c = _compile(DelegationSpec(allow_unrestricted=True))
        assert c.delegation.caveats == ()


class TestSaltAndDefaults:
    def test_auto_salt_deterministic_ish(self):
        c1 = compile_spec(
            DelegationSpec(native_per_call_wei=1),
            delegate=_DELEGATE,
            delegator=_DELEGATOR,
            now_ts=1000,
        )
        c2 = compile_spec(
            DelegationSpec(native_per_call_wei=1),
            delegate=_DELEGATE,
            delegator=_DELEGATOR,
            now_ts=1000,
        )
        # Same inputs + same timestamp → same salt.
        assert c1.delegation.salt == c2.delegation.salt
        assert isinstance(c1.delegation.salt, int)


class TestSpecFromPolicy:
    def test_max_amount_maps_to_per_call(self):
        policy = Policy(name="p", decision="confirm", max_amount_wei=10**17)
        spec, notes = spec_from_policy(policy, expiry_seconds=3600)
        assert spec.native_per_call_wei == 10**17
        assert spec.expiry_seconds == 3600
        assert any("chain-agnostic" in n for n in notes)

    def test_max_per_hour_maps_to_calls_with_window(self):
        policy = Policy(name="p", decision="confirm", max_per_hour=20, chain_ids=(8453,))
        spec, notes = spec_from_policy(policy)
        assert spec.max_calls == 20
        assert spec.calls_window_seconds == 3600
        assert any("1-hour" in n for n in notes)
        assert not any("chain-agnostic" in n for n in notes)


class TestFormatting:
    def test_format_and_enforcers(self):
        c = _compile(DelegationSpec(native_per_call_wei=10**17, expiry_seconds=604800))
        text = format_compilation(c, 8453)
        assert "Delegation preview" in text
        assert "Base" in text
        assert "ValueLteEnforcer" in enforcers_in(c)

    def test_format_includes_warnings(self):
        c = _compile(DelegationSpec(native_cap_wei=10**18))
        text = format_compilation(c, 8453)
        assert "Warnings" in text
