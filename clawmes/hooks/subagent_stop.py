"""``subagent_stop`` hook — observer for completed subagent runs.

Fires once per child after ``delegate_task`` completes. Carries:

  * ``parent_session_id``
  * ``child_role``
  * ``child_summary``
  * ``child_status`` — ``ok`` | ``timeout`` | ``error`` | ``cancelled``
  * ``duration_ms``

Used for orchestration metrics and to roll up child-spawned ledger
entries into the parent session's view.
"""

from __future__ import annotations

from typing import Any

from clawmes.lib.logger import logger_for

_log = logger_for("hooks.subagent_stop")


def callback(
    *,
    parent_session_id: str | None = None,
    child_role: str | None = None,
    child_summary: str | None = None,
    child_status: str | None = None,
    duration_ms: float | None = None,
    **kwargs: Any,
) -> None:
    _log.info(
        "subagent stop: parent=%s role=%s status=%s duration=%.0fms",
        parent_session_id,
        child_role,
        child_status,
        duration_ms or 0,
    )
