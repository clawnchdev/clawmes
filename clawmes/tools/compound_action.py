"""``compound_action`` — multi-step automation plans.

Six actions exposing the existing plan_scheduler infrastructure to
the LLM:

  * ``create``    — submit a new plan (NL or DSL).
  * ``validate``  — type-check a plan without scheduling it.
  * ``dry_run``   — simulate execution without on-chain side effects.
  * ``cancel``    — stop a scheduled plan.
  * ``list``      — list scheduled / running / completed plans.
  * ``logs``      — fetch execution logs for a plan.

Plans are persisted by the plan_scheduler service and survive
restarts. Triggers (time, price, on-chain events) fire via Hermes'
cron daemon polling the scheduler.
"""

from __future__ import annotations

from typing import Any

from clawmes.lib.logger import logger_for
from clawmes.lib.params import read_str
from clawmes.lib.tool_result import error_result, json_result
from clawmes.tools.registry import register_with_ctx, write_tool

_log = logger_for("tools.compound_action")

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["create", "validate", "dry_run", "cancel", "list", "logs"],
        },
        "plan": {
            "type": "string",
            "description": "Plan in clawmes' NL or DSL form (create/validate/dry_run).",
        },
        "plan_id": {"type": "string", "description": "For cancel / logs."},
        "policyConfirmationNonce": {"type": "string"},
    },
    "required": ["action"],
}


@write_tool(
    name="compound_action",
    toolset="clawmes-misc",
    description=(
        "Multi-step automation plans — DCA, conditional triggers, "
        "loops. create/validate/dry_run/cancel/list/logs map to the "
        "existing plan_scheduler. Time / price / on-chain triggers "
        "fire via Hermes cron."
    ),
    schema=_SCHEMA,
    emoji="\U0001f3ac",
)
def compound_action(args: dict[str, Any], **kwargs: Any) -> str:
    action = read_str(args, "action", required=True)
    scheduler = _get_scheduler()
    if scheduler is None:
        return error_result("Plan scheduler not available", code="not_available")

    try:
        if action == "create":
            plan_text = read_str(args, "plan", required=True)
            result = scheduler.create_plan(plan_text)
        elif action == "validate":
            plan_text = read_str(args, "plan", required=True)
            result = scheduler.validate_plan(plan_text)
        elif action == "dry_run":
            plan_text = read_str(args, "plan", required=True)
            result = scheduler.dry_run(plan_text)
        elif action == "cancel":
            plan_id = read_str(args, "plan_id", required=True)
            result = scheduler.cancel_plan(plan_id)
        elif action == "list":
            result = scheduler.list_plans()
        else:
            plan_id = read_str(args, "plan_id", required=True)
            result = scheduler.get_plan_logs(plan_id)
    except AttributeError:
        return error_result(
            f"Plan scheduler doesn't support action {action!r} — "
            "this milestone exposes a subset of the planned API.",
            code="not_implemented",
        )
    except Exception as exc:  # noqa: BLE001
        return error_result(f"Plan scheduler raised: {exc}", code="scheduler_error")
    return json_result(
        {"action": action, "result": result},
        summary=f"compound_action {action}",
    )


def _get_scheduler():
    try:
        from clawmes.plans.scheduler import get_scheduler

        return get_scheduler()
    except ImportError:
        return None


def register(ctx) -> None:
    register_with_ctx(ctx, compound_action)
