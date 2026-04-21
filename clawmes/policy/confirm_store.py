"""One-time nonces for the ``confirm`` policy decision.

Flow:

  1. Tool gating evaluates a policy that returns ``confirm``.
  2. Gating calls :meth:`ConfirmStore.issue` with the action context;
     gets back a nonce string.
  3. Gating returns a ``POLICY HOLD`` error tool result instructing the
     LLM to show the user the action and retry with
     ``policyConfirmationNonce=<nonce>``.
  4. User confirms in chat; LLM retries the tool with the nonce arg.
  5. Gating calls :meth:`ConfirmStore.consume` with the action context
     and nonce. If it matches and isn't expired, gating proceeds.

Nonces are single-use. Expired entries are GC'd lazily on issue/consume.
"""

from __future__ import annotations

import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any

_DEFAULT_TTL_SECONDS = 600  # 10 min — the LLM has plenty of time to relay


@dataclass
class _Pending:
    nonce: str
    fingerprint: str
    expires_at: float


class ConfirmStore:
    """In-process nonce store. One per Hermes process."""

    def __init__(self, ttl_seconds: int = _DEFAULT_TTL_SECONDS) -> None:
        self._lock = threading.Lock()
        self._pending: list[_Pending] = []
        self._ttl_seconds = ttl_seconds

    def issue(self, action_ctx: Any) -> str:
        """Generate a fresh nonce bound to ``action_ctx``."""
        nonce = secrets.token_urlsafe(16)
        fp = self._fingerprint(action_ctx)
        now = time.monotonic()
        with self._lock:
            self._gc_locked(now)
            self._pending.append(
                _Pending(nonce=nonce, fingerprint=fp, expires_at=now + self._ttl_seconds)
            )
        return nonce

    def consume(self, action_ctx: Any, nonce: str) -> bool:
        """Return True iff ``nonce`` was issued for an equivalent action and is unexpired."""
        fp = self._fingerprint(action_ctx)
        now = time.monotonic()
        with self._lock:
            self._gc_locked(now)
            for i, p in enumerate(self._pending):
                if p.nonce == nonce and p.fingerprint == fp:
                    self._pending.pop(i)
                    return True
        return False

    def _gc_locked(self, now: float) -> None:
        self._pending = [p for p in self._pending if p.expires_at > now]

    @staticmethod
    def _fingerprint(action_ctx: Any) -> str:
        """Stable digest of the action so retries with same args match."""
        # Tool name + frozen args (excluding the nonce field itself)
        tool = getattr(action_ctx, "tool_name", str(action_ctx))
        args = getattr(action_ctx, "args", {}) or {}
        rest = sorted((k, repr(v)) for k, v in args.items() if k != "policyConfirmationNonce")
        return f"{tool}::{rest}"


# Module-level singleton; tools share it.
store = ConfirmStore()
