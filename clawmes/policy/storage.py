"""Policy storage — JSON persistence under ``${HERMES_HOME}/clawmes/policy/policies.json``.

On first read the file is created from
:data:`clawmes.policy.types.DEFAULT_POLICIES`. Users editing the file
directly is supported; we re-read on every ``load_policies()`` call so
edits land without a restart.
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict
from pathlib import Path

from clawmes.lib.logger import logger_for
from clawmes.lib.paths import policy_dir
from clawmes.policy.types import DEFAULT_POLICIES, Policy

_log = logger_for("policy.storage")
_lock = threading.RLock()


def policies_path() -> Path:
    return policy_dir() / "policies.json"


def load_policies() -> list[Policy]:
    """Read the persisted policy list.

    First run: writes the bundled default set, returns it.
    Subsequent: re-reads from disk. Malformed entries are skipped
    with a warning so a single bad edit doesn't disable all policies.
    """
    with _lock:
        path = policies_path()
        if not path.exists():
            save_policies(list(DEFAULT_POLICIES))
            return list(DEFAULT_POLICIES)

        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            _log.warning("policies.json unreadable (%s); falling back to defaults", exc)
            return list(DEFAULT_POLICIES)

        if not isinstance(raw, list):
            _log.warning("policies.json must be a list; got %s", type(raw).__name__)
            return list(DEFAULT_POLICIES)

        out: list[Policy] = []
        for entry in raw:
            policy = _decode(entry)
            if policy is not None:
                out.append(policy)
        return out


def save_policies(policies: list[Policy]) -> None:
    """Atomically write the policy list to disk.

    Uses ``write_text`` rather than rename-on-write because policies
    are small enough that a torn write is recoverable from the default
    set on next read. Locks against concurrent saves.
    """
    with _lock:
        path = policies_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = [_encode(p) for p in policies]
        path.write_text(
            json.dumps(payload, indent=2, sort_keys=False),
            encoding="utf-8",
        )


def _encode(policy: Policy) -> dict:
    """Convert a Policy to a JSON-friendly dict."""
    out = asdict(policy)
    # asdict turns tuple → list automatically — fine for JSON
    return out


def _decode(entry: dict) -> Policy | None:
    """Rehydrate a JSON dict into a Policy, returning None on bad input."""
    if not isinstance(entry, dict):
        _log.warning("policy entry is not a dict: %r", entry)
        return None
    try:
        return Policy(
            name=str(entry["name"]),
            decision=entry["decision"],
            applies_to_tools=tuple(entry.get("applies_to_tools") or ()),
            chain_ids=tuple(entry.get("chain_ids") or ()),
            max_amount_wei=_int_or_none(entry.get("max_amount_wei")),
            max_per_hour=_int_or_none(entry.get("max_per_hour")),
            description=str(entry.get("description") or ""),
        )
    except (KeyError, TypeError, ValueError) as exc:
        _log.warning("skipping malformed policy entry: %s", exc)
        return None


def _int_or_none(value) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
