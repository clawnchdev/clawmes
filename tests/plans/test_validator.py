"""Tests for clawmes.plans.validator (passes 1 + 5 implemented)."""

from __future__ import annotations

from clawmes.plans.ir import Action, If, Loop, Parallel, Plan
from clawmes.plans.validator import validate_plan


class TestSchemaPass:
    def test_empty_plan_rejected(self):
        plan = Plan(plan_id="p1", description="empty", steps=[])
        report = validate_plan(plan)
        assert not report.ok
        assert any("no steps" in e.lower() for e in report.errors)

    def test_simple_action_ok(self):
        plan = _plan([Action(tool="transfer", args={})])
        report = validate_plan(plan)
        assert report.ok, report.errors

    def test_action_missing_tool(self):
        plan = _plan([Action(tool="", args={})])
        report = validate_plan(plan)
        assert not report.ok
        assert any("Action.tool is empty" in e for e in report.errors)

    def test_if_missing_condition(self):
        plan = _plan(
            [
                If(condition="", then=[Action(tool="transfer", args={})]),
            ]
        )
        report = validate_plan(plan)
        assert not report.ok
        assert any("If.condition is empty" in e for e in report.errors)

    def test_if_with_valid_condition(self):
        plan = _plan(
            [
                If(
                    condition="${steps.0.details.price} > 2000",
                    then=[Action(tool="transfer", args={})],
                ),
            ]
        )
        report = validate_plan(plan)
        assert report.ok, report.errors

    def test_loop_no_iter_no_until(self):
        plan = _plan(
            [
                Loop(iterations=0, body=[Action(tool="transfer", args={})]),
            ]
        )
        report = validate_plan(plan)
        assert not report.ok
        assert any("Loop has no iteration cap" in e for e in report.errors)

    def test_loop_with_until_clause_no_iter_cap_ok(self):
        plan = _plan(
            [
                Loop(
                    iterations=0,
                    until="${steps.0.details.done} == true",
                    body=[Action(tool="transfer", args={})],
                ),
            ]
        )
        report = validate_plan(plan)
        assert report.ok, report.errors

    def test_parallel_no_branches(self):
        plan = _plan([Parallel(branches=[])])
        report = validate_plan(plan)
        assert not report.ok
        assert any("Parallel has no branches" in e for e in report.errors)

    def test_parallel_with_branches(self):
        plan = _plan(
            [
                Parallel(
                    branches=[
                        [Action(tool="transfer", args={})],
                        [Action(tool="defi_swap", args={})],
                    ]
                ),
            ]
        )
        report = validate_plan(plan)
        assert report.ok, report.errors

    def test_nested_if_validation(self):
        plan = _plan(
            [
                If(
                    condition="x > 0",
                    then=[
                        If(condition="", then=[Action(tool="transfer", args={})]),
                    ],
                ),
            ]
        )
        report = validate_plan(plan)
        assert not report.ok
        # Nested error path should be reported
        assert any("If.condition is empty" in e for e in report.errors)


class TestBoundedResources:
    def test_default_plan_passes(self):
        plan = _plan([Action(tool="transfer", args={})])
        report = validate_plan(plan)
        assert report.ok

    def test_zero_parallelism_rejected(self):
        plan = Plan(
            plan_id="p",
            description="d",
            steps=[Action(tool="transfer", args={})],
            max_parallelism=0,
        )
        report = validate_plan(plan)
        assert not report.ok
        assert any("max_parallelism" in e for e in report.errors)

    def test_zero_loop_iterations_cap_rejected(self):
        plan = Plan(
            plan_id="p",
            description="d",
            steps=[Action(tool="transfer", args={})],
            max_loop_iterations=0,
        )
        report = validate_plan(plan)
        assert not report.ok
        assert any("max_loop_iterations" in e for e in report.errors)

    def test_zero_wall_clock_rejected(self):
        plan = Plan(
            plan_id="p",
            description="d",
            steps=[Action(tool="transfer", args={})],
            max_wall_clock_seconds=0,
        )
        report = validate_plan(plan)
        assert not report.ok
        assert any("max_wall_clock_seconds" in e for e in report.errors)


class TestReportShape:
    def test_ok_report_has_no_errors(self):
        report = validate_plan(_plan([Action(tool="transfer", args={})]))
        assert report.ok is True
        assert report.errors == []

    def test_failing_report_has_errors(self):
        report = validate_plan(_plan([]))
        assert report.ok is False
        assert len(report.errors) >= 1


# Helper -------------------------------------------------------------------


def _plan(steps):
    return Plan(plan_id="p1", description="test plan", steps=steps)
