"""Tests for clawmes.tools.clawmes_info — the agent-callable read bridge."""

from __future__ import annotations

import json

import pytest

from clawmes.tools.clawmes_info import _OPS, clawmes_info, register


def _set_handler(monkeypatch, op, fn):
    """Monkeypatch the async handler that ``op`` dispatches to."""
    module_path, handler_name, _desc = _OPS[op]
    monkeypatch.setattr(f"{module_path}.{handler_name}", fn)


class TestDispatch:
    def test_dispatches_and_returns_output(self, monkeypatch):
        async def _stub(raw_args, *a, **k):
            return "WALLET STATUS OK"

        _set_handler(monkeypatch, "wallet", _stub)
        out = json.loads(clawmes_info({"op": "wallet"}))
        assert out["details"]["op"] == "wallet"
        assert out["details"]["output"] == "WALLET STATUS OK"
        assert out["content"][0]["text"] == "WALLET STATUS OK"
        assert "isError" not in out

    def test_passes_args_through(self, monkeypatch):
        async def _stub(raw_args, *a, **k):
            return f"researching {raw_args}"

        _set_handler(monkeypatch, "research", _stub)
        out = json.loads(clawmes_info({"op": "research", "args": "CLAWNCH"}))
        assert out["details"]["args"] == "CLAWNCH"
        assert "CLAWNCH" in out["details"]["output"]

    def test_op_is_normalized(self, monkeypatch):
        async def _stub(raw_args, *a, **k):
            return "ok"

        _set_handler(monkeypatch, "trending", _stub)
        out = json.loads(clawmes_info({"op": "  TRENDING  "}))
        assert out["details"]["op"] == "trending"

    def test_missing_args_defaults_empty(self, monkeypatch):
        captured = {}

        async def _stub(raw_args, *a, **k):
            captured["raw"] = raw_args
            return "ok"

        _set_handler(monkeypatch, "balance", _stub)
        clawmes_info({"op": "balance"})
        assert captured["raw"] == ""


class TestPreview:
    def test_preview_extracted_from_card_path(self, monkeypatch):
        async def _stub(raw_args, *a, **k):
            return "Report\n\nResearch card: /home/u/.hermes/clawmes/cards/research-FOO-1700000000.html\n"

        _set_handler(monkeypatch, "research", _stub)
        out = json.loads(clawmes_info({"op": "research", "args": "FOO"}))
        assert out["preview"] == "/home/u/.hermes/clawmes/cards/research-FOO-1700000000.html"

    def test_no_preview_when_no_card(self, monkeypatch):
        async def _stub(raw_args, *a, **k):
            return "just text, no card path here"

        _set_handler(monkeypatch, "scan", _stub)
        out = json.loads(clawmes_info({"op": "scan", "args": "0xabc"}))
        assert "preview" not in out


class TestErrors:
    def test_unknown_op(self):
        out = json.loads(clawmes_info({"op": "definitely_not_an_op"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "param_error"

    def test_op_required(self):
        # read_str(required=True) raises ParamError → read_tool maps to param_error
        out = json.loads(clawmes_info({}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "param_error"

    def test_handler_exception_is_caught(self, monkeypatch):
        async def _boom(raw_args, *a, **k):
            raise RuntimeError("handler blew up")

        _set_handler(monkeypatch, "wallet", _boom)
        out = json.loads(clawmes_info({"op": "wallet"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "tool_error"


class TestRunningLoopBranch:
    @pytest.mark.asyncio
    async def test_dispatch_from_running_loop(self, monkeypatch):
        # Inside an async test a loop is already running, so _run_coro must take
        # the worker-thread branch instead of asyncio.run.
        async def _stub(raw_args, *a, **k):
            return "VIA WORKER THREAD"

        _set_handler(monkeypatch, "wallet", _stub)
        out = json.loads(clawmes_info({"op": "wallet"}))
        assert out["details"]["output"] == "VIA WORKER THREAD"


class TestRegister:
    def test_registers_tool(self, mock_ctx):
        register(mock_ctx)
        names = [t["name"] for t in mock_ctx.tools]
        assert "clawmes_info" in names

    def test_schema_lists_all_ops(self):
        # The tool's own metadata enum should match the bridged op set.
        meta = clawmes_info._clawmes_meta
        assert set(meta["schema"]["properties"]["op"]["enum"]) == set(_OPS)
        assert meta["is_write"] is False
