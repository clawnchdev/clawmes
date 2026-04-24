"""Tests for transform_terminal_output and transform_tool_result hooks.

Both delegate to ``clawmes.services.credential_redactor.scan_and_redact``.
Tests verify (a) credentials get redacted on the way through, (b) plain
text passes through unchanged, (c) errors during redaction degrade to
pass-through rather than blanking the output.
"""

from __future__ import annotations

import pytest

from clawmes.hooks import transform_terminal_output, transform_tool_result
from clawmes.services import credential_redactor as cr_module


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(cr_module, "_instance", None)


class TestTransformTerminalOutput:
    def test_plain_text_passes_through(self):
        assert transform_terminal_output.callback("hello world") == "hello world"

    def test_empty_string(self):
        assert transform_terminal_output.callback("") == ""

    def test_extra_kwargs_swallowed(self):
        assert transform_terminal_output.callback("x", session_id="s", user_id="u") == "x"

    def test_unicode(self):
        assert transform_terminal_output.callback("hello 🚀") == "hello 🚀"

    def test_redacts_api_key(self):
        secret = "ghp_" + "a" * 36
        out = transform_terminal_output.callback(f"token={secret}")
        assert secret not in out
        assert "[REDACTED:api_key" in out

    def test_redactor_failure_degrades_to_passthrough(self, monkeypatch):
        from clawmes.hooks import transform_terminal_output as mod

        def boom(*a, **kw):
            raise RuntimeError("simulated redactor failure")

        monkeypatch.setattr(mod, "scan_and_redact", boom)
        out = transform_terminal_output.callback("any text")
        assert out == "any text"


class TestTransformToolResult:
    def test_plain_text_passes_through(self):
        assert transform_tool_result.callback("some json") == "some json"

    def test_empty(self):
        assert transform_tool_result.callback("") == ""

    def test_extra_kwargs(self):
        assert transform_tool_result.callback("x", tool_name="transfer") == "x"

    def test_redacts_wc_uri(self):
        uri = "wc:" + "a" * 32 + "@2"
        out = transform_tool_result.callback(f"pair: {uri}")
        assert uri not in out
        assert "[REDACTED:walletconnect_uri" in out

    def test_redactor_failure_degrades_to_passthrough(self, monkeypatch):
        from clawmes.hooks import transform_tool_result as mod

        def boom(*a, **kw):
            raise RuntimeError("simulated redactor failure")

        monkeypatch.setattr(mod, "scan_and_redact", boom)
        out = transform_tool_result.callback("any result")
        assert out == "any result"
