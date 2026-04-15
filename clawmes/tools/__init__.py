"""Tool implementations.

Each tool lives in its own module (``transfer.py``, ``defi_swap.py``, ...) and
exports a ``register(ctx)`` function that wires the tool into Hermes via
``ctx.register_tool``. The decorators in :mod:`clawmes.tools.registry`
generate the metadata that ``register`` consumes.

Top-level :func:`register_all` is called from the plugin's ``register(ctx)``
entry point in ``clawmes/__init__.py``.
"""

from __future__ import annotations

from clawmes.tools.registry import (
    WRITE_TOOL_NAMES,
    read_tool,
    write_tool,
)

__all__ = ["WRITE_TOOL_NAMES", "read_tool", "register_all", "write_tool"]


def register_all(ctx) -> None:
    """Register every clawmes tool with the Hermes plugin context.

    Stub at this milestone — individual tool modules are added in subsequent
    commits and imported here as they land. Order matters: registration
    order = order in the LLM-visible tool catalog. Most-used tools first.
    """
    # TODO(v0.1.0): import tool modules and call ``mod.register(ctx)`` in
    # the order documented in PRD §8.12.
    _ = ctx
