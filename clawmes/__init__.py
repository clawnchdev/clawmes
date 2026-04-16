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

from clawmes import persona, services, tools
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
    try:
        _log.info("clawmes %s registering with Hermes", __version__)

        # 1. First-run setup
        persona.ensure_soul_md()

        # 2. Register surface (each subsystem's register_all is a stub
        # until later milestones; safe to call now).
        tools.register_all(ctx)

        # 3. Background services
        services.start_all()

        # 4. Cleanup
        atexit.register(services.stop_all)
        _install_signal_handlers()

        _log.info("clawmes register() complete")
    except Exception:
        _log.exception("clawmes register() failed; plugin will be disabled")
        raise


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
