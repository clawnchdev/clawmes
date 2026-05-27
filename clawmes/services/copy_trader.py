"""Copy-trader scheduler service — drives ``/copy tick`` automatically.

Mirror of ``DcaSchedulerService`` for the ``/copy`` command. Each
service tick polls Basescan for every active follow's new ERC-20
receipts and submits a copy buy for any non-blocklisted token.

Lifecycle:

  * ``start()`` — mark running.
  * ``stop()``  — mark not running.
  * ``tick()``  — call ``copy._run_due_sync()``. Per-follow errors are
    caught inside the runner; this layer adds one more catch so a
    Basescan outage or RPC failure cannot crash the cron loop.

Storage lives in the ``/copy`` command module. This service is just
the cron-driver wrapper.
"""

from __future__ import annotations

from typing import Any

from clawmes.lib.logger import logger_for
from clawmes.services._base import Service

_log = logger_for("services.copy_trader")

_SERVICE: CopyTraderService | None = None


class CopyTraderService(Service):
    id = "clawmes.copy_trader"
    ticking = True

    def __init__(self) -> None:
        self._running = False
        self._ticks = 0
        self._last_runs = 0
        self._total_runs = 0

    def start(self) -> None:
        self._running = True
        _log.info("copy trader started — tick cadence driven by registry")

    def stop(self) -> None:
        self._running = False
        _log.info(
            "copy trader stopped (ticks=%d, total_runs=%d)",
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
            from clawmes.commands import copy

            n = copy._run_due_sync()
            self._last_runs = n
            self._total_runs += n
            if n > 0:
                _log.info("copy tick fired %d buy(s)", n)
        except Exception:  # noqa: BLE001
            _log.exception("copy trader tick raised; swallowing")


def get_copy_trader_service() -> CopyTraderService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = CopyTraderService()
    return _SERVICE


def _reset_for_tests() -> None:
    global _SERVICE
    _SERVICE = None
