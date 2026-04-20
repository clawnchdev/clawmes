"""Plan triggers.

Each trigger module exports an ``evaluate(trigger, now)`` function that
returns ``True`` if the trigger should fire on this tick. The scheduler
calls all of them per active plan.
"""

from __future__ import annotations
