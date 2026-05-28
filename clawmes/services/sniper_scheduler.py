"""Sniper scheduler service — drives ``/sniper tick`` automatically.

Same shape as the DCA / copy / alerts / limit-order schedulers. Each
service tick pulls the Clawnch launches feed, matches each active
sniper config against the latest launches, and fires swaps via
``defi_swap`` for any matches.

Per-config errors caught internally so one bad config (e.g. a regex
that never matches anything weird) can't crash the loop.
"""

from __future__ import annotations

from typing import Any

from clawmes.lib.logger import logger_for
from clawmes.services._base import Service

_log = logger_for("services.sniper_scheduler")

_SERVICE: SniperSchedulerService | None = None


class SniperSchedulerService(Service):
    id = "clawmes.sniper_scheduler"
    ticking = True

    def __init__(self) -> None:
        self._running = False
        self._ticks = 0
        self._last_runs = 0
        self._total_runs = 0

    def start(self) -> None:
        self._running = True
        _log.info("sniper scheduler started — tick cadence driven by registry")

    def stop(self) -> None:
        self._running = False
        _log.info(
            "sniper scheduler stopped (ticks=%d, total_fired=%d)",
            self._ticks,
            self._total_runs,
        )

    def health(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": "running" if self._running else "stopped",
            "ticks": self._ticks,
            "last_runs": self._last_runs,
            "total_runs": self._total_runs,
        }

    def tick(self) -> None:
        if not self._running:
            return
        self._ticks += 1
        try:
            from clawmes.commands import sniper

            n = sniper._run_due_sync()
            self._last_runs = n
            self._total_runs += n
            if n > 0:
                _log.info("sniper tick fired %d snipe(s)", n)
        except Exception:  # noqa: BLE001
            _log.exception("sniper scheduler tick raised; swallowing")


def get_sniper_scheduler_service() -> SniperSchedulerService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = SniperSchedulerService()
    return _SERVICE


def _reset_for_tests() -> None:
    global _SERVICE
    _SERVICE = None
