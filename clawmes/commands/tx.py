"""Transaction history commands: ``/tx``, ``/tx_search``, ``/tx_export``, ``/pending``.

Reads from the event-sourced ledger at
``${HERMES_HOME}/clawmes/ledger/events.jsonl``. Every successful write
tool's invocation is recorded by the ``post_tool_call`` hook and shows
up here.
"""

from __future__ import annotations

import json

from clawmes.ledger.tx_ledger import TxRecord, get_ledger
from clawmes.lib.addr import short

_DEFAULT_RECENT = 10
_MAX_RECENT = 50


async def handle_tx(raw_args: str) -> str:
    arg = raw_args.strip()
    records = get_ledger().iter_records()
    if not records:
        return "No transactions recorded yet."

    if arg:
        # Lookup by tx hash (allow partial / no-prefix match)
        return _format_one(_find_by_hash(records, arg), arg)

    # No args — show most recent N
    recent = records[-_DEFAULT_RECENT:]
    return _format_recent(recent, total=len(records))


async def handle_tx_search(raw_args: str) -> str:
    query = raw_args.strip()
    if not query:
        return "Usage: /tx_search <token | recipient | action>"

    records = get_ledger().iter_records()
    matches = [r for r in records if _matches_query(r, query)]
    if not matches:
        return f"No transactions match {query!r}."

    return _format_recent(matches[-_MAX_RECENT:], total=len(matches))


async def handle_tx_export(raw_args: str) -> str:
    """Export full ledger as one JSON document. ``raw_args`` is currently ignored."""
    records = get_ledger().iter_records()
    if not records:
        return "No transactions to export."

    payload = [_record_to_dict(r) for r in records]
    return f"Exporting {len(records)} record(s):\n```json\n{json.dumps(payload, indent=2)}\n```"


async def handle_pending(raw_args: str) -> str:
    """Pending tx queue.

    The ``submitted`` status in the ledger means "tx hash returned, no
    receipt yet". v0.1.0 records every write as ``submitted`` and
    leaves status updates to a future commit; once we have receipt
    polling, this command will show genuine pending entries.
    """
    records = get_ledger().iter_records()
    pending = [r for r in records if r.status == "submitted"]
    if not pending:
        return "Pending tx queue: (empty)"
    return _format_recent(pending[-_MAX_RECENT:], total=len(pending))


# --- formatting helpers ---------------------------------------------------


def _format_recent(records: list[TxRecord], *, total: int) -> str:
    if not records:
        return "No transactions."
    lines = []
    for r in records:
        ts = r.ts.replace("Z", "").replace("T", " ")
        hash_suffix = f"  {short(r.tx_hash)}" if r.tx_hash else ""
        chain_suffix = f"  chain={r.chain_id}" if r.chain_id else ""
        lines.append(f"  {ts}  {r.tool_name:<14}  {r.status}{hash_suffix}{chain_suffix}")

    header = f"Showing {len(records)} of {total} transaction(s):"
    return header + "\n" + "\n".join(lines)


def _format_one(record: TxRecord | None, query: str) -> str:
    if record is None:
        return f"No transaction found matching {query!r}."
    return (
        f"Transaction {record.tx_hash or '(no hash)'}\n"
        f"  Time:   {record.ts}\n"
        f"  Tool:   {record.tool_name}\n"
        f"  Chain:  {record.chain_id or '(unknown)'}\n"
        f"  Status: {record.status}\n"
        f"  From:   {record.from_addr or '(unknown)'}\n"
        f"  To:     {record.to_addr or '(unknown)'}\n"
        f"  Value:  {record.value_wei or '(unknown)'} wei\n"
        f"  Args:   {json.dumps(record.action_args, default=str)}"
    )


def _find_by_hash(records: list[TxRecord], query: str) -> TxRecord | None:
    """Match by full tx hash, prefix, or stripped 0x-prefix."""
    q = query.lower().lstrip("0x")
    for r in reversed(records):  # search newest first
        if not r.tx_hash:
            continue
        h = r.tx_hash.lower().lstrip("0x")
        if h == q or h.startswith(q):
            return r
    return None


def _matches_query(r: TxRecord, query: str) -> bool:
    """Naive substring match across the searchable fields."""
    q = query.lower()
    haystacks = [
        r.tool_name,
        r.tx_hash or "",
        r.from_addr or "",
        r.to_addr or "",
        json.dumps(r.action_args, default=str),
    ]
    return any(q in field.lower() for field in haystacks)


def _record_to_dict(r: TxRecord) -> dict:
    from dataclasses import asdict

    return asdict(r)


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
