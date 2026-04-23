"""Clawmes — Hermes Agent for crypto.

A crypto-native plugin for Hermes Agent. Registers tools, commands, hooks,
skills, and CLI subcommands via Hermes' standard ``register(ctx)`` plugin
contract.

The ``register`` function is the single entry point invoked by
``hermes_cli.plugins.PluginManager`` at process startup. It is sync and must
return quickly. Heavy work (RPC warmup, key validation) is deferred to first
use or to background threads started by ``services.start_all()``.
"""

from __future__ import annotations

import atexit
import signal

from clawmes import (
    cli,
    commands,
    hooks,
    persona,
    services,
    skills,
    tools,
)
from clawmes._version import __version__
from clawmes.lib.logger import logger_for

__all__ = ["__version__", "register"]

_log = logger_for("plugin")


def register(ctx) -> None:
    """Plugin entry point called by Hermes at startup.

    Stages, in order:

    1. **Idempotent first-run setup.** ``persona.ensure_soul_md()`` copies
       the bundled SOUL.md into ``${HERMES_HOME}/SOUL.md`` if absent. Never
       overwrites a user-edited file.
    2. **Surface registration.** Tools, commands, hooks, skills, and CLI
       subcommands are wired through the corresponding ``register_all(ctx)``
       calls. Each subsystem's stub is filled in across subsequent
       milestones; calling them now is a no-op except for ``tools`` once
       individual tool modules land.
    3. **Service start.** ``services.start_all()`` brings up wallet, RPC,
       price, plan-scheduler, etc. in topological order.
    4. **Cleanup hooks.** ``atexit`` + SIGTERM/SIGINT handlers ensure
       ``services.stop_all()`` runs on graceful shutdown.

    Errors during ``register()`` are caught and logged so a clawmes failure
    never crashes Hermes' boot — the plugin manager will mark the plugin
    disabled and surface the error in ``hermes plugins list``.
    """
    _log.info("clawmes %s registering with Hermes", __version__)

    # 1. First-run setup. If this fails we still continue to surface
    # registration — a missing SOUL.md is a degraded state, not a hard
    # failure.
    _safe("persona.ensure_soul_md", persona.ensure_soul_md)

    # 2. Register surface — each subsystem is isolated so a buggy
    # commands module can't take down tools, hooks, skills, or the CLI.
    # Hermes shows a partial-feature plugin instead of a fully-disabled
    # one.
    _safe("tools.register_all", tools.register_all, ctx)
    _safe("commands.register_all", commands.register_all, ctx)
    _safe("hooks.register_all", hooks.register_all, ctx)
    _safe("skills.register_all", skills.register_all, ctx)
    _safe("cli.register_all", cli.register_all, ctx)

    # 3. Background services. start_all itself wraps each service in
    # try/except, so partial failure here means partial feature loss
    # rather than total plugin failure.
    _safe("services.start_all", services.start_all)

    # 4. Cleanup
    try:
        atexit.register(services.stop_all)
        _install_signal_handlers()
    except Exception:
        _log.exception("failed to install cleanup hooks")

    _log.info("clawmes register() complete")


def _safe(label: str, fn, *args, **kwargs) -> None:
    """Run ``fn(*args, **kwargs)`` and log any exception without re-raising.

    Used to isolate register-time failures: a broken module in one
    subsystem (e.g. ``commands``) shouldn't disable every other
    subsystem. If something fails here the user gets a partially-
    functional plugin and a clear log entry instead of a fully-
    disabled plugin and a stack trace in ``hermes plugins list``.
    """
    try:
        fn(*args, **kwargs)
    except Exception:
        _log.exception("clawmes register: %s failed; continuing", label)


def _install_signal_handlers() -> None:
    """Attach SIGTERM/SIGINT handlers for clean service shutdown.

    Falls through to whatever Hermes already installed — we re-raise after
    stopping our own services so the parent's handlers still fire.
    """
    for sig in (signal.SIGTERM, signal.SIGINT):
        prev = signal.getsignal(sig)

        def _handler(signum, frame, _prev=prev):
            try:
                services.stop_all()
            finally:
                if callable(_prev) and _prev not in (signal.SIG_DFL, signal.SIG_IGN):
                    _prev(signum, frame)

        try:
            signal.signal(sig, _handler)
        except ValueError:
            # Signal handlers can only be set in the main thread — Hermes
            # may have already done this, fine to skip.
            pass
