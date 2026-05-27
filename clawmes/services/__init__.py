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
    from clawmes.services.alerts_scheduler import get_alerts_scheduler_service
    from clawmes.services.bankr_service import get_bankr_service
    from clawmes.services.bv7x import get_bv7x_service
    from clawmes.services.clawnch import get_clawnch_service
    from clawmes.services.coingecko import get_coingecko_service
    from clawmes.services.command_history import get_command_history_service
    from clawmes.services.copy_trader import get_copy_trader_service
    from clawmes.services.credential_redactor import get_credential_redactor
    from clawmes.services.dca_scheduler import get_dca_scheduler_service
    from clawmes.services.endpoint_allowlist import get_endpoint_allowlist_service
    from clawmes.services.evolution_mode import get_evolution_mode_service
    from clawmes.services.explorer import get_explorer_service
    from clawmes.services.identity import get_identity_service
    from clawmes.services.lifi import get_lifi_service
    from clawmes.services.limit_order_scheduler import (
        get_limit_order_scheduler_service,
    )
    from clawmes.services.mode_service import get_mode_service
    from clawmes.services.onboarding_service import get_onboarding_service
    from clawmes.services.opengateway import get_opengateway_service
    from clawmes.services.persona_service import get_persona_service
    from clawmes.services.price import get_price_service
    from clawmes.services.rpc import get_rpc_service
    from clawmes.services.token_decimals import get_token_decimals_service
    from clawmes.services.token_gate import get_token_gate_service
    from clawmes.services.wallet import get_wallet_service
    from clawmes.services.wc_notifications import get_wc_notification_consumer
    from clawmes.services.zerox import get_zerox_service

    factories = [
        # 1. Security primitives — credential redactor must be live
        #    before any tool result flows through transform_tool_result.
        get_credential_redactor,
        # 1a. Endpoint allowlist — outbound HTTP guard. Read by
        #     clawmes.lib.http._check_allowlist; must be live before
        #     the first HTTP call any later service makes.
        get_endpoint_allowlist_service,
        # 2. Mode service — used by stage 1 of the @write_tool gate.
        #    Live before any tool dispatch.
        get_mode_service,
        # 2a. Persona service — read by the pre_llm_call hook to inject
        #     the active persona snippet into the per-turn context.
        get_persona_service,
        # 2b. Onboarding service — holds per-sender step state, capability
        #     picks, and step history for /skip and /back. Pure in-memory;
        #     persisted-to-disk variant is future work.
        get_onboarding_service,
        # 2c. Evolution-mode gate — read by agent_memory / skill_evolve
        #     write actions. Default disabled; user opts in via /evolve.
        get_evolution_mode_service,
        # 2d. Command-history ring — populated by slash-command handlers
        #     that opt in via record_command_call(); read by pre_llm_call
        #     so the agent sees what the user just ran.
        get_command_history_service,
        # 2e. Identity service — holds the agent's ed25519 keypair +
        #     did:key encoding. In-memory only in v1; persistence is
        #     follow-up work mirroring the wallet keystore.
        get_identity_service,
        # 3. RPC client — read-side foundation; many other services
        #    (token_decimals, wallet, balance tools) depend on it.
        get_rpc_service,
        # 4. Token decimals cache — depends on RPC.
        get_token_decimals_service,
        # 4b. Token gate — depends on RPC. Resolves wallet → tier
        #     based on $CLAWNCH balance. Gated features in /dca / /copy
        #     / /agent / /alerts read from this on each gated call.
        get_token_gate_service,
        # 4a. Block-explorer client — read-side, no dependencies; lives
        #     here so block_explorer tool dispatch is ready.
        get_explorer_service,
        # 5. Wallet — depends on credentials being readable; reads
        #    chain state via RPC at first use.
        get_wallet_service,
        # 6. Market data — exercised by tools and triggers.
        get_coingecko_service,
        get_price_service,  # depends on coingecko
        # 6a. Bankr custodial-wallet HTTP client — read-only without
        #     BANKR_API_KEY; signing requires it.
        get_bankr_service,
        # 6b. 0x DEX aggregator HTTP client. Read-only by definition;
        #     swaps are signed locally by the wallet mode.
        get_zerox_service,
        # 6c. LiFi cross-chain bridge aggregator. Same shape as 0x.
        get_lifi_service,
        # 6e. BV-7X autonomous BTC oracle (public REST API).
        #     Daily signal, regime, ETF flows, agent identity. The
        #     token-gated premium endpoints are deliberately NOT wired.
        get_bv7x_service,
        # 6f. Clawnch launchpad HTTP client. Powers /launch +
        #     /register_agent + the clawnch_launch / clawnch_fees tools.
        #     Reads work unauthenticated; deploys require CLAWNCH_API_KEY.
        get_clawnch_service,
        # 6d. OpenAI-compatible LLM gateway (gitlawb opengateway). Used
        #     by tools that need targeted inference outside the host
        #     Hermes agent loop. Independent from Hermes' main LLM —
        #     the agent's conversational inference is owned upstream.
        get_opengateway_service,
        # 7. Background daemons — last so they pick up everything above.
        get_scheduler,  # ticking=True; needs cron driver to actually fire
        # 7a. DCA scheduler — ticking=True. Fires due /dca schedules on
        #     the registry cadence (60s by default). Sync execution path;
        #     per-schedule errors caught + logged.
        get_dca_scheduler_service,
        # 7b. Copy-trader watcher — ticking=True. Polls Basescan for
        #     followed wallets' new ERC-20 receipts and submits copy
        #     buys at the configured fixed ETH amount per copy.
        get_copy_trader_service,
        # 7c. Alerts scheduler — ticking=True. Polls active alerts on
        #     each tick (price quotes via defi_price, wallet receipts
        #     via Basescan) and records fires. Notification delivery
        #     is downstream via the channel layer.
        get_alerts_scheduler_service,
        # 7d. Limit-order scheduler — ticking=True. Evaluates active
        #     orders against current USD prices and fires swaps via
        #     defi_swap when thresholds are crossed.
        get_limit_order_scheduler_service,
        get_wc_notification_consumer,  # threaded; routes WC bridge notifs
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
