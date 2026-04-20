"""Plan scheduler — fires triggers and dispatches plans to the executor.

Driven by the global tick loop in ``services.registry.tick_all()``,
which Hermes cron calls every 60s. On each tick:

  1. Load all active plans from disk (``${HERMES_HOME}/clawmes/plans/``).
  2. For each plan, evaluate triggers (time / price / on-chain).
  3. For each fired trigger, hand the plan to
     :func:`clawmes.plans.executor.run_plan`.

State persistence: scheduler writes plan execution state back after
every step so a process restart resumes from the last completed step.
"""

from __future__ import annotations

from clawmes.lib.logger import logger_for
from clawmes.services._base import Service

_log = logger_for("plans.scheduler")


class PlanScheduler(Service):
    id = "clawmes.plan_scheduler"
    ticking = True

    def __init__(self) -> None:
        self._running = False

    def start(self) -> None:
        self._running = True
        _log.info("plan scheduler started (stub — no triggers yet)")

    def stop(self) -> None:
        self._running = False

    def tick(self) -> None:
        if not self._running:
            return
        # TODO(v0.2.0): load plans, evaluate triggers, dispatch executor


_instance: PlanScheduler | None = None


def get_scheduler() -> PlanScheduler:
    global _instance
    if _instance is None:
        _instance = PlanScheduler()
    return _instance
