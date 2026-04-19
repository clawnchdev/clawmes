"""Event-sourced transaction ledger.

Every successful write tool appends a record to
``${HERMES_HOME}/clawmes/ledger/events.jsonl``. Records carry the action
context, the tx hash, the receipt summary, and a UTC timestamp. Queries
fold the log in-memory (with snapshot caching every N events) for P&L,
cost-basis, and search.
"""

from __future__ import annotations

from clawmes.ledger.tx_ledger import TxLedger, get_ledger, record_tx

__all__ = ["TxLedger", "get_ledger", "record_tx"]
