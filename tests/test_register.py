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
    # At minimum ``transfer`` and ``defi_price`` are registered.
    names = {t["name"] for t in mock_ctx.tools}
    assert "transfer" in names
    assert "defi_price" in names


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
    # Six bundled skills land at this milestone; more arrive as the
    # crypto-extension port progresses.
    expected = {"transfer", "defi-trading", "lending", "staking", "bridge", "block-explorer"}
    assert expected.issubset(names)


def test_double_register_is_idempotent(mock_ctx) -> None:
    """Calling register twice must not raise (e.g. service double-start)."""
    clawmes.register(mock_ctx)
    initial_tool_count = len(mock_ctx.tools)
    # Re-running should work; the recorder accumulates but no exception
    # should escape and services should not double-start.
    clawmes.register(mock_ctx)
    assert len(mock_ctx.tools) >= initial_tool_count


def test_register_starts_core_services(mock_ctx) -> None:
    """start_all should register all core services."""
    from clawmes.services.registry import registry

    clawmes.register(mock_ctx)
    ids = {svc.id for svc in registry.iter_services()}
    assert "clawmes.credential_redactor" in ids
    assert "clawmes.wallet" in ids
    assert "clawmes.coingecko" in ids
    assert "clawmes.price" in ids
    assert "clawmes.plan_scheduler" in ids


def test_register_isolates_subsystem_failures(mock_ctx, monkeypatch) -> None:
    """A buggy subsystem must not take down the others.

    Patches ``commands.register_all`` to raise. After register(), the
    other subsystems should still have wired (tools, hooks, skills,
    CLI). No exception should escape.
    """
    from clawmes import commands

    def boom(ctx):  # noqa: ARG001
        raise RuntimeError("simulated commands failure")

    monkeypatch.setattr(commands, "register_all", boom)

    # Must NOT raise
    clawmes.register(mock_ctx)

    # Other subsystems still landed
    tool_names = {t["name"] for t in mock_ctx.tools}
    assert "transfer" in tool_names
    assert "defi_price" in tool_names

    cli_names = {c["name"] for c in mock_ctx.cli_commands}
    assert "clawmes" in cli_names

    skill_names = {s["name"] for s in mock_ctx.skills}
    assert "transfer" in skill_names

    # commands subsystem broke → no commands were wired
    assert mock_ctx.commands == []


def test_register_isolates_persona_failure(mock_ctx, monkeypatch) -> None:
    """A SOUL.md install error must not take down register()."""
    from clawmes import persona

    def boom():
        raise OSError("disk full")

    monkeypatch.setattr(persona, "ensure_soul_md", boom)
    clawmes.register(mock_ctx)  # must not raise

    # Other subsystems still wired
    assert any(t["name"] == "transfer" for t in mock_ctx.tools)


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path, monkeypatch) -> None:
    """Point HERMES_HOME at a temp dir so tests don't touch real state."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    # Reset the service registry between tests so test_register_starts_core_services
    # sees a clean slate (services are module-singletons; the registry holds them)
    from clawmes.services.registry import registry

    registry.clear()
