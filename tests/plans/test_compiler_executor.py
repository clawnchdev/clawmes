"""Tests for the plan compiler and executor stubs.

Both raise ``NotImplementedError`` at this milestone. The tests pin the
public surface so the integration with the scheduler doesn't drift
while the real impls are written.
"""

from __future__ import annotations

import pytest

from clawmes.plans.compiler import compile_plan
from clawmes.plans.executor import run_plan
from clawmes.plans.ir import Action, Plan


class TestCompiler:
    def test_raises_not_implemented(self):
        with pytest.raises(NotImplementedError, match="plan compiler not wired"):
            compile_plan("DCA $100 into ETH every week")

    def test_with_explicit_plan_id(self):
        with pytest.raises(NotImplementedError):
            compile_plan("anything", plan_id="p-test")


class TestExecutor:
    @pytest.mark.asyncio
    async def test_raises_not_implemented(self):
        plan = Plan(
            plan_id="p1",
            description="d",
            steps=[Action(tool="transfer", args={})],
        )
        with pytest.raises(NotImplementedError, match="plan executor not wired"):
            await run_plan(plan, ctx=None)
