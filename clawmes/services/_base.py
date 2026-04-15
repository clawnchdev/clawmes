"""Service ABC and helpers.

A "service" is a long-lived component with a managed lifecycle:

  * ``start()``  — called once at plugin boot, after dependencies are up.
  * ``stop()``   — called at plugin shutdown.
  * ``health()`` — optional; returns a small dict reported by ``hermes
    clawmes doctor``.
  * ``tick()``   — optional; called by ``services.registry`` on a fixed
    cadence (driven by Hermes cron), used by the plan scheduler and
    similar polling components.

Services are accessed via module-level ``get_<name>_service()`` accessors —
classic singleton pattern. Tests substitute fakes by patching the module
globals; production code never re-instantiates.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class Service(ABC):
    """Base class for managed services."""

    #: Stable, dotted identifier — used as a key in the registry and in logs.
    id: str = ""

    @abstractmethod
    def start(self) -> None:
        """Initialize the service. Idempotent — safe to call twice."""

    @abstractmethod
    def stop(self) -> None:
        """Tear down. Must not raise."""

    def health(self) -> dict[str, Any]:
        """Return a small status dict for diagnostics.

        Default implementation reports nothing useful — subclasses override
        when they have meaningful state to expose.
        """
        return {"id": self.id, "status": "unknown"}

    def tick(self) -> None:
        """Optional periodic callback. Default: no-op.

        Services that opt into the tick loop should set ``ticking = True``
        on the class so the registry knows to call them.
        """

    #: Set to ``True`` on subclasses that want ``tick()`` invoked on the
    #: cadence configured in ``services.registry`` (default 60s, driven by
    #: Hermes cron).
    ticking: bool = False
