"""Spending-policy engine.

Three modules:

  * :mod:`clawmes.policy.evaluator` — the ``allow | block | confirm``
    decision function. Called by the ``@write_tool`` decorator before
    every write tool's handler.
  * :mod:`clawmes.policy.confirm_store` — one-time nonce store for the
    ``confirm`` decision. Issues a nonce, then consumes it when the LLM
    retries with ``policyConfirmationNonce`` set.
  * :mod:`clawmes.policy.storage` — load/save policies as JSON under
    ``${HERMES_HOME}/clawmes/policy/policies.json`` (stub here; full
    impl in a forthcoming commit).

Policies themselves are NL-parsed by ``policy.parser``. Each compiled
policy is a small IR document with: action_type, threshold, time_window,
exceptions. The evaluator iterates and short-circuits on the first
match.
"""

from __future__ import annotations

from clawmes.policy.confirm_store import ConfirmStore
from clawmes.policy.evaluator import Decision, evaluate

__all__ = ["ConfirmStore", "Decision", "evaluate"]
