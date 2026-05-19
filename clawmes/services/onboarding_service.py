"""Onboarding service — in-memory per-sender onboarding state.

Holds three concerns separately so we don't have to extend the
:class:`clawmes.onboarding.flow.OnboardingState` dataclass:

  * **Step state** — the current step ('welcome' / 'pick_persona' /
    'pick_wallet' / 'pick_chain' / 'done') plus chosen persona /
    wallet mode / chain id (delegated to the existing
    :class:`OnboardingState`).
  * **Capability picks** — a per-sender set of capability IDs the user
    has opted into. Capabilities are advertised but not yet enforced;
    enforcement (suppressing tool registrations on a per-sender basis)
    is future work, tracked separately from this surface.
  * **Step history** — a stack used by ``/back``. Each
    :meth:`advance_step` push the previous step; :meth:`back` pops
    and restores. ``/reonboard`` clears everything.

State is in-memory only — matches the posture of :mod:`clawmes.services.persona_service`.
Restart resets every sender to a fresh ``welcome`` step. Persistence
to ``${HERMES_HOME}/clawmes/onboarding/<sender_id>.json`` is documented
in ``clawmes/onboarding/__init__.py`` but not yet wired; a follow-up
PR can route through this service without changing the public API.

Single-user default: ``sender_id`` defaults to ``"default"``, matching
:mod:`clawmes.policy.types.ActionContext` and the assumption baked
into the CLI ``hermes clawmes init`` flow.
"""

from __future__ import annotations

import threading

from clawmes.lib.logger import logger_for
from clawmes.onboarding.flow import OnboardingState, OnboardingStep
from clawmes.services._base import Service
from clawmes.services.persona_service import get_persona_service

_log = logger_for("services.onboarding")

DEFAULT_SENDER = "default"

# The canonical 10 capability IDs. Mirrors OpenClawnch's CAPABILITIES
# constant (``onboarding-flow.ts:142-215`) with clawmes-appropriate
# labels. The labels are surfaced through the LLM in slash-command
# descriptions and the ``/welcome`` status output.
CAPABILITIES: tuple[tuple[str, str], ...] = (
    ("wallet", "Wallet & transactions"),
    ("prices", "Prices & market data"),
    ("portfolio", "Portfolio & balance tracking"),
    ("trading", "DEX trading & swaps"),
    ("liquidity", "Liquidity provision"),
    ("launchpad", "Token launchpad (Clawnch)"),
    ("bridge", "Cross-chain bridge"),
    ("routing", "Smart routing (Wayfinder)"),
    ("clawnx", "ClawnX protocol"),
    ("hummingbot", "Market making (Hummingbot)"),
)
_CAPABILITY_IDS: frozenset[str] = frozenset(cap_id for cap_id, _ in CAPABILITIES)

# Linear step sequence used by ``/skip``. Mirrors the order in
# :data:`clawmes.onboarding.flow.OnboardingStep`.
_STEP_SEQUENCE: tuple[OnboardingStep, ...] = (
    "welcome",
    "pick_persona",
    "pick_wallet",
    "pick_chain",
    "done",
)


class OnboardingService(Service):
    id = "clawmes.onboarding"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._states: dict[str, OnboardingState] = {}
        self._capabilities: dict[str, set[str]] = {}
        self._history: dict[str, list[OnboardingStep]] = {}

    def start(self) -> None:
        # Pure-memory service — nothing to bring up. start() is a no-op
        # so the registry-driven boot sequence doesn't fail.
        pass

    def stop(self) -> None:
        with self._lock:
            self._states.clear()
            self._capabilities.clear()
            self._history.clear()

    # --- state access ----------------------------------------------------

    def get_state(self, sender_id: str = DEFAULT_SENDER) -> OnboardingState:
        """Return the sender's :class:`OnboardingState`, creating it on first access."""
        with self._lock:
            if sender_id not in self._states:
                self._states[sender_id] = OnboardingState(sender_id=sender_id)
                self._capabilities[sender_id] = set()
                self._history[sender_id] = []
            return self._states[sender_id]

    def get_capabilities(self, sender_id: str = DEFAULT_SENDER) -> frozenset[str]:
        """Return the set of enabled capability IDs for the sender."""
        self.get_state(sender_id)
        with self._lock:
            return frozenset(self._capabilities[sender_id])

    # --- capability mutation --------------------------------------------

    def set_capability(
        self,
        capability_id: str,
        enabled: bool,
        *,
        sender_id: str = DEFAULT_SENDER,
    ) -> bool:
        """Set a capability's enabled state. Returns the resulting state.

        Raises :class:`ValueError` for unknown capability IDs.
        """
        if capability_id not in _CAPABILITY_IDS:
            raise ValueError(f"unknown capability: {capability_id!r}")
        self.get_state(sender_id)
        with self._lock:
            caps = self._capabilities[sender_id]
            if enabled:
                caps.add(capability_id)
            else:
                caps.discard(capability_id)
            return capability_id in caps

    def toggle_capability(
        self,
        capability_id: str,
        *,
        sender_id: str = DEFAULT_SENDER,
    ) -> bool:
        """Flip a capability's enabled state. Returns the resulting state."""
        currently = capability_id in self.get_capabilities(sender_id)
        return self.set_capability(capability_id, not currently, sender_id=sender_id)

    # --- step transitions -----------------------------------------------

    def advance_step(
        self,
        step: OnboardingStep,
        *,
        sender_id: str = DEFAULT_SENDER,
    ) -> OnboardingState:
        """Move to ``step``, pushing the previous step onto the history stack."""
        state = self.get_state(sender_id)
        with self._lock:
            self._history[sender_id].append(state.step)
        state.advance_to(step)
        return state

    def skip(self, sender_id: str = DEFAULT_SENDER) -> OnboardingState:
        """Skip the current step → advance to the next in the canonical sequence.

        Already-at-end is a no-op (returns the current state unchanged).
        """
        state = self.get_state(sender_id)
        try:
            idx = _STEP_SEQUENCE.index(state.step)
        except ValueError:
            # Unknown step shouldn't be reachable through the public API,
            # but stay safe: jump to "done" so the user can move on.
            return self.advance_step("done", sender_id=sender_id)
        if idx + 1 >= len(_STEP_SEQUENCE):
            return state
        return self.advance_step(_STEP_SEQUENCE[idx + 1], sender_id=sender_id)

    def back(self, sender_id: str = DEFAULT_SENDER) -> OnboardingState | None:
        """Restore the previous step from history.

        Returns ``None`` when the history stack is empty (i.e. user is
        at the original ``welcome`` step or has manually reonboarded).
        """
        state = self.get_state(sender_id)
        with self._lock:
            history = self._history[sender_id]
            if not history:
                return None
            previous = history.pop()
        state.step = previous
        # If we walked back from done, we're no longer complete.
        state.complete = previous == "done"
        return state

    def reonboard(self, sender_id: str = DEFAULT_SENDER) -> OnboardingState:
        """Reset all onboarding state for the sender. Clears the active persona too."""
        with self._lock:
            self._states[sender_id] = OnboardingState(sender_id=sender_id)
            self._capabilities[sender_id] = set()
            self._history[sender_id] = []
            fresh = self._states[sender_id]
        # Clear persona outside the lock — set_persona acquires its own.
        get_persona_service().set_persona(None)
        _log.info("onboarding reset for sender=%s", sender_id)
        return fresh


_instance: OnboardingService | None = None


def get_onboarding_service() -> OnboardingService:
    global _instance
    if _instance is None:
        _instance = OnboardingService()
    return _instance
