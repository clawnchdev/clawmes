"""Plan / automation commands: ``/plans``, ``/plan``, ``/plan_logs``,
``/interrupt_plan``, ``/pause_plan``, ``/resume_plan``, ``/triggers``,
``/watch``, ``/unwatch``, ``/cron``."""

from __future__ import annotations


async def handle_plans(raw_args: str) -> str:
    return "Active plans: (none — plan engine ships in v0.2.0)"


async def handle_plan(raw_args: str) -> str:
    plan_id = raw_args.strip()
    if not plan_id:
        return "Usage: /plan <plan_id>"
    return f"Plan {plan_id!r} not found (plan engine ships in v0.2.0)."


async def handle_plan_logs(raw_args: str) -> str:
    return "Plan logs not yet implemented at this milestone."


async def handle_interrupt_plan(raw_args: str) -> str:
    return "Plan cancellation not yet implemented at this milestone."


async def handle_pause_plan(raw_args: str) -> str:
    return "Plan pause not yet implemented at this milestone."


async def handle_resume_plan(raw_args: str) -> str:
    return "Plan resume not yet implemented at this milestone."


async def handle_triggers(raw_args: str) -> str:
    return "Active triggers: (none)"


async def handle_watch(raw_args: str) -> str:
    return "Watch trigger creation not yet implemented at this milestone."


async def handle_unwatch(raw_args: str) -> str:
    return "Watch trigger removal not yet implemented at this milestone."


async def handle_cron(raw_args: str) -> str:
    return (
        "Registered cron jobs: clawmes_plan_tick (every 1m, internal)\n"
        "Defer to `hermes cron list` for the full Hermes-managed view."
    )


def register(ctx) -> None:
    for name, handler, desc, hint in [
        ("plans", handle_plans, "List plans (running, paused, completed)", ""),
        ("plan", handle_plan, "Show plan IR + status", "<id>"),
        ("plan_logs", handle_plan_logs, "Step-by-step execution log", "<id>"),
        ("interrupt_plan", handle_interrupt_plan, "Cancel a running plan", "<id>"),
        ("pause_plan", handle_pause_plan, "Suspend a running plan", "<id>"),
        ("resume_plan", handle_resume_plan, "Resume a paused plan", "<id>"),
        ("triggers", handle_triggers, "Show active triggers", ""),
        ("watch", handle_watch, "Add a trigger (price | onchain | balance)", "<type> <args>"),
        ("unwatch", handle_unwatch, "Remove a trigger", "<id>"),
        ("cron", handle_cron, "Show clawmes-registered cron jobs", ""),
    ]:
        ctx.register_command(name=name, handler=handler, description=desc, args_hint=hint)
