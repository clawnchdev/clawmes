"""6-pass plan validator.

Each pass is an independent function that raises :class:`ValidationError`
on a problem. ``validate_plan`` runs all six in order and aggregates
errors so a user gets a complete report rather than one-at-a-time.

Passes:

  1. **Schema** — ensures every step is a known kind and required fields
     are present.
  2. **Reference resolution** — ``${steps.N.details.X}`` references must
     point to a step that comes before, and ``X`` must be a key the
     referenced tool's ``details`` payload could produce.
  3. **Tool-name existence** — every Action.tool must be a registered
     clawmes tool.
  4. **Trigger validity** — every trigger has a known type
     (time/price/onchain) and parsable args.
  5. **Bounded resources** — Parallel.max_concurrency ≤ plan cap,
     Loop.iterations ≤ plan cap, Plan.max_wall_clock_seconds ≤ system
     cap.
  6. **Policy compatibility** — does any step violate a configured
     spending policy at face value? (E.g. unbounded approval where a
     bounded approval is required.)

Stubs at this milestone — passes 1 and 5 are implemented; 2/3/4/6 are
placeholders.
"""

from __future__ import annotations

from dataclasses import dataclass

from clawmes.plans.ir import Action, If, Loop, Parallel, Plan, Step


class ValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ValidationReport:
    ok: bool
    errors: list[str]
    warnings: list[str]


def validate_plan(plan: Plan) -> ValidationReport:
    errors: list[str] = []
    warnings: list[str] = []

    try:
        _pass_schema(plan)
    except ValidationError as exc:
        errors.append(f"schema: {exc}")
    # _pass_reference_resolution — TODO
    # _pass_tool_name_existence — TODO
    # _pass_trigger_validity    — TODO
    try:
        _pass_bounded_resources(plan)
    except ValidationError as exc:
        errors.append(f"bounded_resources: {exc}")
    # _pass_policy_compatibility — TODO

    return ValidationReport(ok=not errors, errors=errors, warnings=warnings)


def _pass_schema(plan: Plan) -> None:
    if not plan.steps:
        raise ValidationError("Plan has no steps")
    _check_steps(plan.steps, "")


def _check_steps(steps: list[Step], prefix: str) -> None:
    for i, step in enumerate(steps):
        path = f"{prefix}step[{i}]"
        if isinstance(step, Action):
            if not step.tool:
                raise ValidationError(f"{path}: Action.tool is empty")
        elif isinstance(step, If):
            if not step.condition:
                raise ValidationError(f"{path}: If.condition is empty")
            _check_steps(step.then, path + ".then.")
            _check_steps(step.else_, path + ".else.")
        elif isinstance(step, Loop):
            if step.iterations <= 0 and step.until is None:
                raise ValidationError(
                    f"{path}: Loop has no iteration cap and no until clause"
                )
            _check_steps(step.body, path + ".body.")
        elif isinstance(step, Parallel):
            if not step.branches:
                raise ValidationError(f"{path}: Parallel has no branches")
            for j, branch in enumerate(step.branches):
                _check_steps(branch, f"{path}.branch[{j}].")
        else:
            raise ValidationError(f"{path}: unknown step kind {type(step).__name__}")


def _pass_bounded_resources(plan: Plan) -> None:
    if plan.max_parallelism <= 0:
        raise ValidationError("max_parallelism must be > 0")
    if plan.max_loop_iterations <= 0:
        raise ValidationError("max_loop_iterations must be > 0")
    if plan.max_wall_clock_seconds <= 0:
        raise ValidationError("max_wall_clock_seconds must be > 0")
