"""Command-history service — ring buffer of recent slash-command calls.

The LLM doesn't see slash commands that the *user* runs in chat — they
bypass the agent loop entirely. So when a user runs ``/balance`` and
then asks the agent "what's my balance?" the agent re-runs the lookup
because it has no context of what just happened.

This service holds a per-session ring buffer of recent slash command
calls and their result summaries. The ``pre_llm_call`` hook reads it
and injects a compact recap into the per-turn user message context.
Net effect: the agent doesn't re-ask things the user already answered
via slash.

Recording happens explicitly — command handlers that want to be
remembered call :func:`record_command_call` themselves. We chose this
over global wrapping at registration time because:

  * It's transparent — a reader of the command source can see "this
    appears in history."
  * Sensitive operations (``/export_wallet`` — surfaces mnemonics)
    can deliberately *not* record themselves. Same for ``/recover``.
  * It avoids monkey-patching ``ctx.register_command``.

State is in-memory only (matches ``persona_service`` and
``mode_service``). The ring auto-evicts older entries beyond the
configured cap (default 20). Result summaries are truncated to keep
the prompt-cache impact bounded.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any

from clawmes.lib.logger import logger_for
from clawmes.services._base import Service

_log = logger_for("services.command_history")

_DEFAULT_RING_SIZE = 20
_DEFAULT_SUMMARY_CHARS = 240


class CommandHistoryService(Service):
    id = "clawmes.command_history"

    def __init__(
        self,
        *,
        ring_size: int = _DEFAULT_RING_SIZE,
        summary_chars: int = _DEFAULT_SUMMARY_CHARS,
    ) -> None:
        self._lock = threading.Lock()
        self._summary_chars = summary_chars
        self._entries: deque[dict[str, Any]] = deque(maxlen=ring_size)

    def start(self) -> None:
        pass

    def stop(self) -> None:
        with self._lock:
            self._entries.clear()

    def record(self, name: str, args: str, result: str) -> None:
        """Append a recent-command entry.

        ``name`` is the canonical slash command name (no leading ``/``).
        ``args`` is the raw argument string as received by the handler.
        ``result`` is the handler's return value; truncated to keep the
        ring footprint bounded.
        """
        if not isinstance(name, str) or not name.strip():
            return  # silently ignore malformed records
        truncated_result = self._truncate(result if isinstance(result, str) else str(result))
        entry = {
            "timestamp": time.time(),
            "name": name.strip().lstrip("/"),
            "args": (args.strip() if isinstance(args, str) else "")[: self._summary_chars],
            "summary": truncated_result,
        }
        with self._lock:
            self._entries.append(entry)

    def recent(self, limit: int = 10) -> list[dict[str, Any]]:
        """Return up to ``limit`` most-recent entries, newest first."""
        if limit <= 0:
            return []
        with self._lock:
            snapshot = list(self._entries)
        return snapshot[-limit:][::-1]

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def _truncate(self, text: str) -> str:
        if len(text) <= self._summary_chars:
            return text
        return text[: self._summary_chars - 3].rstrip() + "..."


_instance: CommandHistoryService | None = None


def get_command_history_service() -> CommandHistoryService:
    global _instance
    if _instance is None:
        _instance = CommandHistoryService()
    return _instance


def record_command_call(name: str, args: str, result: str) -> None:
    """Module-level convenience wrapper used by command handlers."""
    try:
        get_command_history_service().record(name, args, result)
    except Exception:  # noqa: BLE001 — recording must never break a command
        _log.exception("command_history record failed for %r", name)
