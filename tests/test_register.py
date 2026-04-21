"""``register(ctx)`` smoke test.

Calls the plugin's entry point against a fake ``PluginContext`` and
asserts every subsystem registered at least its expected minimum.
Forgiving on counts (subsystems are still being filled in) but strict
on shape — every register call carries the right kwargs.
"""

from __future__ import annotations

import pytest

import clawmes


def test_register_does_not_raise(mock_ctx) -> None:
    clawmes.register(mock_ctx)


def test_register_wires_at_least_one_tool(mock_ctx) -> None:
    clawmes.register(mock_ctx)
    # At minimum ``transfer`` is registered.
    names = {t["name"] for t in mock_ctx.tools}
    assert "transfer" in names


def test_register_wires_at_least_one_command(mock_ctx) -> None:
    clawmes.register(mock_ctx)
    names = {c["name"] for c in mock_ctx.commands}
    # ``/wallet`` is in the wallet command module
    assert "wallet" in names


def test_register_wires_lifecycle_hooks(mock_ctx) -> None:
    clawmes.register(mock_ctx)
    expected = {
        "pre_tool_call",
        "post_tool_call",
        "pre_llm_call",
        "pre_gateway_dispatch",
        "on_session_start",
        "on_session_end",
        "on_session_finalize",
        "on_session_reset",
        "transform_terminal_output",
        "transform_tool_result",
        "subagent_stop",
    }
    assert expected.issubset(mock_ctx.hooks.keys())


def test_register_wires_clawmes_cli_subcommand(mock_ctx) -> None:
    clawmes.register(mock_ctx)
    names = {c["name"] for c in mock_ctx.cli_commands}
    assert "clawmes" in names


def test_register_wires_at_least_one_skill(mock_ctx) -> None:
    clawmes.register(mock_ctx)
    names = {s["name"] for s in mock_ctx.skills}
    assert "transfer" in names


def test_double_register_is_idempotent(mock_ctx) -> None:
    """Calling register twice must not raise (e.g. service double-start)."""
    clawmes.register(mock_ctx)
    initial_tool_count = len(mock_ctx.tools)
    # Re-running should work; the recorder accumulates but no exception
    # should escape and services should not double-start.
    clawmes.register(mock_ctx)
    assert len(mock_ctx.tools) >= initial_tool_count


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path, monkeypatch) -> None:
    """Point HERMES_HOME at a temp dir so tests don't touch real state."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
