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

from clawmes._version import __version__

__all__ = ["__version__", "register"]


def register(ctx) -> None:
    """Plugin entry point called by Hermes at startup.

    Parameters
    ----------
    ctx
        ``hermes_cli.plugins.PluginContext`` instance. Provides
        ``register_tool``, ``register_command``, ``register_hook``,
        ``register_cli_command``, ``register_skill``, etc.

    Notes
    -----
    Implementation is a stub at this milestone. The real wiring lands in a
    later commit that ties together ``tools/``, ``commands/``, ``hooks/``,
    ``services/``, and ``cli/``.
    """
    # TODO(v0.1.0): wire register_all calls for each subsystem
    _ = ctx
