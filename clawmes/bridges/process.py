"""``BridgeProcess`` — long-lived Node subprocess with JSON-line RPC.

Manages spawn, stdio reader/writer threads, request/response matching,
notification dispatch, and graceful restart on crash. Per-bridge clients
(``wc_client``, ``sa_client``) wrap typed methods on top of this.

The subprocess is restarted with exponential backoff on crash. In-flight
requests are failed with ``BridgeError`` carrying a ``bridge_restarted``
code so callers can decide whether to retry.
"""

from __future__ import annotations

import json
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from queue import Queue
from typing import Any

from clawmes.lib.logger import logger_for

_log = logger_for("bridges.process")


class BridgeError(RuntimeError):
    """Raised when a bridge call fails (crash, timeout, RPC error)."""

    def __init__(self, code: str, message: str, *, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data


@dataclass
class Notification:
    """Server-pushed event from a bridge (no ``id`` field)."""

    method: str
    params: dict[str, Any]


class BridgeProcess:
    """Lifecycle wrapper around a single Node bridge subprocess.

    Stub at this milestone — start/stop succeed and call() raises a
    clear ``not_implemented`` error. The full impl (stdio reader/writer
    threads, futures-based request matching, exponential-backoff
    respawn) lands in the same milestone as the actual bridge sources.
    """

    def __init__(
        self,
        name: str,
        entry: Path,
        *,
        node_bin: str = "node",
        restart_on_crash: bool = True,
    ) -> None:
        self.name = name
        self.entry = entry
        self._node_bin = node_bin
        self._restart_on_crash = restart_on_crash
        self._proc: subprocess.Popen | None = None
        self._notifications: Queue[Notification] = Queue()
        self._lock = threading.RLock()

    def start(self) -> None:
        with self._lock:
            if self._proc and self._proc.poll() is None:
                return
            _log.info("starting bridge %s: %s %s", self.name, self._node_bin, self.entry)
            # TODO(v0.1.0): actual subprocess.Popen + reader/writer threads
            self._proc = None  # placeholder — real start lands with the bridge sources

    def stop(self) -> None:
        with self._lock:
            if self._proc and self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
            self._proc = None

    def call(self, method: str, params: dict[str, Any], *, timeout: float = 30.0) -> Any:
        """Send a request, wait for the matching response."""
        raise BridgeError(
            "not_implemented",
            f"BridgeProcess.call({method!r}) is stubbed at this milestone",
        )

    def notifications(self) -> "Queue[Notification]":
        return self._notifications

    def _enqueue_notification(self, raw_line: str) -> None:
        try:
            payload = json.loads(raw_line)
            if "id" in payload:
                # Response, not notification
                return
            self._notifications.put(Notification(method=payload["method"], params=payload.get("params", {})))
        except (json.JSONDecodeError, KeyError) as exc:
            _log.warning("bad notification on %s: %s", self.name, exc)
