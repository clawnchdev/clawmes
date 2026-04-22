"""Tool-result helpers.

Hermes tool handlers must return a JSON string. Clawmes carries an internal
shape inside that JSON envelope so tool callers (the LLM) and plan callers
(the executor) can both consume the same payload — the LLM reads ``content``,
the executor reads ``details`` for variable references.

The shape:

.. code-block:: json

    {
      "content": [{"type": "text", "text": "<human readable>"}],
      "details": {"<arbitrary>": "<machine readable>"},
      "isError": false
    }

``isError`` is omitted on success and set to ``true`` on failure, matching the
upstream OpenClaw tool-result convention so ported skill bundles continue to
read the same way.
"""

from __future__ import annotations

import json
from typing import Any


def text_result(text: str, *, details: Any = None) -> str:
    """Return a successful tool result wrapping a text body."""
    payload: dict[str, Any] = {
        "content": [{"type": "text", "text": text}],
    }
    if details is not None:
        payload["details"] = details
    return json.dumps(payload)


def json_result(data: dict[str, Any], *, summary: str | None = None) -> str:
    """Return a successful tool result whose details are a structured dict.

    The human-readable ``content`` is either ``summary`` (preferred) or a
    pretty-printed JSON dump of ``data``.
    """
    text = summary if summary is not None else json.dumps(data, indent=2, default=str)
    return json.dumps(
        {
            "content": [{"type": "text", "text": text}],
            "details": data,
        },
        default=str,
    )


def error_result(message: str, *, code: str | None = None) -> str:
    """Return an error tool result.

    ``code`` is a machine-readable error code (e.g. ``policy_block``,
    ``readonly_mode``, ``tool_error``). It is consumed by the post-tool-call
    hook for metric tagging and by the plan executor's retry policy.
    """
    details: dict[str, Any] | None = {"error_code": code} if code else None
    return json.dumps(
        {
            "content": [{"type": "text", "text": message}],
            "details": details,
            "isError": True,
        }
    )
