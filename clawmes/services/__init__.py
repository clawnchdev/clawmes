"""Background services with managed lifecycles.

The plugin ``register(ctx)`` entry point calls :func:`start_all` after
hooks/tools/commands are wired. Atexit + signal handlers in ``clawmes/__init__``
ensure :func:`stop_all` runs on graceful shutdown.

Services start in dependency order (encryption → credentials → rpc → wallet
→ market data → DEX/lending → background daemons). Services stop in
reverse order.
"""

from __future__ import annotations

from clawmes.lib.logger import logger_for
from clawmes.services._base import Service
from clawmes.services.registry import registry, tick_all

_log = logger_for("services")

__all__ = ["Service", "registry", "start_all", "stop_all", "tick_all"]


def start_all() -> None:
    """Start every clawmes service in topological order.

    Stub at this milestone — individual service modules are added in
    subsequent commits and registered here as they land. The plugin still
    boots cleanly with no services running; tools / commands degrade
    gracefully.
    """
    _log.info("clawmes.services.start_all() — no services registered yet")


def stop_all() -> None:
    """Stop every registered service in reverse registration order.

    Each service's ``stop()`` is wrapped in try/except so one failure does
    not block the rest.
    """
    services = list(registry.iter_services())
    for svc in reversed(services):
        try:
            svc.stop()
        except Exception:
            _log.exception("service %s stop raised", svc.id)
