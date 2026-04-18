"""Hermes lifecycle hook subscriptions.

Each module under ``clawmes/hooks/`` exposes a single public callback
function and a ``register(ctx)`` helper. :func:`register_all` wires every
callback into ``ctx.register_hook(name, callback)``.

Names match Hermes' :data:`hermes_cli.plugins.VALID_HOOKS`. Return
contracts are documented in ``HERMES_PARITY.md``.
"""

from __future__ import annotations

from clawmes.hooks import (
    after_tool_call,
    on_session,
    pre_gateway_dispatch,
    pre_tool_call,
    prompt_builder,
    subagent_stop,
    transform_terminal_output,
    transform_tool_result,
)

__all__ = ["register_all"]


def register_all(ctx) -> None:
    """Register every clawmes hook with Hermes."""
    ctx.register_hook("pre_tool_call", pre_tool_call.callback)
    ctx.register_hook("post_tool_call", after_tool_call.callback)
    ctx.register_hook("pre_llm_call", prompt_builder.callback)
    ctx.register_hook("pre_gateway_dispatch", pre_gateway_dispatch.callback)
    ctx.register_hook("on_session_start", on_session.on_start)
    ctx.register_hook("on_session_end", on_session.on_end)
    ctx.register_hook("on_session_finalize", on_session.on_finalize)
    ctx.register_hook("on_session_reset", on_session.on_reset)
    ctx.register_hook("transform_terminal_output", transform_terminal_output.callback)
    ctx.register_hook("transform_tool_result", transform_tool_result.callback)
    ctx.register_hook("subagent_stop", subagent_stop.callback)
