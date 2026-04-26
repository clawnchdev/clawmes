"""Tests for clawmes.policy.types — Policy IR + ActionContext + Decision."""

from __future__ import annotations

import pytest

from clawmes.policy.types import (
    DEFAULT_POLICIES,
    ActionContext,
    Decision,
    Policy,
)


class TestPolicy:
    def test_minimal_construction(self):
        p = Policy(name="x", decision="allow")
        assert p.name == "x"
        assert p.decision == "allow"
        assert p.applies_to_tools == ()
        assert p.chain_ids == ()
        assert p.max_amount_wei is None
        assert p.max_per_hour is None

    def test_frozen(self):
        from dataclasses import FrozenInstanceError

        p = Policy(name="x", decision="allow")
        with pytest.raises(FrozenInstanceError):
            p.name = "other"

    def test_matches_filters_default(self):
        # No filters → matches anything
        p = Policy(name="x", decision="allow")
        ctx = ActionContext(tool_name="transfer", args={})
        assert p.matches_filters(ctx) is True

    def test_matches_filters_tool_match(self):
        p = Policy(name="x", decision="block", applies_to_tools=("transfer",))
        assert p.matches_filters(ActionContext(tool_name="transfer", args={})) is True
        assert p.matches_filters(ActionContext(tool_name="defi_swap", args={})) is False

    def test_matches_filters_chain_match(self):
        p = Policy(name="x", decision="block", chain_ids=(8453,))
        assert p.matches_filters(ActionContext(tool_name="t", args={}, chain_id=8453)) is True
        assert p.matches_filters(ActionContext(tool_name="t", args={}, chain_id=1)) is False
        # Missing chain_id when filter is set → no match
        assert p.matches_filters(ActionContext(tool_name="t", args={})) is False

    def test_matches_combines_tool_and_chain(self):
        p = Policy(
            name="x",
            decision="block",
            applies_to_tools=("transfer",),
            chain_ids=(8453,),
        )
        # Both must match
        assert (
            p.matches_filters(ActionContext(tool_name="transfer", args={}, chain_id=8453)) is True
        )
        assert p.matches_filters(ActionContext(tool_name="transfer", args={}, chain_id=1)) is False
        assert (
            p.matches_filters(ActionContext(tool_name="defi_swap", args={}, chain_id=8453)) is False
        )

    def test_has_quantitative_gates(self):
        assert Policy(name="x", decision="allow").has_quantitative_gates() is False
        assert (
            Policy(name="x", decision="confirm", max_amount_wei=10).has_quantitative_gates() is True
        )
        assert Policy(name="x", decision="confirm", max_per_hour=5).has_quantitative_gates() is True


class TestActionContext:
    def test_minimal(self):
        ctx = ActionContext(tool_name="transfer", args={})
        assert ctx.user_id == "default"
        assert ctx.chain_id is None
        assert ctx.value_wei is None

    def test_frozen(self):
        from dataclasses import FrozenInstanceError

        ctx = ActionContext(tool_name="transfer", args={})
        with pytest.raises(FrozenInstanceError):
            ctx.tool_name = "other"


class TestDecision:
    def test_minimal(self):
        d = Decision(kind="allow")
        assert d.policy_name == ""
        assert d.reason == ""

    def test_frozen(self):
        from dataclasses import FrozenInstanceError

        d = Decision(kind="block")
        with pytest.raises(FrozenInstanceError):
            d.kind = "allow"


class TestDefaultPolicies:
    def test_block_unbounded_approvals_present(self):
        names = {p.name for p in DEFAULT_POLICIES}
        assert "block_unbounded_token_approvals" in names

    def test_confirm_large_transfers_present(self):
        names = {p.name for p in DEFAULT_POLICIES}
        assert "confirm_large_transfers" in names

    def test_rate_limit_swaps_present(self):
        names = {p.name for p in DEFAULT_POLICIES}
        assert "rate_limit_swaps" in names
