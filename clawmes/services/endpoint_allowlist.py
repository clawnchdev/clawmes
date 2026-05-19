"""Endpoint-allowlist service — runtime + audit surface for outbound HTTP.

The static allowlist in :mod:`clawmes.lib.http` (``_DEFAULT_ALLOWLIST``)
already blocks outbound HTTP to unknown hosts — that's the primary
prompt-injection defense. This service layers two additional
capabilities on top:

  1. **Runtime user-added hosts.** Users can temporarily allow a host
     for the current session via ``/allow <host>`` without editing
     code or config. State is in-memory only — restart returns to the
     curated default set. This is intentional: an attacker who tricks
     the agent into adding a host can't keep it added across
     processes.
  2. **Audit ring buffer.** Every blocked attempt is recorded with
     timestamp, host, and URL. Users can review via ``/allowlist`` to
     see what the LLM has been trying to reach. The buffer is bounded
     (default 100 entries) and FIFO — old entries roll off.

``clawmes.lib.http._check_allowlist`` consults this service after
checking the static defaults and per-call ``extra_hosts`` — services
that want short-lived per-call exceptions still pass ``extra_hosts=``
to ``http_get`` / ``http_post`` (the existing pattern); users that
want session-scoped exceptions add via ``/allow``.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any

from clawmes.lib.logger import logger_for
from clawmes.services._base import Service

_log = logger_for("services.endpoint_allowlist")

_DEFAULT_RING_SIZE = 100


class EndpointAllowlistService(Service):
    id = "clawmes.endpoint_allowlist"

    def __init__(self, *, ring_size: int = _DEFAULT_RING_SIZE) -> None:
        self._lock = threading.Lock()
        self._user_hosts: set[str] = set()
        self._blocks: deque[tuple[float, str, str]] = deque(maxlen=ring_size)

    def start(self) -> None:
        pass

    def stop(self) -> None:
        with self._lock:
            self._user_hosts.clear()
            self._blocks.clear()

    # --- user-host CRUD --------------------------------------------------

    def add_host(self, host: str) -> bool:
        """Add a host to the user allowlist. Returns True if newly added,
        False if already present. Raises :class:`ValueError` for empty
        input.
        """
        normalized = self._normalize(host)
        with self._lock:
            if normalized in self._user_hosts:
                return False
            self._user_hosts.add(normalized)
        _log.info("user allowlist add: %r", normalized)
        return True

    def remove_host(self, host: str) -> bool:
        """Remove a user-added host. Returns True if removed, False if
        the host wasn't in the user set. Defaults are not removable via
        this surface (they live in ``lib.http._DEFAULT_ALLOWLIST``).
        """
        normalized = self._normalize(host)
        with self._lock:
            if normalized not in self._user_hosts:
                return False
            self._user_hosts.discard(normalized)
        _log.info("user allowlist remove: %r", normalized)
        return True

    def list_user_hosts(self) -> frozenset[str]:
        """Snapshot of the user-added host set."""
        with self._lock:
            return frozenset(self._user_hosts)

    def is_allowed(self, host: str) -> bool:
        """True iff the host is in the user allowlist (does not check
        defaults — callers consult those separately).
        """
        normalized = self._normalize(host, allow_empty=True)
        if not normalized:
            return False
        with self._lock:
            return normalized in self._user_hosts

    # --- audit -----------------------------------------------------------

    def record_block(self, url: str, host: str) -> None:
        """Append a blocked request to the audit ring."""
        with self._lock:
            self._blocks.append((time.time(), host, url))

    def recent_blocks(self, limit: int = 10) -> list[dict[str, Any]]:
        """Return up to ``limit`` most-recent blocked attempts, newest first."""
        if limit <= 0:
            return []
        with self._lock:
            snapshot = list(self._blocks)
        recent = snapshot[-limit:][::-1]
        return [{"timestamp": ts, "host": host, "url": url} for ts, host, url in recent]

    # --- internal --------------------------------------------------------

    @staticmethod
    def _normalize(host: str, *, allow_empty: bool = False) -> str:
        if not isinstance(host, str):
            raise ValueError(f"host must be a string, got {type(host).__name__}")
        normalized = host.strip().lower()
        if not normalized:
            if allow_empty:
                return ""
            raise ValueError("host cannot be empty")
        return normalized


_instance: EndpointAllowlistService | None = None


def get_endpoint_allowlist_service() -> EndpointAllowlistService:
    global _instance
    if _instance is None:
        _instance = EndpointAllowlistService()
    return _instance
