"""Tests for clawmes.lib.tool_result."""

from __future__ import annotations

import json

from clawmes.lib.tool_result import error_result, json_result, text_result


class TestTextResult:
    def test_minimal(self):
        out = json.loads(text_result("hello"))
        assert out == {"content": [{"type": "text", "text": "hello"}]}
        assert "isError" not in out

    def test_with_details(self):
        out = json.loads(text_result("hi", details={"foo": 1}))
        assert out["details"] == {"foo": 1}

    def test_details_zero_is_preserved(self):
        # `details=0` should still be carried (not stripped as falsy)
        out = json.loads(text_result("zero", details=0))
        assert out["details"] == 0

    def test_details_none_is_omitted(self):
        out = json.loads(text_result("none", details=None))
        assert "details" not in out

    def test_empty_string(self):
        out = json.loads(text_result(""))
        assert out["content"][0]["text"] == ""


class TestJsonResult:
    def test_default_summary_is_pretty_dump(self):
        out = json.loads(json_result({"a": 1}))
        assert "\n" in out["content"][0]["text"]
        assert out["details"] == {"a": 1}

    def test_explicit_summary(self):
        out = json.loads(json_result({"a": 1}, summary="all good"))
        assert out["content"][0]["text"] == "all good"
        assert out["details"] == {"a": 1}

    def test_handles_non_serializable_with_default_str(self):
        from datetime import datetime

        ts = datetime(2026, 1, 1)
        # Should not raise — `default=str` in the json.dumps call
        out_text = json_result({"ts": ts})
        out = json.loads(out_text)
        assert "2026-01-01" in out["content"][0]["text"]


class TestErrorResult:
    def test_isError_true(self):
        out = json.loads(error_result("bad"))
        assert out["isError"] is True
        assert out["content"][0]["text"] == "bad"
        assert out["details"] is None

    def test_with_code(self):
        out = json.loads(error_result("nope", code="policy_block"))
        assert out["details"] == {"error_code": "policy_block"}
        assert out["isError"] is True
