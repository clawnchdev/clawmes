"""DCA scheduler service — drives ``/dca tick`` automatically.

The ``/dca`` command provides a manual ``tick`` subcommand that runs
due schedules synchronously. Wiring that into the Hermes cron loop
turns DCA from a manual cron-curio into a real "set it and forget it"
feature. This service does exactly that: every service tick (60s by
default, configurable via Hermes cron) it dispatches due schedules.

Lifecycle:

  * ``start()`` — mark running; no-op otherwise (state lives in the
    /dca command module, not the service).
  * ``stop()``  — mark not running. Pending schedules wait for the
    next service start.
  * ``tick()``  — sync wrapper around the same execution path the
    ``/dca tick`` command uses. Safe to call from the registry tick
    loop because:
      - The dispatch path itself is sync (no asyncio required at the
        engine level — only the command interface is async).
      - Failures inside one schedule do not bubble up; we catch + log
        per-schedule so one bad token cannot stall the whole loop.

This service intentionally has no state of its own. All persistence
lives in ``${HERMES_HOME}/clawmes/dca/schedules.json`` (managed by the
``/dca`` command). The service is just the cron-driver wrapper.
"""

from __future__ import annotations

from typing import Any

from clawmes.lib.logger import logger_for
from clawmes.services._base import Service

_log = logger_for("services.dca_scheduler")

_SERVICE: DcaSchedulerService | None = None


class DcaSchedulerService(Service):
    """Cron-driven DCA execution loop."""

    id = "clawmes.dca_scheduler"
    ticking = True

    def __init__(self) -> None:
        self._running = False
        self._ticks = 0
        self._last_runs = 0
        self._total_runs = 0

    def start(self) -> None:
        self._running = True
        _log.info("dca scheduler started — tick cadence driven by registry")

    def stop(self) -> None:
        self._running = False
        _log.info("dca scheduler stopped (ticks=%d, total_runs=%d)", self._ticks, self._total_runs)

    def health(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": "running" if self._running else "stopped",
            "ticks": self._ticks,
            "last_runs": self._last_runs,
            "total_runs": self._total_runs,
        }

    def tick(self) -> None:
        """Fire any due DCA schedules. Catches all per-schedule errors."""
        if not self._running:
            return
        self._ticks += 1
        try:
            from clawmes.commands import dca

            n = dca._run_due_sync()
            self._last_runs = n
            self._total_runs += n
            if n > 0:
                _log.info("dca tick fired %d schedule(s)", n)
        except Exception:  # noqa: BLE001 — never let cron break the loop
            _log.exception("dca scheduler tick raised; swallowing")


def get_dca_scheduler_service() -> DcaSchedulerService:
    """Module-level singleton accessor — production code never re-instantiates."""
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = DcaSchedulerService()
    return _SERVICE


def _reset_for_tests() -> None:
    """Test hook to clear the singleton so each test starts fresh."""
    global _SERVICE
    _SERVICE = None
