"""Tests for transform_terminal_output and transform_tool_result hooks.

Both are pass-through stubs at this milestone — the real redactor lands
once ``services.credential_redactor`` is implemented. The tests pin
the signature and the pass-through behavior so callers can wire them
without surprises.
"""

from __future__ import annotations

from clawmes.hooks import transform_terminal_output, transform_tool_result


class TestTransformTerminalOutput:
    def test_passthrough(self):
        assert transform_terminal_output.callback("hello world") == "hello world"

    def test_empty_string(self):
        assert transform_terminal_output.callback("") == ""

    def test_extra_kwargs_swallowed(self):
        assert transform_terminal_output.callback("x", session_id="s", user_id="u") == "x"

    def test_unicode(self):
        assert transform_terminal_output.callback("hello 🚀") == "hello 🚀"


class TestTransformToolResult:
    def test_passthrough(self):
        assert transform_tool_result.callback("some json") == "some json"

    def test_empty(self):
        assert transform_tool_result.callback("") == ""

    def test_extra_kwargs(self):
        assert transform_tool_result.callback("x", tool_name="transfer") == "x"
