"""Plan scheduler — fires triggers and dispatches plans to the executor.

Driven by the global tick loop in ``services.registry.tick_all()``,
which Hermes cron calls every 60s. On each tick:

  1. Load all active plans from disk (``${HERMES_HOME}/clawmes/plans/``).
  2. For each plan, evaluate triggers (time / price / on-chain).
  3. For each fired trigger, hand the plan to
     :func:`clawmes.plans.executor.run_plan`.

State persistence: scheduler writes plan execution state back after
every step so a process restart resumes from the last completed step.

The full triggering / executor wiring is staged for v0.2.0; this
file currently exposes the management surface (``create_plan``,
``cancel_plan``, etc.) used by the ``compound_action`` tool. Plans
are persisted as JSON files in ``${HERMES_HOME}/clawmes/plans/``;
the tick loop just doesn't fire triggers against them yet.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from clawmes.lib.logger import logger_for
from clawmes.lib.paths import hermes_home
from clawmes.services._base import Service

_log = logger_for("plans.scheduler")


def _plans_dir() -> Path:
    return hermes_home() / "clawmes" / "plans"


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

    # --- management surface (used by compound_action) -----------------

    def create_plan(self, plan_text: str) -> dict[str, Any]:
        """Persist a plan and return its id. Triggers don't fire yet."""
        d = _plans_dir()
        d.mkdir(parents=True, exist_ok=True)
        plan_id = f"plan-{int(time.time())}"
        record = {
            "id": plan_id,
            "text": plan_text,
            "created_at": time.time(),
            "status": "pending",
        }
        (d / f"{plan_id}.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
        return record

    def validate_plan(self, plan_text: str) -> dict[str, Any]:
        """Lightweight syntactic validation. Real type-check lands with
        the IR compiler in v0.2.0."""
        if not plan_text or not isinstance(plan_text, str):
            return {"valid": False, "errors": ["plan text is empty"]}
        return {"valid": True, "errors": []}

    def dry_run(self, plan_text: str) -> dict[str, Any]:
        """Stub — full simulation needs the IR compiler + executor."""
        return {
            "plan_text": plan_text,
            "would_execute": True,
            "note": "Full simulation is not yet implemented; this is a syntactic dry-run only.",
        }

    def list_plans(self) -> list[dict[str, Any]]:
        d = _plans_dir()
        if not d.exists():
            return []
        out = []
        for p in sorted(d.glob("*.json")):
            try:
                out.append(json.loads(p.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                continue
        return out

    def cancel_plan(self, plan_id: str) -> dict[str, Any]:
        path = _plans_dir() / f"{plan_id}.json"
        if not path.exists():
            return {"cancelled": False, "reason": "not found"}
        try:
            path.unlink()
        except OSError as exc:
            return {"cancelled": False, "reason": str(exc)}
        return {"cancelled": True, "plan_id": plan_id}

    def get_plan_logs(self, plan_id: str) -> list[dict[str, Any]]:
        """Return execution logs for a plan. None recorded yet since the
        executor is staged."""
        path = _plans_dir() / f"{plan_id}.json"
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
        return data.get("logs") or []


_instance: PlanScheduler | None = None


def get_scheduler() -> PlanScheduler:
    global _instance
    if _instance is None:
        _instance = PlanScheduler()
    return _instance
