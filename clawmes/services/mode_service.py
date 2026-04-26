"""Mode service — readonly / danger-mode toggle.

The ``@write_tool`` decorator's stage 1 reads from this service.
``readonly`` mode blocks every write at the gate; ``danger`` mode
disables the readonly check but still runs every other gate (policy,
delegation, ledger).

Persistence: state is held in memory only — modes are session-scoped.
A user toggling ``/safemode`` doesn't survive a process restart, by
design (so an attacker who compromised the agent and flipped the bit
can't keep it set).
"""

from __future__ import annotations

import threading
from typing import Literal

from clawmes.lib.logger import logger_for
from clawmes.services._base import Service

_log = logger_for("services.mode")

Mode = Literal["normal", "readonly", "danger"]


class ModeService(Service):
    id = "clawmes.mode"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._mode: Mode = "normal"

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    @property
    def mode(self) -> Mode:
        return self._mode

    def is_readonly(self, user_id: str = "default") -> bool:
        # ``user_id`` is accepted for API symmetry with future per-user
        # modes; v0.1 uses a single global mode.
        del user_id
        return self._mode == "readonly"

    def is_danger(self, user_id: str = "default") -> bool:
        del user_id
        return self._mode == "danger"

    def set_mode(self, mode: Mode) -> None:
        if mode not in ("normal", "readonly", "danger"):
            raise ValueError(f"Unknown mode: {mode!r}")
        with self._lock:
            previous = self._mode
            self._mode = mode
        _log.info("mode change: %s -> %s", previous, mode)


_instance: ModeService | None = None


def get_mode_service() -> ModeService:
    global _instance
    if _instance is None:
        _instance = ModeService()
    return _instance


def is_readonly(user_id: str = "default") -> bool:
    """Module-level convenience wrapper."""
    return get_mode_service().is_readonly(user_id)
