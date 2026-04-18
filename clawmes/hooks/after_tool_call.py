"""``post_tool_call`` hook — observer for completed tool calls.

Side effects layered here:

  1. **Cost-basis ingest** — swap results auto-record to FIFO ledger.
  2. **Tx ledger append** — every write tool's tx hash + receipt logged.
  3. **Budget tracking** — per-tool + per-session cost roll-up.
  4. **Onboarding step advance** — the onboarding state machine reads
     ``post_tool_call`` events to know when a setup tool ran.
  5. **Skill evolution nudges** — if a session has used the same tool
     5+ times with similar args, prompt the user about creating a
     macro / saved query.

All currently stubs. Real wiring lands once the corresponding services
are ready.
"""

from __future__ import annotations

from typing import Any

from clawmes.lib.logger import logger_for

_log = logger_for("hooks.post_tool_call")


def callback(
    *,
    tool_name: str,
    args: dict[str, Any] | None,
    result: str | None,
    duration_ms: float | None = None,
    error: BaseException | None = None,
    **kwargs: Any,
) -> None:
    """Pure observer — Hermes ignores the return value."""
    if error is not None:
        _log.warning(
            "tool %s failed in %.0fms: %s",
            tool_name,
            duration_ms or 0,
            error,
        )
        return
    _log.debug("tool %s completed in %.0fms", tool_name, duration_ms or 0)
    # TODO(v0.1.0): cost basis, tx ledger, budget, onboarding, evolution
