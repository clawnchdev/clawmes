"""Argument-parsing helpers for tool handlers.

LLMs occasionally pass arguments with the wrong type (string instead of
number, ``"true"`` instead of ``true``, etc.). These helpers coerce
defensively and surface a uniform error path via ``ParamError``.

Tool handlers should always go through these helpers rather than indexing
into ``args`` directly — the gating decorator in ``clawmes/tools/registry``
catches ``ParamError`` and converts to a friendly error tool result without
re-raising.
"""

from __future__ import annotations

from typing import Any


class ParamError(ValueError):
    """Raised when a required arg is missing or fails type coercion."""


def read_str(args: dict[str, Any], key: str, *, required: bool = False) -> str | None:
    """Read a string arg, coercing common LLM inputs."""
    value = args.get(key)
    if value is None or value == "":
        if required:
            raise ParamError(f"Missing required parameter: {key!r}")
        return None
    if isinstance(value, str):
        return value
    return str(value)


def read_int(args: dict[str, Any], key: str, *, required: bool = False) -> int | None:
    """Read an int arg, accepting numeric strings."""
    value = args.get(key)
    if value is None or value == "":
        if required:
            raise ParamError(f"Missing required parameter: {key!r}")
        return None
    if isinstance(value, bool):
        # bools are ints in Python; reject explicitly to avoid silent confusion
        raise ParamError(f"{key!r} must be an integer, got bool")
    if isinstance(value, int):
        return value
    try:
        return int(str(value), 10)
    except ValueError as exc:
        raise ParamError(f"{key!r} must be an integer: {value!r}") from exc


def read_float(args: dict[str, Any], key: str, *, required: bool = False) -> float | None:
    """Read a float arg, accepting numeric strings."""
    value = args.get(key)
    if value is None or value == "":
        if required:
            raise ParamError(f"Missing required parameter: {key!r}")
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except ValueError as exc:
        raise ParamError(f"{key!r} must be a number: {value!r}") from exc


def read_bool(args: dict[str, Any], key: str, *, default: bool = False) -> bool:
    """Read a bool arg, accepting common stringified forms."""
    value = args.get(key)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def read_enum(
    args: dict[str, Any],
    key: str,
    choices: list[str],
    *,
    required: bool = False,
) -> str | None:
    """Read a string arg constrained to ``choices``."""
    value = read_str(args, key, required=required)
    if value is None:
        return None
    if value not in choices:
        raise ParamError(f"{key!r} must be one of {choices!r}, got {value!r}")
    return value


def read_list(args: dict[str, Any], key: str, *, default: list | None = None) -> list:
    """Read a list arg. If a string is passed, splits on comma."""
    value = args.get(key)
    if value is None:
        return list(default) if default is not None else []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    raise ParamError(f"{key!r} must be a list, got {type(value).__name__}")
