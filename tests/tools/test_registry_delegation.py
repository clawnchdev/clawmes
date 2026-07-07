"""Tests for the delegation stage (stage 3) of the write-tool gate."""

from __future__ import annotations

import json

import pytest

from clawmes.delegation.executor import DelegationExecutionResult
from clawmes.lib.tool_result import json_result
from clawmes.policy import storage as policy_storage
from clawmes.policy import usage_counter as usage_counter_module
from clawmes.policy.types import ActionContext
from clawmes.services import mode_service as mode_module
from clawmes.tools.registry import _try_delegation, write_tool


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(mode_module, "_instance", None)
    monkeypatch.setattr(usage_counter_module, "_instance", None)
    policy_storage.save_policies([])


def _ctx(tool="transfer"):
    return ActionContext(tool_name=tool, args={}, user_id="u", chain_id=8453)


class TestTryDelegationHelper:
    def test_executed_returns_success_result(self, monkeypatch):
        monkeypatch.setattr(
            "clawmes.delegation.executor.try_delegation_execution",
            lambda ctx, args: DelegationExecutionResult(True, tx_hash="0xabc", chain_id=8453),
        )
        out = _try_delegation(_ctx(), {})
        assert out is not None
        parsed = json.loads(out)
        assert parsed["details"]["delegated"] is True
        assert parsed["details"]["tx_hash"] == "0xabc"

    def test_error_fails_closed(self, monkeypatch):
        monkeypatch.setattr(
            "clawmes.delegation.executor.try_delegation_execution",
            lambda ctx, args: DelegationExecutionResult(False, error="over cap"),
        )
        out = _try_delegation(_ctx(), {})
        parsed = json.loads(out)
        assert parsed["isError"] is True
        assert parsed["details"]["error_code"] == "delegation_refused"

    def test_skip_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            "clawmes.delegation.executor.try_delegation_execution",
            lambda ctx, args: DelegationExecutionResult(False, skip_reason="no delegation"),
        )
        assert _try_delegation(_ctx(), {}) is None

    def test_executor_exception_falls_through(self, monkeypatch):
        def _boom(ctx, args):
            raise RuntimeError("executor blew up")

        monkeypatch.setattr("clawmes.delegation.executor.try_delegation_execution", _boom)
        assert _try_delegation(_ctx(), {}) is None

    def test_import_failure_falls_through(self, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def _fake_import(name, *args, **kwargs):
            if name == "clawmes.delegation.executor":
                raise ImportError("simulated missing module")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _fake_import)
        assert _try_delegation(_ctx(), {}) is None


class TestGateStage3Integration:
    def test_delegation_executes_skips_handler(self, monkeypatch):
        monkeypatch.setattr(
            "clawmes.delegation.executor.try_delegation_execution",
            lambda ctx, args: DelegationExecutionResult(True, tx_hash="0xdead", chain_id=8453),
        )

        @write_tool(name="transfer", toolset="t", description="d", schema={"type": "object"})
        def transfer(args, **kw):
            return json_result({"handler": "ran"})

        out = json.loads(transfer({"action": "send", "to": "0x" + "1" * 40, "amount": "1"}))
        assert out["details"]["delegated"] is True

    def test_records_invocation_on_delegated_execution(self, monkeypatch):
        monkeypatch.setattr(
            "clawmes.delegation.executor.try_delegation_execution",
            lambda ctx, args: DelegationExecutionResult(True, tx_hash="0xdead", chain_id=8453),
        )

        @write_tool(name="transfer2", toolset="t", description="d", schema={"type": "object"})
        def transfer(args, **kw):
            return json_result({"handler": "ran"})

        from clawmes.policy.usage_counter import get_usage_counter

        before = get_usage_counter().count("u", "transfer2")
        transfer({}, user_id="u")
        assert get_usage_counter().count("u", "transfer2") == before + 1

    def test_fall_through_runs_handler(self, monkeypatch):
        monkeypatch.setattr(
            "clawmes.delegation.executor.try_delegation_execution",
            lambda ctx, args: DelegationExecutionResult(False, skip_reason="n/a"),
        )

        @write_tool(name="transfer3", toolset="t", description="d", schema={"type": "object"})
        def transfer(args, **kw):
            return json_result({"handler": "ran"})

        out = json.loads(transfer({}))
        assert out["details"]["handler"] == "ran"
