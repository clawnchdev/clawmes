"""Append-only transaction ledger.

Storage: ``${HERMES_HOME}/clawmes/ledger/events.jsonl`` — one JSON
record per line, never modified after write. Recovery / search reads the
file forward; large installs get a snapshot file written every N events
to keep query startup fast.

Record shape:

.. code-block:: json

    {
      "ts":          "2026-04-23T22:51:09Z",
      "session_id":  "...",
      "user_id":     "...",
      "tool_name":   "transfer",
      "action_args": {...},
      "tx_hash":     "0x...",
      "chain_id":    8453,
      "from_addr":   "0x...",
      "to_addr":     "0x...",
      "value_wei":   "...",
      "status":      "ok" | "reverted" | "submitted"
    }
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from clawmes.lib.logger import logger_for
from clawmes.lib.paths import ledger_dir

_log = logger_for("ledger.tx_ledger")


@dataclass(frozen=True)
class TxRecord:
    ts: str
    session_id: str
    user_id: str
    tool_name: str
    action_args: dict[str, Any] = field(default_factory=dict)
    tx_hash: str | None = None
    chain_id: int | None = None
    from_addr: str | None = None
    to_addr: str | None = None
    value_wei: str | None = None
    status: str = "submitted"


class TxLedger:
    def __init__(self, path: Path | None = None) -> None:
        self._lock = threading.Lock()
        self._path = path or (ledger_dir() / "events.jsonl")

    @property
    def path(self) -> Path:
        return self._path

    def append(self, record: TxRecord) -> None:
        line = json.dumps(asdict(record), separators=(",", ":"))
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(line)
                fh.write("\n")

    def iter_records(self) -> list[TxRecord]:
        if not self._path.exists():
            return []
        out: list[TxRecord] = []
        with self._path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    out.append(TxRecord(**data))
                except (json.JSONDecodeError, TypeError) as exc:
                    _log.warning("skipping bad ledger line: %s", exc)
        return out


_instance: TxLedger | None = None


def get_ledger() -> TxLedger:
    global _instance
    if _instance is None:
        _instance = TxLedger()
    return _instance


def record_tx(
    *,
    session_id: str,
    user_id: str,
    tool_name: str,
    action_args: dict[str, Any],
    tx_hash: str | None = None,
    chain_id: int | None = None,
    from_addr: str | None = None,
    to_addr: str | None = None,
    value_wei: str | None = None,
    status: str = "submitted",
) -> None:
    """Append a record. Convenience wrapper around ``get_ledger().append``."""
    record = TxRecord(
        ts=datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z"),
        session_id=session_id,
        user_id=user_id,
        tool_name=tool_name,
        action_args=action_args,
        tx_hash=tx_hash,
        chain_id=chain_id,
        from_addr=from_addr,
        to_addr=to_addr,
        value_wei=value_wei,
        status=status,
    )
    get_ledger().append(record)
