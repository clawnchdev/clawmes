"""Tool implementations.

Each tool lives in its own module (``transfer.py``, ``defi_swap.py``, ...) and
exports a ``register(ctx)`` function that wires the tool into Hermes via
``ctx.register_tool``. The decorators in :mod:`clawmes.tools.registry`
generate the metadata that ``register`` consumes.

Top-level :func:`register_all` is called from the plugin's ``register(ctx)``
entry point in ``clawmes/__init__.py``.
"""

from __future__ import annotations

from clawmes.tools.registry import (
    WRITE_TOOL_NAMES,
    read_tool,
    write_tool,
)

__all__ = ["WRITE_TOOL_NAMES", "read_tool", "register_all", "write_tool"]


def register_all(ctx) -> None:
    """Register every clawmes tool with the Hermes plugin context.

    Order matters: registration order = order in the LLM-visible tool
    catalog. Most-used tools first. The full 48-tool roster comes online
    across v0.1.0 → v0.5.0; this list grows as each tool module lands.
    """
    from clawmes.tools import (
        _user_tools,
        agent_memory,
        airdrop,
        analytics,
        approvals,
        bankr_automate,
        bankr_launch,
        bankr_leverage,
        bankr_polymarket,
        block_explorer,
        bridge,
        clawnch_fees,
        clawnch_launch,
        clawnchconnect,
        clawnx,
        cost_basis,
        defi_balance,
        defi_lend,
        defi_price,
        defi_stake,
        defi_swap,
        farcaster,
        giza,
        governance,
        herd_intelligence,
        hummingbot,
        lobster_cash,
        market_intel,
        molten,
        nft,
        nookplot,
        paysponge,
        privacy,
        safe,
        session_recall,
        skill_evolve,
        transfer,
        watch_activity,
        wayfinder,
        yield_farming,
    )

    # clawnchconnect first — the LLM should reach for it whenever a
    # write tool errors with wallet_not_connected, so it benefits from
    # being early in the tool catalog.
    for mod in (
        clawnchconnect,
        transfer,
        defi_swap,
        defi_lend,
        defi_stake,
        bridge,
        approvals,
        defi_price,
        defi_balance,
        yield_farming,
        nft,
        safe,
        governance,
        farcaster,
        airdrop,
        watch_activity,
        clawnch_launch,
        clawnch_fees,
        bankr_launch,
        bankr_automate,
        bankr_polymarket,
        bankr_leverage,
        paysponge,
        lobster_cash,
        analytics,
        market_intel,
        cost_basis,
        block_explorer,
        molten,
        clawnx,
        hummingbot,
        wayfinder,
        giza,
        nookplot,
        privacy,
        herd_intelligence,
        agent_memory,
        skill_evolve,
        session_recall,
        _user_tools,
    ):
        mod.register(ctx)
