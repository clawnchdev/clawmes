"""``agent_memory`` — persistent memory delegated to Hermes' built-in memory.

Hermes ships with a memory provider system (plugins/memory). Clawmes
wraps the canonical interface so the LLM can drive memory operations
through a consistent tool name.

Four actions:

  * ``add``      — store a new memory entry.
  * ``replace``  — update an existing entry by key.
  * ``remove``   — delete an entry by key.
  * ``query``    — search for entries matching a query.

Memory backend resolution: clawmes uses Hermes' configured memory
provider (Hindsight by default). If Hermes isn't loaded (e.g. test
environment), the tool returns ``not_available`` so the LLM can
gracefully degrade rather than crash.
"""

from __future__ import annotations

from typing import Any

from clawmes.lib.logger import logger_for
from clawmes.lib.params import read_int, read_str
from clawmes.lib.tool_result import error_result, json_result
from clawmes.tools.registry import register_with_ctx, write_tool

_log = logger_for("tools.agent_memory")

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["add", "replace", "remove", "query"],
        },
        "key": {"type": "string"},
        "content": {"type": "string"},
        "query": {"type": "string"},
        "limit": {"type": "integer"},
        "policyConfirmationNonce": {"type": "string"},
    },
    "required": ["action"],
}


@write_tool(
    name="agent_memory",
    toolset="clawmes-misc",
    description=(
        "Persistent memory via Hermes' configured memory provider. "
        "add / replace / remove entries by key; query searches stored "
        "entries."
    ),
    schema=_SCHEMA,
    emoji="\U0001f9e0",
)
def agent_memory(args: dict[str, Any], **kwargs: Any) -> str:
    action = read_str(args, "action", required=True)
    if action in ("add", "replace", "remove"):
        from clawmes.services.evolution_mode import is_evolving

        if not is_evolving():
            return error_result(
                "Evolution mode is disabled. agent_memory write actions "
                "(add / replace / remove) require /evolve. Use /evolution "
                "to check status; the safe default is OFF so a "
                "prompt-injected LLM can't silently rewrite your memory.",
                code="evolution_gate",
            )
    provider = _resolve_provider()
    if provider is None:
        return error_result(
            "Hermes memory provider not configured. Set up a memory "
            "backend via `hermes plugins enable memory/<provider>` "
            "(hindsight, retaindb, openviking, holographic).",
            code="not_available",
        )

    try:
        if action == "add":
            key = read_str(args, "key", required=True)
            content = read_str(args, "content", required=True)
            result = provider.add(key, content)
        elif action == "replace":
            key = read_str(args, "key", required=True)
            content = read_str(args, "content", required=True)
            result = provider.replace(key, content)
        elif action == "remove":
            key = read_str(args, "key", required=True)
            result = provider.remove(key)
        else:
            query = read_str(args, "query", required=True)
            limit = read_int(args, "limit") or 10
            result = provider.query(query, limit=limit)
    except Exception as exc:  # noqa: BLE001
        return error_result(f"Memory provider raised: {exc}", code="provider_error")

    return json_result(
        {"action": action, "result": result},
        summary=f"agent_memory {action}",
    )


def _resolve_provider():
    """Return Hermes' active memory provider, or None if not loaded."""
    try:
        from plugins.memory import get_active_provider  # type: ignore[import-not-found]

        return get_active_provider()
    except ImportError:
        return None


def register(ctx) -> None:
    register_with_ctx(ctx, agent_memory)
