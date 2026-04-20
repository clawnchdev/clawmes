"""First-message onboarding flow.

When a user messages clawmes for the first time on a given channel,
``pre_gateway_dispatch`` short-circuits the LLM and runs the welcome
flow instead. The flow:

  1. **Welcome** — short greeting + capabilities pitch
  2. **Persona pick** — present 5 built-in personas + custom; user
     selects via reply or button
  3. **Wallet pick** — WalletConnect / Bankr / local key / skip
  4. **Done** — handoff to LLM, mark onboarding complete

Each user's onboarding state is persisted under
``${HERMES_HOME}/clawmes/onboarding/<sender_id>.json``.
"""

from __future__ import annotations

from clawmes.onboarding.flow import OnboardingState
from clawmes.onboarding.personas import PERSONAS, get_persona

__all__ = ["OnboardingState", "PERSONAS", "get_persona"]
