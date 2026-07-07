"""Tests for clawmes.delegation.types."""

from __future__ import annotations

from clawmes.delegation.types import (
    DELEGATION_MANAGER,
    EXECUTE_MODE_DEFAULT,
    ROOT_AUTHORITY,
    SUPPORTED_CHAIN_IDS,
    Caveat,
    DelegationRecord,
    ExecutionAction,
    SignedDelegation,
    UnsignedDelegation,
    chain_name,
    delegation_domain,
    enforcer_name,
    is_supported_chain,
)
from clawmes.delegation.types import (
    NATIVE_TOKEN_PERIOD_TRANSFER_ENFORCER as NPT,
)


class TestConstants:
    def test_root_authority_is_all_f(self):
        assert ROOT_AUTHORITY == "0x" + "f" * 64

    def test_execute_mode_is_zero(self):
        assert EXECUTE_MODE_DEFAULT == "0x" + "0" * 64

    def test_delegation_manager_address(self):
        assert DELEGATION_MANAGER == "0xdb9B1e94B5b69Df7e401DDbedE43491141047dB3"

    def test_supported_chains(self):
        assert {1, 8453, 42161, 10, 137, 59144, 11155111, 84532} == set(SUPPORTED_CHAIN_IDS)


class TestHelpers:
    def test_is_supported_chain(self):
        assert is_supported_chain(8453)
        assert not is_supported_chain(999999)

    def test_chain_name_known(self):
        assert chain_name(8453) == "Base"

    def test_chain_name_unknown(self):
        assert chain_name(424242) == "424242"

    def test_enforcer_name_known(self):
        assert enforcer_name(NPT) == "NativeTokenPeriodTransferEnforcer"

    def test_enforcer_name_unknown(self):
        assert enforcer_name("0x" + "ab" * 20) == "0x" + "ab" * 20

    def test_delegation_domain(self):
        d = delegation_domain(8453)
        assert d == {
            "name": "DelegationManager",
            "version": "1",
            "chainId": 8453,
            "verifyingContract": DELEGATION_MANAGER,
        }


class TestDataclasses:
    def test_signed_from_unsigned(self):
        unsigned = UnsignedDelegation(
            delegate="0x" + "22" * 20,
            delegator="0x" + "33" * 20,
            authority=ROOT_AUTHORITY,
            caveats=(Caveat(enforcer="0x" + "44" * 20, terms="0x00"),),
            salt=1,
        )
        signed = SignedDelegation.from_unsigned(unsigned, "0xdead")
        assert signed.signature == "0xdead"
        assert signed.delegate == unsigned.delegate
        assert signed.caveats == unsigned.caveats

    def test_caveat_default_args(self):
        assert Caveat(enforcer="0x" + "44" * 20, terms="0x00").args == "0x"

    def test_record_redeemable_states(self):
        base = SignedDelegation(
            delegate="0x" + "22" * 20,
            delegator="0x" + "33" * 20,
            authority=ROOT_AUTHORITY,
            caveats=(),
            salt=1,
            signature="0xaa",
        )
        for status, expected in [
            ("signed", True),
            ("active", True),
            ("revoked", False),
            ("expired", False),
            ("unsigned", False),
        ]:
            rec = DelegationRecord(id="x", chain_id=8453, delegation=base, status=status)
            assert rec.is_redeemable() is expected

    def test_execution_action(self):
        a = ExecutionAction(target="0x" + "11" * 20, value=5, call_data="0x")
        assert a.value == 5
