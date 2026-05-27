"""Alerts scheduler service — drives ``/alerts tick`` automatically.

Same shape as ``DcaSchedulerService`` and ``CopyTraderService``. Each
service tick polls every active alert (price or wallet type), records
fires on the alert's history, and (for price alerts) flips the alert
to ``status="fired"`` so we don't repeatedly notify on subsequent
ticks after a threshold has been crossed.

Notification delivery (Telegram / Slack / etc.) happens via the
Hermes channel layer reading clawmes command history — this service
is responsible only for detecting fires and persisting them.
"""

from __future__ import annotations

from typing import Any

from clawmes.lib.logger import logger_for
from clawmes.services._base import Service

_log = logger_for("services.alerts_scheduler")

_SERVICE: AlertsSchedulerService | None = None


class AlertsSchedulerService(Service):
    id = "clawmes.alerts_scheduler"
    ticking = True

    def __init__(self) -> None:
        self._running = False
        self._ticks = 0
        self._last_runs = 0
        self._total_runs = 0

    def start(self) -> None:
        self._running = True
        _log.info("alerts scheduler started — tick cadence driven by registry")

    def stop(self) -> None:
        self._running = False
        _log.info(
            "alerts scheduler stopped (ticks=%d, total_fires=%d)",
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
            from clawmes.commands import alerts

            n = alerts._run_due_sync()
            self._last_runs = n
            self._total_runs += n
            if n > 0:
                _log.info("alerts tick fired %d alert(s)", n)
        except Exception:  # noqa: BLE001
            _log.exception("alerts scheduler tick raised; swallowing")


def get_alerts_scheduler_service() -> AlertsSchedulerService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = AlertsSchedulerService()
    return _SERVICE


def _reset_for_tests() -> None:
    global _SERVICE
    _SERVICE = None
