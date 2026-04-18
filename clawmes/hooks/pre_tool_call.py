"""``pre_tool_call`` hook — block dangerous tool calls before they fire.

This is the rate-limit + dangerous-command-override surface. Returning
``{"action": "block", "message": ...}`` vetoes the call; the LLM sees the
message and is instructed to retry with safer parameters or ask the user.

Most policy work lives at the wallet layer (``@write_tool`` decorator)
because it has access to the action context. This hook is a coarser
filter — useful for rate limits, global kill switches, and emergency
overrides.
"""

from __future__ import annotations

from typing import Any

from clawmes.lib.logger import logger_for

_log = logger_for("hooks.pre_tool_call")


def callback(*, tool_name: str, args: dict[str, Any], **kwargs: Any) -> dict[str, str] | None:
    """Pre-tool-call observer.

    Currently a pass-through. Future work will plug in:
      * global rate limit (per-tool, per-minute)
      * kill switch via ``clawmes.kill_switch`` config flag
      * suspicious-pattern detection (e.g. tool storms after a session
        anomaly).
    """
    _log.debug("pre_tool_call: %s args_keys=%s", tool_name, list(args or {}))
    return None
