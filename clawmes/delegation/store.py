"""Persistence for signed delegations.

One JSON file per delegation under ``${HERMES_HOME}/clawmes/delegations/``.
Each file holds the full :class:`clawmes.delegation.types.DelegationRecord`
(signed struct + metadata) needed to redeem or revoke on-chain. Bigints
(salt) are stored as hex strings; caveats keep their hex terms/args.

Follows the clawmes storage conventions: atomic write via tmp+rename, mode
0600, an in-memory cache, and corruption quarantine (a bad file is renamed
``.corrupt`` rather than deleted — the on-chain delegation still exists, so
losing the file would make it unrevokable through clawmes).
"""

from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path

from clawmes.delegation.types import (
    Caveat,
    DelegationRecord,
    SignedDelegation,
)
from clawmes.lib.logger import logger_for
from clawmes.lib.paths import state_dir

_log = logger_for("delegation.store")


def delegations_dir() -> Path:
    return state_dir("delegations")


def _safe_id(record_id: str) -> str:
    return "".join(c if (c.isalnum() or c in "_-") else "_" for c in record_id)


def _record_path(record_id: str) -> Path:
    return delegations_dir() / f"{_safe_id(record_id)}.json"


# ─── (de)serialization ──────────────────────────────────────────────────


def record_to_dict(record: DelegationRecord) -> dict:
    d = record.delegation
    return {
        "id": record.id,
        "chain_id": record.chain_id,
        "status": record.status,
        "hash": record.hash,
        "policy_name": record.policy_name,
        "tools": list(record.tools),
        "permissions_context": record.permissions_context,
        "expires_at": record.expires_at,
        "unmapped": list(record.unmapped),
        "created_at": record.created_at,
        "last_checked_at": record.last_checked_at,
        "kind": record.kind,
        "delegation": {
            "delegate": d.delegate,
            "delegator": d.delegator,
            "authority": d.authority,
            "caveats": [
                {"enforcer": c.enforcer, "terms": c.terms, "args": c.args} for c in d.caveats
            ],
            "salt": hex(d.salt),
            "signature": d.signature,
        },
    }


def record_from_dict(data: dict) -> DelegationRecord:
    d = data["delegation"]
    delegation = SignedDelegation(
        delegate=d["delegate"],
        delegator=d["delegator"],
        authority=d["authority"],
        caveats=tuple(
            Caveat(enforcer=c["enforcer"], terms=c["terms"], args=c.get("args", "0x"))
            for c in d["caveats"]
        ),
        salt=int(d["salt"], 16) if isinstance(d["salt"], str) else int(d["salt"]),
        signature=d["signature"],
    )
    return DelegationRecord(
        id=str(data["id"]),
        chain_id=int(data["chain_id"]),
        delegation=delegation,
        status=data.get("status", "signed"),
        hash=data.get("hash", "0x"),
        policy_name=data.get("policy_name", ""),
        tools=tuple(data.get("tools", ()) or ()),
        permissions_context=data.get("permissions_context", ""),
        expires_at=data.get("expires_at", ""),
        unmapped=tuple(data.get("unmapped", ()) or ()),
        created_at=data.get("created_at", ""),
        last_checked_at=data.get("last_checked_at", ""),
        kind=data.get("kind", "eip7710"),
    )


# ─── store ──────────────────────────────────────────────────────────────


class DelegationStore:
    """File-backed store of signed delegations, keyed by record id."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._cache: dict[str, DelegationRecord] = {}

    def save(self, record: DelegationRecord) -> None:
        if not record.created_at:
            record.created_at = datetime.now(tz=UTC).isoformat()
        with self._lock:
            self._cache[record.id] = record
            path = _record_path(record.id)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(record_to_dict(record), indent=2), encoding="utf-8")
            os.chmod(tmp, 0o600)
            tmp.replace(path)
        _log.info("saved delegation %s (chain %d)", record.id, record.chain_id)

    def load(self, record_id: str) -> DelegationRecord | None:
        with self._lock:
            cached = self._cache.get(record_id)
            if cached is not None:
                return cached
            path = _record_path(record_id)
            if not path.exists():
                return None
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                record = record_from_dict(data)
            except (OSError, ValueError, KeyError) as exc:
                _log.error("delegation %s unreadable (%s); quarantining", record_id, exc)
                self._quarantine(path)
                return None
            self._cache[record_id] = record
            return record

    def has(self, record_id: str) -> bool:
        with self._lock:
            return record_id in self._cache or _record_path(record_id).exists()

    def delete(self, record_id: str) -> bool:
        with self._lock:
            self._cache.pop(record_id, None)
            path = _record_path(record_id)
            if path.exists():
                try:
                    path.unlink()
                    return True
                except OSError:
                    _log.exception("failed to unlink delegation %s", record_id)
                    return False
            return False

    def list_records(self) -> list[DelegationRecord]:
        """Return all persisted records (disk is the source of truth)."""
        out: list[DelegationRecord] = []
        seen: set[str] = set()
        with self._lock:
            directory = delegations_dir()
            for path in sorted(directory.glob("*.json")):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    record = record_from_dict(data)
                except (OSError, ValueError, KeyError) as exc:
                    _log.error("skipping unreadable %s (%s)", path.name, exc)
                    self._quarantine(path)
                    continue
                self._cache[record.id] = record
                seen.add(record.id)
                out.append(record)
            # Include cache-only records (saved but somehow not on disk).
            for rid, rec in self._cache.items():
                if rid not in seen:
                    out.append(rec)
        return out

    def reset(self) -> None:
        with self._lock:
            self._cache.clear()

    @staticmethod
    def _quarantine(path: Path) -> None:
        try:
            stamp = int(datetime.now(tz=UTC).timestamp())
            path.rename(path.with_suffix(f".corrupt.{stamp}"))
        except OSError:
            _log.exception("failed to quarantine %s", path)


_instance: DelegationStore | None = None


def get_delegation_store() -> DelegationStore:
    global _instance
    if _instance is None:
        _instance = DelegationStore()
    return _instance
