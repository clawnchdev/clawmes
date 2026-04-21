"""Onboarding state machine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

OnboardingStep = Literal[
    "welcome",
    "pick_persona",
    "pick_wallet",
    "pick_chain",
    "done",
]


@dataclass
class OnboardingState:
    """Per-user onboarding progress.

    Persisted as JSON under
    ``${HERMES_HOME}/clawmes/onboarding/<sender_id>.json``.
    """

    sender_id: str
    step: OnboardingStep = "welcome"
    chosen_persona: str | None = None
    chosen_wallet_mode: str | None = None
    chosen_chain_id: int | None = None
    complete: bool = False

    def advance_to(self, step: OnboardingStep) -> None:
        self.step = step
        if step == "done":
            self.complete = True
