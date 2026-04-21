"""Plan IR — the validated intermediate representation produced by the compiler.

Four step types (sum type):

  * :class:`Action`    — call a tool with bound args
  * :class:`If`        — branch on a condition expression
  * :class:`Loop`      — bounded iteration with a per-step body
  * :class:`Parallel`  — concurrent branch group with bounded fan-out

Plans are flat lists of steps. Steps reference earlier-step results by
``${steps.N.details.field}`` paths (resolved by the executor against
the per-tool ``details`` payload).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class Action:
    kind: Literal["action"] = field(default="action", init=False)
    tool: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    on_failure: Literal["abort", "skip", "retry"] = "abort"
    retry_max: int = 0
    label: str = ""


@dataclass(frozen=True)
class If:
    kind: Literal["if"] = field(default="if", init=False)
    condition: str = ""  # e.g. "${steps.2.details.price} > 2000"
    then: list[Step] = field(default_factory=list)
    else_: list[Step] = field(default_factory=list)
    label: str = ""


@dataclass(frozen=True)
class Loop:
    kind: Literal["loop"] = field(default="loop", init=False)
    iterations: int = 1
    body: list[Step] = field(default_factory=list)
    every: str | None = None  # cron-or-interval; if set, scheduler waits between iters
    until: str | None = None  # boolean expr; loop early-terminates when true
    label: str = ""


@dataclass(frozen=True)
class Parallel:
    kind: Literal["parallel"] = field(default="parallel", init=False)
    branches: list[list[Step]] = field(default_factory=list)
    max_concurrency: int = 4
    label: str = ""


Step = Action | If | Loop | Parallel


@dataclass(frozen=True)
class Plan:
    """Top-level plan document."""

    plan_id: str = ""
    description: str = ""  # human summary
    steps: list[Step] = field(default_factory=list)

    # Triggers — at least one entry, evaluated by scheduler.
    triggers: list[dict[str, Any]] = field(default_factory=list)

    # Bounded resources (set by validator).
    max_parallelism: int = 4
    max_loop_iterations: int = 100
    max_wall_clock_seconds: int = 60 * 60 * 24

    created_at: str = ""
    created_by: str = ""
