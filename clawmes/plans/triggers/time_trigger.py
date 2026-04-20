"""Time triggers — cron + interval based.

Trigger schema:

.. code-block:: json

    {
      "type": "time",
      "schedule": "every 1h" | "0 9 * * *",
      "next_at":  "2026-04-30T09:00:00Z"
    }

``next_at`` is computed by :func:`clawmes.lib.time.parse_schedule` and
stamped on the trigger doc so the scheduler can do an O(1) compare per
tick.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


def evaluate(trigger: dict[str, Any], now: datetime) -> bool:
    """Return True if the time trigger should fire at ``now``."""
    next_at_str = trigger.get("next_at")
    if not next_at_str:
        return False
    try:
        next_at = datetime.fromisoformat(next_at_str.replace("Z", "+00:00"))
    except ValueError:
        return False
    return now >= next_at
