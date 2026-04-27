"""Persona service — holds the active persona for the session.

The five built-in personas live in :mod:`clawmes.onboarding.personas`
with snippet files at ``clawmes/data/personas/<name>.md``. Users
choose one during onboarding via ``/persona <name>``; the chosen
persona's snippet text is injected into every LLM call's per-turn
context by the ``pre_llm_call`` hook.

State is in-memory only — restart resets to no persona. The
onboarding flow re-asks if no persona is set on first message.
"""

from __future__ import annotations

import threading

from clawmes.lib.logger import logger_for
from clawmes.onboarding.personas import Persona, get_persona
from clawmes.services._base import Service

_log = logger_for("services.persona")


class PersonaService(Service):
    id = "clawmes.persona"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: str | None = None

    def start(self) -> None:
        pass

    def stop(self) -> None:
        with self._lock:
            self._active = None

    @property
    def active_name(self) -> str | None:
        return self._active

    def set_persona(self, name: str | None) -> Persona | None:
        """Set the active persona by name. Returns the loaded Persona,
        or ``None`` if the name is unknown / cleared.

        Passing ``None`` clears the active persona.
        """
        if name is None or name.strip() == "":
            with self._lock:
                self._active = None
            _log.info("persona cleared")
            return None

        persona = get_persona(name)
        if persona is None:
            _log.warning("unknown persona name: %r", name)
            return None

        with self._lock:
            self._active = persona.name
        _log.info("persona set to %s", persona.name)
        return persona

    def active_persona(self) -> Persona | None:
        """Return the currently-active :class:`Persona`, or ``None``."""
        with self._lock:
            name = self._active
        return get_persona(name) if name else None

    def active_snippet(self) -> str:
        """Return the active persona's snippet text, or ``""`` if none."""
        persona = self.active_persona()
        if persona is None:
            return ""
        return persona.load_snippet()


_instance: PersonaService | None = None


def get_persona_service() -> PersonaService:
    global _instance
    if _instance is None:
        _instance = PersonaService()
    return _instance
