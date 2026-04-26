"""Tests for clawmes.policy.evaluator (real implementation)."""

from __future__ import annotations

import pytest

from clawmes.policy import usage_counter as uc_module
from clawmes.policy.evaluator import (
    _default_reason,
    _gate_triggers,
    evaluate,
    record_invocation,
)
from clawmes.policy.types import ActionContext, Policy
from clawmes.policy.usage_counter import get_usage_counter


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    # Reset usage counter singleton between tests
    monkeypatch.setattr(uc_module, "_instance", None)


def _ctx(**kw):
    base = {"tool_name": "transfer", "args": {}, "user_id": "default"}
    base.update(kw)
    return ActionContext(**base)


class TestNoMatch:
    def test_no_policies_allows(self):
        assert evaluate(_ctx(), policies=[]).kind == "allow"

    def test_no_matching_filter_allows(self):
        policies = [
            Policy(name="other-tool-only", decision="block", applies_to_tools=("defi_swap",)),
        ]
        assert evaluate(_ctx(tool_name="transfer"), policies=policies).kind == "allow"


class TestCatchAllPolicies:
    def test_catch_all_block(self):
        policies = [Policy(name="block-all", decision="block")]
        decision = evaluate(_ctx(), policies=policies)
        assert decision.kind == "block"
        assert decision.policy_name == "block-all"

    def test_catch_all_confirm(self):
        policies = [Policy(name="confirm-all", decision="confirm")]
        decision = evaluate(_ctx(), policies=policies)
        assert decision.kind == "confirm"

    def test_first_match_wins(self):
        # Block is first in list — should win over the later confirm
        policies = [
            Policy(name="block-tool", decision="block", applies_to_tools=("transfer",)),
            Policy(name="confirm-all", decision="confirm"),
        ]
        decision = evaluate(_ctx(tool_name="transfer"), policies=policies)
        assert decision.kind == "block"
        assert decision.policy_name == "block-tool"


class TestQuantitativeGates:
    def test_amount_gate_triggers_when_exceeded(self):
        policies = [
            Policy(
                name="block-large",
                decision="block",
                applies_to_tools=("transfer",),
                max_amount_wei=10**17,  # 0.1 ETH threshold
            ),
        ]
        # 0.5 ETH transfer → triggers
        ctx = _ctx(value_wei=5 * 10**17)
        decision = evaluate(ctx, policies=policies)
        assert decision.kind == "block"

    def test_amount_gate_does_not_trigger_below_threshold(self):
        policies = [
            Policy(
                name="block-large",
                decision="block",
                applies_to_tools=("transfer",),
                max_amount_wei=10**17,
            ),
        ]
        # 0.05 ETH < threshold → policy doesn't apply → allow
        ctx = _ctx(value_wei=5 * 10**16)
        decision = evaluate(ctx, policies=policies)
        assert decision.kind == "allow"

    def test_amount_gate_with_unknown_value(self):
        # value_wei=None → policy can't fire, falls through to allow
        policies = [
            Policy(
                name="block-large",
                decision="block",
                applies_to_tools=("transfer",),
                max_amount_wei=10**17,
            ),
        ]
        decision = evaluate(_ctx(value_wei=None), policies=policies)
        assert decision.kind == "allow"

    def test_rate_gate_triggers(self):
        counter = get_usage_counter()
        # Pre-load 5 invocations
        for _ in range(5):
            counter.record("default", "transfer")

        policies = [
            Policy(
                name="rate-limit",
                decision="confirm",
                applies_to_tools=("transfer",),
                max_per_hour=5,
            ),
        ]
        decision = evaluate(_ctx(), policies=policies)
        assert decision.kind == "confirm"

    def test_rate_gate_below_threshold(self):
        counter = get_usage_counter()
        # 3 < 5
        for _ in range(3):
            counter.record("default", "transfer")

        policies = [
            Policy(
                name="rate-limit",
                decision="confirm",
                applies_to_tools=("transfer",),
                max_per_hour=5,
            ),
        ]
        # Below cap → policy doesn't fire → allow
        assert evaluate(_ctx(), policies=policies).kind == "allow"


class TestGateInteraction:
    def test_filters_skip_before_gates(self):
        # Filter says wrong tool → policy is skipped without checking the gate
        policies = [
            Policy(
                name="amount-on-swap",
                decision="block",
                applies_to_tools=("defi_swap",),
                max_amount_wei=1,  # very low — would fire if checked
            ),
        ]
        # Tool is transfer; filter mismatches; gate not checked
        assert (
            evaluate(_ctx(tool_name="transfer", value_wei=10**18), policies=policies).kind
            == "allow"
        )


class TestRecordInvocation:
    def test_increments_counter(self):
        counter = get_usage_counter()
        assert counter.count("default", "transfer") == 0
        record_invocation(_ctx())
        assert counter.count("default", "transfer") == 1


class TestDefaultsLoaded:
    def test_evaluate_loads_persisted_policies(self):
        # No `policies=` kwarg → load from disk (defaults installed on first run)
        decision = evaluate(_ctx(tool_name="transfer", value_wei=10**18))
        # 1 ETH > 0.05 ETH threshold → confirm
        assert decision.kind == "confirm"
        assert decision.policy_name == "confirm_large_transfers"


class TestDefaultReason:
    def test_amount_gate_reason(self):
        policy = Policy(
            name="x",
            decision="block",
            applies_to_tools=("transfer",),
            max_amount_wei=100,
        )
        ctx = _ctx(value_wei=200)
        text = _default_reason(policy, ctx)
        assert "200" in text and "100" in text

    def test_rate_gate_reason(self):
        counter = get_usage_counter()
        for _ in range(7):
            counter.record("default", "transfer")
        policy = Policy(
            name="x",
            decision="confirm",
            applies_to_tools=("transfer",),
            max_per_hour=5,
        )
        ctx = _ctx()
        text = _default_reason(policy, ctx)
        assert "7" in text and "5" in text

    def test_no_gates_reason(self):
        policy = Policy(name="catchall", decision="block")
        ctx = _ctx()
        text = _default_reason(policy, ctx)
        assert "catchall" in text


class TestGateTriggersHelper:
    def test_no_gates_returns_false(self):
        policy = Policy(name="x", decision="allow")
        # Should NOT happen — has_quantitative_gates filters this
        # earlier — but verify the helper itself
        assert _gate_triggers(policy, _ctx(), get_usage_counter()) is False

    def test_amount_gate_triggers_at_exact_threshold(self):
        policy = Policy(
            name="x",
            decision="block",
            applies_to_tools=("t",),
            max_amount_wei=100,
        )
        ctx = _ctx(value_wei=100)  # >=, not strictly greater
        assert _gate_triggers(policy, ctx, get_usage_counter()) is True
