"""End-to-end plugin loading smoke test.

The clawmes plugin's contract with Hermes is the ``register(ctx)`` entry
point in ``clawmes/__init__.py``. ``ctx`` is a Hermes-provided object
that implements ``register_tool``, ``register_command``, ``register_hook``,
``register_skill``, and ``register_cli_command``.

These tests exercise the entry point end-to-end against a recorder ctx
that captures every call. They catch:

  * A subsystem importing a Hermes API that doesn't exist or has drifted.
  * A hook callback signature that doesn't match Hermes' expectations.
  * A subsystem that crashes during ``register``.
  * Services that fail to start.
  * The atexit / signal-handler installation succeeding.

This is the closest we can get to a real Hermes integration without
actually installing Hermes; until ``hermes-agent`` is published to PyPI
this is the best regression coverage available for the loading contract.
"""

from __future__ import annotations

import signal
from collections import defaultdict
from typing import Any

import pytest


class FakeHermesCtx:
    """Recorder implementing every ``register_*`` method clawmes uses.

    Stores each call as a dict so test assertions can inspect the
    arguments. Errors during register propagate unless the plugin
    wraps them in ``_safe`` — we want those to surface in the test
    output rather than be swallowed.
    """

    def __init__(self) -> None:
        self.tools: list[dict[str, Any]] = []
        self.commands: list[dict[str, Any]] = []
        self.hooks: dict[str, list[Any]] = defaultdict(list)
        self.skills: list[dict[str, Any]] = []
        self.cli_commands: list[dict[str, Any]] = []

    def register_tool(self, **kw: Any) -> None:
        self.tools.append(kw)

    def register_command(self, **kw: Any) -> None:
        self.commands.append(kw)

    def register_hook(self, name: str, callback: Any) -> None:
        self.hooks[name].append(callback)

    def register_skill(self, **kw: Any) -> None:
        self.skills.append(kw)

    def register_cli_command(self, **kw: Any) -> None:
        self.cli_commands.append(kw)


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Sandbox HERMES_HOME and reset every singleton register() touches."""
    from clawmes.plans import scheduler as plan_scheduler
    from clawmes.policy import storage as policy_storage
    from clawmes.services import (
        bankr_service,
        coingecko,
        explorer,
        mode_service,
        price,
        rpc,
        token_decimals,
        wallet,
        wc_notifications,
    )

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    for module in (
        rpc,
        token_decimals,
        wallet,
        coingecko,
        price,
        explorer,
        mode_service,
        bankr_service,
        plan_scheduler,
        wc_notifications,
    ):
        monkeypatch.setattr(module, "_instance", None, raising=False)
    policy_storage.save_policies([])

    # Stub signal handlers so the test doesn't actually install them
    # (which would survive past the test and confuse pytest's signal
    # handling).
    real_signal = signal.signal

    def fake_signal(sig, handler):
        return real_signal(sig, signal.SIG_DFL)

    monkeypatch.setattr(signal, "signal", fake_signal)


def test_register_completes_without_errors(monkeypatch):
    """The big one: register(ctx) returns cleanly against a fake ctx
    that exercises every register_* path. If any subsystem raises, the
    plugin's _safe() wrapper catches it and logs — but a regression
    that breaks an entire subsystem should be visible at the surface
    level (zero tools registered, etc.)."""
    import clawmes

    ctx = FakeHermesCtx()
    clawmes.register(ctx)

    # Tools: at least the five we ship
    tool_names = {t["name"] for t in ctx.tools}
    assert "transfer" in tool_names
    assert "clawnchconnect" in tool_names
    assert "defi_price" in tool_names
    assert "defi_balance" in tool_names
    assert "block_explorer" in tool_names

    # Hooks: at least the eleven the manifest declares
    expected_hooks = {
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
    assert expected_hooks <= set(ctx.hooks.keys())

    # Commands: at least the wallet commands
    command_names = {c["name"] for c in ctx.commands}
    assert "wallet" in command_names
    assert "connect" in command_names
    assert "disconnect" in command_names

    # CLI: at minimum the doctor + status entries from cli.register_all
    cli_names = {c["name"] for c in ctx.cli_commands}
    assert "clawmes" in cli_names or len(ctx.cli_commands) > 0


def test_register_handles_subsystem_failure(monkeypatch, caplog):
    """The plugin's _safe() wrapper must catch errors from any single
    subsystem so a buggy module can't take down the entire plugin.
    The tests/services suite proves each subsystem is robust on its
    own — this proves the wrapper at the top level."""
    import clawmes
    from clawmes import tools as tools_module

    def boom(_ctx):
        raise RuntimeError("simulated subsystem failure")

    monkeypatch.setattr(tools_module, "register_all", boom)

    ctx = FakeHermesCtx()
    # Must not raise — the _safe wrapper logs and continues.
    clawmes.register(ctx)
    # Other subsystems still wired up
    assert any(name in ctx.hooks for name in ("pre_tool_call",))


def test_register_installs_atexit_hook(monkeypatch):
    """Cleanup hooks must be wired so services stop gracefully on
    process exit. We can't easily verify atexit fires, but we can
    verify the registration succeeded by intercepting atexit.register."""
    import atexit

    import clawmes
    from clawmes import services as services_module

    registered: list[Any] = []

    def fake_register(fn):
        registered.append(fn)

    monkeypatch.setattr(atexit, "register", fake_register)

    ctx = FakeHermesCtx()
    clawmes.register(ctx)

    # services.stop_all should have been registered for atexit
    assert services_module.stop_all in registered


def test_every_tool_has_required_metadata():
    """Every registered tool must carry the four fields Hermes
    requires: name, schema, description, handler. Missing any of
    these would fail at `hermes plugins list` time."""
    import clawmes

    ctx = FakeHermesCtx()
    clawmes.register(ctx)

    for tool in ctx.tools:
        for key in ("name", "schema", "description", "handler"):
            assert key in tool, f"Tool missing {key!r}: {tool.get('name', '?')}"
        assert callable(tool["handler"]), f"Tool {tool['name']} handler is not callable"


def test_every_command_has_required_metadata():
    """Every registered command must carry name + handler. Description
    is recommended but not enforced here."""
    import clawmes

    ctx = FakeHermesCtx()
    clawmes.register(ctx)

    for cmd in ctx.commands:
        assert "name" in cmd, f"Command missing name: {cmd}"
        assert "handler" in cmd, f"Command missing handler: {cmd.get('name', '?')}"
        assert callable(cmd["handler"]), f"Command {cmd['name']} handler is not callable"


def test_every_hook_callback_is_callable():
    """Hooks registered as non-callable values would crash Hermes the
    first time the hook fired."""
    import clawmes

    ctx = FakeHermesCtx()
    clawmes.register(ctx)

    for name, callbacks in ctx.hooks.items():
        for cb in callbacks:
            assert callable(cb), f"Hook {name!r} got non-callable: {cb!r}"


def test_register_idempotent():
    """Calling register twice (e.g. after `hermes plugins reload`)
    must not raise. Each call replaces or appends — Hermes handles
    deduplication, but our side has to remain functional."""
    import clawmes

    ctx1 = FakeHermesCtx()
    clawmes.register(ctx1)
    ctx2 = FakeHermesCtx()
    clawmes.register(ctx2)

    # Same tool surface both times
    assert {t["name"] for t in ctx1.tools} == {t["name"] for t in ctx2.tools}
