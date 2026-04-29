"""``_user_tools`` — dispatcher for user-defined custom tools.

Users can extend clawmes with their own tools by dropping Python
modules in ``${HERMES_HOME}/clawmes/user_tools/``. This dispatcher
finds them at register time and forwards calls to the underlying
implementation.

Each user tool module must:

  * Live at ``${HERMES_HOME}/clawmes/user_tools/<tool_name>.py``.
  * Define a ``handler(args: dict, **kwargs) -> str`` function returning
    a JSON tool-result envelope.
  * Optionally define ``SCHEMA: dict`` and ``DESCRIPTION: str`` for
    Hermes-side metadata.

This dispatcher is the routing layer. Each user tool is registered
under its own name (not ``_user_tools``); this module is just the
discovery mechanism. Users invoking ``my_custom_tool`` via the LLM
hit this dispatcher, which forwards to ``user_tools/my_custom_tool.py:handler``.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from clawmes.lib.logger import logger_for
from clawmes.lib.paths import hermes_home
from clawmes.lib.tool_result import error_result, json_result
from clawmes.tools.registry import register_with_ctx, write_tool

_log = logger_for("tools._user_tools")

_GENERIC_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "tool_name": {
            "type": "string",
            "description": "Custom tool to invoke.",
        },
        "args": {"type": "object", "description": "Tool args."},
    },
    "required": ["tool_name"],
}


def _user_tools_dir() -> Path:
    return hermes_home() / "clawmes" / "user_tools"


def _load_handler(tool_name: str):
    path = _user_tools_dir() / f"{tool_name}.py"
    if not path.exists():
        return None
    # spec_from_file_location returns a valid spec for any existing
    # .py file — the None-check would only fire if path validation
    # above missed something (which it doesn't given the .py suffix).
    spec = importlib.util.spec_from_file_location(f"clawmes_user_{tool_name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    handler = getattr(module, "handler", None)
    return handler if callable(handler) else None


@write_tool(
    name="_user_tools",
    toolset="clawmes-misc",
    description=(
        "Dispatcher for user-defined custom tools. Drop a Python "
        "module at ${HERMES_HOME}/clawmes/user_tools/<name>.py with a "
        "handler(args, **kwargs) function and invoke it via this "
        "dispatcher. Useful for one-off custom logic that doesn't "
        "warrant a full plugin."
    ),
    schema=_GENERIC_SCHEMA,
    emoji="\U0001f527",
)
def _user_tools(args: dict[str, Any], **kwargs: Any) -> str:
    tool_name = args.get("tool_name")
    if not isinstance(tool_name, str) or not tool_name.strip():
        return error_result(
            "tool_name required (e.g. 'my_custom_tool')",
            code="param_error",
        )

    # Block the LLM from invoking dunders / dispatcher itself
    if tool_name.startswith("_") or tool_name == "_user_tools":
        return error_result(
            f"Invalid user tool name: {tool_name!r}",
            code="param_error",
        )

    handler = _load_handler(tool_name)
    if handler is None:
        return error_result(
            f"User tool {tool_name!r} not found at {_user_tools_dir() / (tool_name + '.py')}",
            code="not_found",
        )

    inner_args = args.get("args") or {}
    if not isinstance(inner_args, dict):
        inner_args = {}

    try:
        result = handler(inner_args, **kwargs)
    except Exception as exc:  # noqa: BLE001
        return error_result(f"User tool {tool_name!r} raised: {exc}", code="tool_error")

    # If handler returns a JSON string already, pass through; else wrap
    if isinstance(result, str):
        return result
    return json_result(
        {"tool_name": tool_name, "result": result},
        summary=f"user tool {tool_name} returned",
    )


def register(ctx) -> None:
    register_with_ctx(ctx, _user_tools)
