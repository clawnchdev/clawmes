"""NL → Plan IR compiler.

The compiler asks an auxiliary model (configured via Hermes' ``auxiliary``
provider slot) to translate a natural-language plan description into a
JSON document conforming to the IR schema. The IR is then handed to
:mod:`clawmes.plans.validator` for the 6-pass check.

Stub at this milestone — exposes the public function so callers can
wire it, but raises ``NotImplementedError`` when invoked. The real
impl needs:

  * Hermes auxiliary-model dispatch
  * IR JSON schema definition (or pydantic models)
  * Few-shot examples (the most-cited form of fragility in NL→IR)
  * Re-prompt-on-malformed-output retry loop (≤3 attempts)
"""

from __future__ import annotations

from clawmes.plans.ir import Plan


def compile_plan(natural_language: str, *, plan_id: str = "") -> Plan:
    """Compile a natural-language plan description into validated IR."""
    raise NotImplementedError(
        "plan compiler not wired in this milestone. "
        "Forthcoming: auxiliary-model dispatch + IR JSON schema + re-prompt loop."
    )
