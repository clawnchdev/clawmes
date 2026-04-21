"""Service registry — lifecycle bookkeeping for managed services.

The registry is a plain in-memory dict keyed by ``Service.id``. Services
register themselves when ``services.start_all()`` is called from the plugin
``register(ctx)`` entry point.

The registry also drives the ``tick()`` loop: components that opt into
``ticking = True`` get called once per tick from
:func:`tick_all`. The actual cadence comes from Hermes cron — clawmes
registers a single recurring cron job at install that calls into here.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator

from clawmes.lib.logger import logger_for
from clawmes.services._base import Service

_log = logger_for("services.registry")


class _Registry:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._services: dict[str, Service] = {}

    def register(self, service: Service) -> None:
        if not service.id:
            raise ValueError(f"Service {service!r} has empty id")
        with self._lock:
            if service.id in self._services:
                _log.debug("service %s already registered, skipping", service.id)
                return
            self._services[service.id] = service
            _log.debug("registered service %s", service.id)

    def get(self, sid: str) -> Service | None:
        with self._lock:
            return self._services.get(sid)

    def iter_services(self) -> Iterator[Service]:
        with self._lock:
            # Snapshot to avoid mutation-during-iter issues
            services = list(self._services.values())
        yield from services

    def clear(self) -> None:
        """Test helper — drop all registrations."""
        with self._lock:
            self._services.clear()


registry = _Registry()
"""Module-level singleton — import as ``from clawmes.services.registry import registry``."""


def tick_all() -> None:
    """Invoke ``tick()`` on every registered service that opted into ticking.

    Called by ``clawmes/services/cron.py``'s registered Hermes-cron handler
    on the configured cadence (default 60s). Errors are caught and logged so
    one slow / broken service doesn't starve the rest.
    """
    for svc in registry.iter_services():
        if not getattr(svc, "ticking", False):
            continue
        try:
            svc.tick()
        except Exception:
            _log.exception("service %s tick raised", svc.id)
