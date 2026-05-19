"""Evolution-mode service — gate for self-modifying tools.

The ``agent_memory`` and ``skill_evolve`` tools let the agent rewrite
its own persistent memory and skills. Without a gate, an LLM that
gets prompt-injected (or that misreads ambient context) can mutate
the user's long-term state without explicit consent.

This service holds a single boolean: ``is_evolving()``. When ``False``
(the default), the write actions of ``agent_memory`` and
``skill_evolve`` should return an ``evolution_gate`` error rather
than executing. Read actions (``query`` / ``list``) are always
allowed.

The user toggles via ``/evolve`` (enable), ``/stable`` (disable),
``/evolution`` (status). State is in-memory only — matches
``mode_service`` and ``persona_service`` posture; a process restart
returns to the safe default.

This is the clawmes equivalent of OpenClawnch's ``wrapWithEvoGate``
(``extensions/crypto/index.ts:402-419``). We implement it inside each
gated tool's handler rather than as a decorator wrapper to keep the
existing ``@write_tool`` decorator pipeline unchanged.
"""

from __future__ import annotations

import threading

from clawmes.lib.logger import logger_for
from clawmes.services._base import Service

_log = logger_for("services.evolution_mode")


class EvolutionModeService(Service):
    id = "clawmes.evolution_mode"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # Disabled by default — the safe posture. Users opt in.
        self._evolving: bool = False

    def start(self) -> None:
        pass

    def stop(self) -> None:
        with self._lock:
            self._evolving = False

    def is_evolving(self) -> bool:
        """Return True iff self-modification is currently enabled."""
        with self._lock:
            return self._evolving

    def set_evolving(self, enabled: bool) -> bool:
        """Set the evolution flag. Returns the new state."""
        with self._lock:
            previous = self._evolving
            self._evolving = bool(enabled)
            now = self._evolving
        if previous != now:
            _log.info(
                "evolution mode %s",
                "enabled" if now else "disabled",
            )
        return now

    def health(self) -> dict[str, object]:
        return {
            "id": self.id,
            "status": "evolving" if self.is_evolving() else "stable",
        }


_instance: EvolutionModeService | None = None


def get_evolution_mode_service() -> EvolutionModeService:
    global _instance
    if _instance is None:
        _instance = EvolutionModeService()
    return _instance


def is_evolving() -> bool:
    """Module-level convenience wrapper used by gated tool handlers."""
    return get_evolution_mode_service().is_evolving()
