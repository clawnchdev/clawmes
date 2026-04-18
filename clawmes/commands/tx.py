"""Transaction history commands: ``/tx``, ``/tx_search``, ``/tx_export``, ``/pending``."""

from __future__ import annotations


async def handle_tx(raw_args: str) -> str:
    arg = raw_args.strip()
    if arg:
        return f"Tx detail lookup for {arg!r} not yet implemented at this milestone."
    # No args — show recent.
    return "Recent transactions: (none — ledger empty)"


async def handle_tx_search(raw_args: str) -> str:
    query = raw_args.strip()
    if not query:
        return "Usage: /tx_search <token | recipient | action>"
    return f"Search not yet implemented at this milestone. Query: {query!r}"


async def handle_tx_export(raw_args: str) -> str:
    return "Tx export not yet implemented at this milestone."


async def handle_pending(raw_args: str) -> str:
    return "Pending tx queue: (empty)"


def register(ctx) -> None:
    ctx.register_command(
        name="tx",
        handler=handle_tx,
        description="Show recent transactions, or details for a specific hash",
        args_hint="[hash]",
    )
    ctx.register_command(
        name="tx_search",
        handler=handle_tx_search,
        description="Search transaction history by token, recipient, or action",
        args_hint="<query>",
    )
    ctx.register_command(
        name="tx_export",
        handler=handle_tx_export,
        description="Export transaction history to CSV/JSON",
        args_hint="[range]",
    )
    ctx.register_command(
        name="pending",
        handler=handle_pending,
        description="Show pending tx queue",
    )
