"""Compound action plan engine.

Pipeline:

.. code-block::

    NL prompt
       │
       ▼
    compiler.py        LLM-assisted compile to IR
       │
       ▼
    ir.py              Typed IR: Plan = list[Step]; Step = Action | If | Loop | Parallel
       │
       ▼
    validator.py       6-pass validation
       │
       ▼
    storage.py         Persist: ${HERMES_HOME}/clawmes/plans/<id>.json
       │
       ▼
    scheduler.py       Tick loop (driven by Hermes cron @ 60s)
       │
       ▼
    executor.py        Tree-walking executor
"""

from __future__ import annotations

from clawmes.plans.ir import Action, If, Loop, Parallel, Plan, Step

__all__ = ["Action", "If", "Loop", "Parallel", "Plan", "Step"]
