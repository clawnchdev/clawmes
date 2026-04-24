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

    Order rules (per PRD §10.16):

      1. Foundational primitives first — RPC, ENS, registries.
      2. Wallet next — depends on credentials being readable.
      3. Market data next — gets exercised by tools and triggers.
      4. DEX/lending/yield services after.
      5. Background daemons last — scheduler, heartbeat, monitors.

    Each ``factory()`` is the singleton accessor for one service. We call
    it to materialize the instance, register it, then ``start()``. New
    services land here as they're built; tools that need an absent
    service degrade with a clear ``not_implemented`` error rather than
    crashing.
    """
    from clawmes.plans.scheduler import get_scheduler
    from clawmes.services.coingecko import get_coingecko_service
    from clawmes.services.credential_redactor import get_credential_redactor
    from clawmes.services.price import get_price_service
    from clawmes.services.wallet import get_wallet_service

    factories = [
        # 1. Security primitives — credential redactor must be live
        #    before any tool result flows through transform_tool_result.
        get_credential_redactor,
        # 2. Foundational singletons — wallet next so anything that
        #    reads wallet state during start has it ready.
        get_wallet_service,
        # 3. Market data — exercised by tools and triggers
        get_coingecko_service,
        get_price_service,  # depends on coingecko
        # 4. Background daemons — last so they pick up everything above
        get_scheduler,  # ticking=True; needs cron driver to actually fire
    ]
    for factory in factories:
        svc = factory()
        registry.register(svc)
        try:
            svc.start()
        except Exception:
            _log.exception("service %s start raised; continuing", svc.id)


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
