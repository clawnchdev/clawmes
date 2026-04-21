"""Cron-expression parsing and time helpers used by the plan scheduler.

Two flavors of schedule strings are accepted:

  * Human form — ``every 1h``, ``every 30m``, ``every 1d``, ``every 5s``
  * Standard cron — ``0 9 * * *`` (5 fields: m h dom mon dow)

Both compile to a ``Schedule`` object that knows how to compute
``next_after(now)``. The actual wall-clock comparisons happen in
``clawmes/plans/scheduler.py``; this module is the parser only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

_HUMAN_RE = re.compile(
    r"^\s*every\s+(\d+)\s*(s|m|h|d)\s*$",
    re.IGNORECASE,
)
_CRON_RE = re.compile(r"^\s*\S+\s+\S+\s+\S+\s+\S+\s+\S+\s*$")

_UNIT_TO_SECONDS = {
    "s": 1,
    "m": 60,
    "h": 60 * 60,
    "d": 60 * 60 * 24,
}


@dataclass(frozen=True)
class Schedule:
    """A compiled, immutable schedule.

    ``kind`` is ``"interval"`` or ``"cron"``.

    For ``interval``, ``seconds`` is the period.
    For ``cron``, ``cron_expr`` is the original 5-field expression.
    """

    kind: str
    seconds: int = 0
    cron_expr: str = ""

    def next_after(self, now: datetime | None = None) -> datetime:
        """Return the next fire time strictly after ``now`` (default: UTC now)."""
        now = now or datetime.now(tz=UTC)
        if self.kind == "interval":
            return now + timedelta(seconds=self.seconds)
        if self.kind == "cron":
            try:
                from croniter import croniter  # type: ignore[import-not-found]
            except ImportError:
                raise RuntimeError(
                    "cron schedules require 'croniter' — pip install croniter"
                ) from None
            it = croniter(self.cron_expr, now)
            return it.get_next(datetime)  # type: ignore[no-any-return]
        raise ValueError(f"Unknown schedule kind: {self.kind!r}")


def parse_schedule(text: str) -> Schedule:
    """Parse a human or cron schedule string into a :class:`Schedule`."""
    s = text.strip()
    if not s:
        raise ValueError("Empty schedule expression")

    m = _HUMAN_RE.match(s)
    if m:
        n, unit = int(m.group(1)), m.group(2).lower()
        if n <= 0:
            raise ValueError(f"Schedule period must be > 0: {text!r}")
        return Schedule(kind="interval", seconds=n * _UNIT_TO_SECONDS[unit])

    if _CRON_RE.match(s):
        return Schedule(kind="cron", cron_expr=s)

    raise ValueError(
        f"Could not parse schedule: {text!r}. "
        "Use either 'every <N><unit>' (e.g. 'every 1h') or "
        "a 5-field cron expression."
    )


def humanize_seconds(seconds: int) -> str:
    """Render a duration in the smallest sensible unit."""
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 60 * 60:
        return f"{seconds // 60}m"
    if seconds < 60 * 60 * 24:
        return f"{seconds // (60 * 60)}h"
    return f"{seconds // (60 * 60 * 24)}d"
