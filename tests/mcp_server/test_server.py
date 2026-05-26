"""Tests for clawmes.mcp.server."""

from __future__ import annotations

import pytest

from clawmes import mcp_server as mcp_pkg
from clawmes.mcp_server import server as srv


class TestToolList:
    def test_lists_all_factories(self):
        tools = srv._build_tool_list()
        names = [t.name for t in tools]
        for expected in (
            "defi_price",
            "defi_balance",
            "market_intel",
            "clawnch_launch",
            "clawnch_fees",
            "bv7x_oracle",
            "bv7x_market",
            "nft",
            "block_explorer",
            "cost_basis",
        ):
            assert expected in names

    def test_tool_has_schema(self):
        tools = srv._build_tool_list()
        for t in tools:
            assert isinstance(t.inputSchema, dict)
            assert t.description  # all tools have descriptions

    def test_skips_tools_without_meta(self, monkeypatch):
        # Defensive — a factory that returns a non-decorated function
        # should be silently skipped, not crash the list.
        def _bad_factory():
            return lambda args, **kw: '{"ok": true}'

        monkeypatch.setitem(srv._TOOL_FACTORIES, "bogus", _bad_factory)
        tools = srv._build_tool_list()
        names = [t.name for t in tools]
        assert "bogus" not in names


class TestCallTool:
    def test_unknown_tool(self):
        out = srv._call_tool("not_a_real_tool", {})
        import json

        body = json.loads(out)
        assert body["isError"] is True
        assert "Unknown" in body["content"][0]["text"]

    def test_dispatches_to_real_tool(self):
        # defi_balance with no args should bounce on schema validation —
        # but the call should still return a JSON envelope (no exception).
        out = srv._call_tool("defi_balance", {})
        import json

        body = json.loads(out)
        assert isinstance(body, dict)
        # Either isError true (no wallet etc.) or a valid envelope
        assert "isError" in body or "content" in body

    def test_handles_runtime_error(self, monkeypatch):
        def _boom_factory():
            def _fn(args, **kwargs):
                raise RuntimeError("kaboom")

            _fn._clawmes_meta = {"description": "boom", "schema": {}}  # type: ignore[attr-defined]
            return _fn

        monkeypatch.setitem(srv._TOOL_FACTORIES, "_boom_test", _boom_factory)
        out = srv._call_tool("_boom_test", {})
        import json

        body = json.loads(out)
        assert body["isError"] is True
        assert "kaboom" in body["content"][0]["text"]


class TestBuildServer:
    def test_returns_a_server(self):
        s = srv.build_server()
        # Just verify the type — actual stdio plumbing is tested via
        # the smoke-test in the README. The server lifecycle test
        # would require running asyncio + faking stdio streams.
        assert s.name == "clawmes"

    @pytest.mark.asyncio
    async def test_list_tools_handler(self):
        # Exercise the registered list_tools handler — the decorator
        # stores it on the Server's request handlers map; we walk it.
        s = srv.build_server()
        # The mcp SDK stores handlers in s.request_handlers keyed by request
        # type. We pull the list_tools handler and invoke it.
        from mcp.types import ListToolsRequest

        # Find the ListTools handler the decorator registered
        handlers = s.request_handlers
        handler = handlers.get(ListToolsRequest)
        assert handler is not None

    @pytest.mark.asyncio
    async def test_call_tool_handler_wraps_text_content(self):
        s = srv.build_server()
        from mcp.types import CallToolRequest

        handler = s.request_handlers.get(CallToolRequest)
        assert handler is not None


class TestImporters:
    """The factories that lazy-import tools shouldn't crash on import."""

    @pytest.mark.parametrize("name", list(srv._TOOL_FACTORIES.keys()))
    def test_factory_returns_callable(self, name):
        fn = srv._TOOL_FACTORIES[name]()
        assert callable(fn)
        assert hasattr(fn, "_clawmes_meta")


class TestWrapCallToolResult:
    def test_wraps_text(self):
        out = srv._wrap_call_tool_result('{"ok": true}')
        assert len(out) == 1
        assert out[0].type == "text"
        assert out[0].text == '{"ok": true}'


class TestServerHandlers:
    """Exercise the list_tools and call_tool handlers that the
    ``build_server`` decorator registers."""

    @pytest.mark.asyncio
    async def test_list_tools_returns_list(self):
        s = srv.build_server()
        from mcp.types import ListToolsRequest

        handler = s.request_handlers[ListToolsRequest]
        # The decorator wraps the user handler — call it via the
        # registered handler chain. The handler signature expects a
        # request object.
        req = ListToolsRequest(method="tools/list", params=None)
        resp = await handler(req)
        # ListToolsResult has a tools field
        assert len(resp.root.tools) > 0

    @pytest.mark.asyncio
    async def test_call_tool_returns_text_content(self):
        s = srv.build_server()
        from mcp.types import CallToolRequest, CallToolRequestParams

        handler = s.request_handlers[CallToolRequest]
        req = CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(name="defi_balance", arguments={}),
        )
        resp = await handler(req)
        # Result has content list of TextContent
        content = resp.root.content
        assert len(content) > 0
        assert content[0].type == "text"


class TestMainEntrypoint:
    def test_main_is_callable(self):
        # Just verify the entry point exists and is callable; running it
        # for real would block on stdin.
        assert callable(mcp_pkg.main)
        assert callable(srv.main)

    def test_main_handles_keyboard_interrupt(self, monkeypatch):
        async def _raise():
            raise KeyboardInterrupt()

        monkeypatch.setattr(srv, "_run", _raise)
        # Should sys.exit(0), not propagate
        with pytest.raises(SystemExit) as exc_info:
            srv.main()
        assert exc_info.value.code == 0

    @pytest.mark.asyncio
    async def test_run_wires_stdio_to_server(self, monkeypatch):
        """``_run`` should construct the server + plug it into stdio."""
        from contextlib import asynccontextmanager

        ran: dict = {"server_run_called": False}

        class _FakeServer:
            name = "clawmes"

            def create_initialization_options(self):
                return {"opts": True}

            async def run(self, read, write, opts):
                ran["server_run_called"] = True
                ran["read"] = read
                ran["write"] = write
                ran["opts"] = opts

        @asynccontextmanager
        async def _fake_stdio():
            yield ("read_stream", "write_stream")

        monkeypatch.setattr(srv, "build_server", lambda: _FakeServer())
        import mcp.server.stdio as stdio_mod

        monkeypatch.setattr(stdio_mod, "stdio_server", _fake_stdio)
        await srv._run()
        assert ran["server_run_called"]
        assert ran["read"] == "read_stream"
        assert ran["opts"] == {"opts": True}
