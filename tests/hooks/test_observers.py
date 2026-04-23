"""Tests for the observer-only hooks (no return contract).

These hooks return ``None``; we exercise them to make sure they don't
raise on common input shapes and to lock in the contract before real
side effects land in v0.2.0.
"""

from __future__ import annotations

from clawmes.hooks import (
    on_session,
    pre_gateway_dispatch,
    pre_tool_call,
    subagent_stop,
)


class TestOnSession:
    def test_on_start(self):
        assert on_session.on_start(session_id="s1") is None

    def test_on_end(self):
        assert on_session.on_end(session_id="s1") is None

    def test_on_finalize(self):
        assert on_session.on_finalize(session_id="s1") is None

    def test_on_reset(self):
        assert on_session.on_reset(session_id="s1") is None

    def test_no_session_id(self):
        # All four accept missing session_id without raising
        assert on_session.on_start() is None
        assert on_session.on_end() is None
        assert on_session.on_finalize() is None
        assert on_session.on_reset() is None


class TestPreToolCall:
    def test_returns_none(self):
        assert pre_tool_call.callback(tool_name="transfer", args={"to": "alice.eth"}) is None

    def test_handles_empty_args(self):
        assert pre_tool_call.callback(tool_name="transfer", args=None) is None

    def test_extra_kwargs_swallowed(self):
        # Forward-compat: extra kwargs from Hermes don't crash us
        assert (
            pre_tool_call.callback(
                tool_name="transfer",
                args={},
                user_id="u",
                future_field="surprise",
            )
            is None
        )


class TestPreGatewayDispatch:
    def test_no_event_returns_none(self):
        assert pre_gateway_dispatch.callback() is None

    def test_with_event(self):
        result = pre_gateway_dispatch.callback(event={"from": "user-1", "content": "hello world"})
        assert result is None

    def test_with_empty_content(self):
        result = pre_gateway_dispatch.callback(event={"from": "user-1", "content": ""})
        assert result is None

    def test_with_missing_content(self):
        # Branch: event.get("content") returns None → len(None or "") == 0
        result = pre_gateway_dispatch.callback(event={"from": "user-1"})
        assert result is None

    def test_with_extra_args(self):
        # gateway/session_store kwargs accepted
        result = pre_gateway_dispatch.callback(
            event={"from": "u", "content": "x"},
            gateway=object(),
            session_store=object(),
        )
        assert result is None


class TestSubagentStop:
    def test_full_kwargs(self):
        assert (
            subagent_stop.callback(
                parent_session_id="parent",
                child_role="worker",
                child_summary="did the thing",
                child_status="ok",
                duration_ms=152.3,
            )
            is None
        )

    def test_missing_optional_kwargs(self):
        assert subagent_stop.callback() is None

    def test_no_duration(self):
        assert subagent_stop.callback(child_status="error") is None
