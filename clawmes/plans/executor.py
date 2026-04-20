"""Tree-walking plan executor.

Walks an IR plan, dispatching each step:

  * :class:`Action` → ``ctx.dispatch_tool`` (the public Hermes API).
  * :class:`If` → evaluate condition expression, recurse into the
    appropriate branch.
  * :class:`Loop` → iterate the body up to ``iterations`` times,
    optionally checking ``until`` between iters.
  * :class:`Parallel` → ``asyncio.gather`` with ``max_concurrency`` cap.

Per-step failure policy: ``abort`` (default), ``skip``, or ``retry``
(with ``retry_max``). Per-plan: hard wall-clock budget enforced by the
scheduler.

Stub at this milestone — exposes ``run_plan`` so other modules can
import the symbol; raises NotImplementedError when called.
"""

from __future__ import annotations

from typing import Any

from clawmes.plans.ir import Plan


async def run_plan(plan: Plan, *, ctx: Any) -> dict[str, Any]:
    """Execute ``plan`` against the given Hermes ``ctx``."""
    raise NotImplementedError(
        "plan executor not wired in this milestone. "
        "Forthcoming: tree-walking dispatch + asyncio.gather for Parallel."
    )
