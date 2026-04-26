"""Spending-policy engine.

Modules:

  * :mod:`clawmes.policy.types` — Policy, ActionContext, Decision IRs
    plus the bundled DEFAULT_POLICIES set.
  * :mod:`clawmes.policy.storage` — JSON persistence under
    ``${HERMES_HOME}/clawmes/policy/policies.json``.
  * :mod:`clawmes.policy.usage_counter` — sliding-window invocation
    counter for rate-limit policies.
  * :mod:`clawmes.policy.evaluator` — the ``allow | block | confirm``
    decision function. Called by the ``@write_tool`` decorator before
    every write tool's handler.
  * :mod:`clawmes.policy.confirm_store` — one-time nonce store for
    the ``confirm`` decision. Issues a nonce, then consumes it when
    the LLM retries with ``policyConfirmationNonce`` set.

Policy parsing from natural language is a planned future addition;
v0.1 reads policies from ``policies.json`` (with bundled defaults
installed on first run).
"""

from __future__ import annotations

from clawmes.policy.confirm_store import ConfirmStore
from clawmes.policy.evaluator import evaluate, record_invocation
from clawmes.policy.types import ActionContext, Decision, Policy

__all__ = [
    "ActionContext",
    "ConfirmStore",
    "Decision",
    "Policy",
    "evaluate",
    "record_invocation",
]
