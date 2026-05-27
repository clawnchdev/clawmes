"""Limit-order scheduler service — drives ``/limit_order tick`` automatically.

Same shape as the DCA / copy / alerts scheduler services. Each
service tick evaluates every active limit order:

  * Fetch current USD price via ``defi_price``
  * If threshold crossed, submit swap via ``defi_swap``
  * Flip status to ``filled`` on success, ``failed`` after
    ``max_attempts`` unsuccessful tries.

Per-order errors caught internally so one bad order can't crash the
loop.
"""

from __future__ import annotations

from typing import Any

from clawmes.lib.logger import logger_for
from clawmes.services._base import Service

_log = logger_for("services.limit_order_scheduler")

_SERVICE: LimitOrderSchedulerService | None = None


class LimitOrderSchedulerService(Service):
    id = "clawmes.limit_order_scheduler"
    ticking = True

    def __init__(self) -> None:
        self._running = False
        self._ticks = 0
        self._last_runs = 0
        self._total_runs = 0

    def start(self) -> None:
        self._running = True
        _log.info("limit-order scheduler started — tick cadence driven by registry")

    def stop(self) -> None:
        self._running = False
        _log.info(
            "limit-order scheduler stopped (ticks=%d, total_fired=%d)",
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
            from clawmes.commands import limit_order

            n = limit_order._run_due_sync()
            self._last_runs = n
            self._total_runs += n
            if n > 0:
                _log.info("limit-order tick fired %d order(s)", n)
        except Exception:  # noqa: BLE001
            _log.exception("limit-order scheduler tick raised; swallowing")


def get_limit_order_scheduler_service() -> LimitOrderSchedulerService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = LimitOrderSchedulerService()
    return _SERVICE


def _reset_for_tests() -> None:
    global _SERVICE
    _SERVICE = None
