"""Tests for clawmes.policy.evaluator (stub returning _ALLOW)."""

from __future__ import annotations

from clawmes.policy.evaluator import ActionContext, Decision, evaluate


def test_evaluate_returns_allow_for_now():
    """v0.1 stub: every action returns _ALLOW."""
    ctx = ActionContext(tool_name="transfer", args={"to": "alice.eth"})
    decision = evaluate(ctx)
    assert isinstance(decision, Decision)
    assert decision.kind == "allow"
    assert decision.policy_name == ""
    assert decision.reason == ""


def test_action_context_immutable():
    from dataclasses import FrozenInstanceError

    ctx = ActionContext(tool_name="transfer", args={})
    try:
        ctx.tool_name = "other"
        raise AssertionError("should have raised")
    except FrozenInstanceError:
        pass
