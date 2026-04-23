"""Tests for clawmes.tools.registry — decorators + register_with_ctx."""

from __future__ import annotations

import json

import pytest

from clawmes.lib.params import ParamError
from clawmes.lib.tool_result import json_result
from clawmes.tools.registry import (
    WRITE_TOOL_NAMES,
    read_tool,
    register_with_ctx,
    write_tool,
)

SAMPLE_SCHEMA = {
    "type": "object",
    "properties": {"x": {"type": "string"}},
}


class TestWriteToolDecorator:
    def test_metadata_attached(self):
        @write_tool(
            name="t_write_meta",
            toolset="t",
            description="desc",
            schema=SAMPLE_SCHEMA,
            requires_env=["X"],
            emoji="🔧",
        )
        def fn(args, **kw):
            return json_result({"ok": True})

        meta = fn._clawmes_meta
        assert meta["name"] == "t_write_meta"
        assert meta["toolset"] == "t"
        assert meta["description"] == "desc"
        assert meta["schema"] is SAMPLE_SCHEMA
        assert meta["requires_env"] == ["X"]
        assert meta["emoji"] == "🔧"
        assert meta["is_write"] is True

    def test_added_to_write_set(self):
        @write_tool(
            name="t_in_set",
            toolset="t",
            description="d",
            schema=SAMPLE_SCHEMA,
        )
        def fn(args, **kw):
            return json_result({})

        assert "t_in_set" in WRITE_TOOL_NAMES

    def test_handler_runs_normal_path(self):
        @write_tool(name="t_run_ok", toolset="t", description="d", schema=SAMPLE_SCHEMA)
        def fn(args, **kw):
            return json_result({"ran": True})

        out = json.loads(fn({"x": "y"}))
        assert out["details"] == {"ran": True}

    def test_handler_param_error_converted(self):
        @write_tool(name="t_param_err", toolset="t", description="d", schema=SAMPLE_SCHEMA)
        def fn(args, **kw):
            raise ParamError("bad arg")

        out = json.loads(fn({}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "param_error"

    def test_handler_other_exception_converted(self):
        @write_tool(name="t_other_err", toolset="t", description="d", schema=SAMPLE_SCHEMA)
        def fn(args, **kw):
            raise RuntimeError("boom")

        out = json.loads(fn({}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "tool_error"


class TestReadToolDecorator:
    def test_metadata_marks_read(self):
        @read_tool(name="t_read_meta", toolset="t", description="d", schema=SAMPLE_SCHEMA)
        def fn(args, **kw):
            return json_result({})

        assert fn._clawmes_meta["is_write"] is False

    def test_not_in_write_set(self):
        @read_tool(name="t_not_in_set", toolset="t", description="d", schema=SAMPLE_SCHEMA)
        def fn(args, **kw):
            return json_result({})

        assert "t_not_in_set" not in WRITE_TOOL_NAMES

    def test_handler_runs(self):
        @read_tool(name="t_read_run", toolset="t", description="d", schema=SAMPLE_SCHEMA)
        def fn(args, **kw):
            return json_result({"ok": 1})

        out = json.loads(fn({}))
        assert out["details"]["ok"] == 1

    def test_param_error_converted(self):
        @read_tool(name="t_read_param_err", toolset="t", description="d", schema=SAMPLE_SCHEMA)
        def fn(args, **kw):
            raise ParamError("bad")

        out = json.loads(fn({}))
        assert out["details"]["error_code"] == "param_error"

    def test_other_exception_converted(self):
        @read_tool(name="t_read_other_err", toolset="t", description="d", schema=SAMPLE_SCHEMA)
        def fn(args, **kw):
            raise RuntimeError("boom")

        out = json.loads(fn({}))
        assert out["details"]["error_code"] == "tool_error"


class TestRegisterWithCtx:
    def test_passes_metadata_to_ctx(self):
        @write_tool(
            name="t_ctx",
            toolset="t",
            description="d",
            schema=SAMPLE_SCHEMA,
            emoji="🚀",
        )
        def fn(args, **kw):
            return json_result({})

        recorded = []

        class FakeCtx:
            def register_tool(self, **kw):
                recorded.append(kw)

        register_with_ctx(FakeCtx(), fn)
        assert recorded[0]["name"] == "t_ctx"
        assert recorded[0]["toolset"] == "t"
        assert recorded[0]["emoji"] == "🚀"
        assert recorded[0]["handler"] is fn

    def test_undecorated_function_raises(self):
        def naked(args, **kw):
            return json.dumps({})

        class FakeCtx:
            def register_tool(self, **kw):
                pass

        with pytest.raises(RuntimeError, match="not a clawmes tool"):
            register_with_ctx(FakeCtx(), naked)
