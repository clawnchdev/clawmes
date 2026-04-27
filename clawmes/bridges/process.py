"""``BridgeProcess`` — long-lived Node subprocess with JSON-line RPC.

Manages spawn, stdio reader / writer threads, future-based request /
response matching, notification dispatch, and graceful shutdown. Per-
bridge clients (``wc_client``, ``sa_client``) wrap typed methods on
top of this.

Wire format (one record per line, terminated with ``\\n``):

  Request:      ``{"id": "<uuid>", "method": "<name>", "params": {...}}``
  Response OK:  ``{"id": "<uuid>", "result": <any>}``
  Response err: ``{"id": "<uuid>", "error": {"code": "<str>", ...}}``
  Notification: ``{"method": "<event>", "params": {...}}`` (no id)

Concurrency model:

  * One **reader thread** per process — pulls lines from stdout,
    parses, dispatches to either the matching pending future
    (response) or the notification queue.
  * The **caller** writes to stdin under a lock, then waits on a
    ``threading.Event`` until the reader resolves it.
  * No event loop — callers can be sync or async; the bridge blocks
    until response arrives or the timeout fires.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
import uuid
from dataclasses import dataclass, field
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


@dataclass
class _PendingCall:
    event: threading.Event = field(default_factory=threading.Event)
    result: Any = None
    error: BridgeError | None = None


class BridgeProcess:
    """Lifecycle wrapper around a single Node bridge subprocess."""

    def __init__(
        self,
        name: str,
        entry: Path,
        *,
        node_bin: str | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self.name = name
        self.entry = entry
        self._node_bin = node_bin or shutil.which("node") or "node"
        self._env = env
        self._proc: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None
        self._stderr_reader: threading.Thread | None = None
        self._writer_lock = threading.Lock()
        self._pending: dict[str, _PendingCall] = {}
        self._pending_lock = threading.Lock()
        self._notifications: Queue[Notification] = Queue()
        self._lock = threading.RLock()
        self._stopping = False

    # ----- lifecycle ----------------------------------------------------

    def start(self) -> None:
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return
            _log.info("starting bridge %s: %s %s", self.name, self._node_bin, self.entry)
            self._stopping = False
            self._proc = subprocess.Popen(
                [self._node_bin, str(self.entry)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=1,  # line-buffered
                text=True,
                env=self._env,
            )
            self._reader = threading.Thread(
                target=self._read_stdout,
                name=f"{self.name}-reader",
                daemon=True,
            )
            self._reader.start()
            self._stderr_reader = threading.Thread(
                target=self._read_stderr,
                name=f"{self.name}-stderr",
                daemon=True,
            )
            self._stderr_reader.start()

    def stop(self) -> None:
        with self._lock:
            self._stopping = True
            if self._proc is None:
                return
            if self._proc.poll() is None:
                try:
                    self._proc.terminate()
                    self._proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
                    try:
                        self._proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        pass
            # Fail every pending call with a clean error
            with self._pending_lock:
                for call in self._pending.values():
                    call.error = BridgeError("bridge_stopped", f"bridge {self.name} stopped")
                    call.event.set()
                self._pending.clear()
            self._proc = None

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    # ----- request / response ------------------------------------------

    def call(self, method: str, params: dict[str, Any], *, timeout: float = 30.0) -> Any:
        """Send a request, wait for the matching response."""
        if not self.is_running():
            raise BridgeError("not_running", f"bridge {self.name} is not running")

        request_id = str(uuid.uuid4())
        call = _PendingCall()
        with self._pending_lock:
            self._pending[request_id] = call

        line = json.dumps({"id": request_id, "method": method, "params": params})
        try:
            with self._writer_lock:
                assert self._proc is not None and self._proc.stdin is not None
                self._proc.stdin.write(line + "\n")
                self._proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            with self._pending_lock:
                self._pending.pop(request_id, None)
            raise BridgeError(
                "write_failed", f"bridge {self.name} stdin write failed: {exc}"
            ) from exc

        if not call.event.wait(timeout):
            with self._pending_lock:
                self._pending.pop(request_id, None)
            raise BridgeError(
                "timeout", f"bridge {self.name} timeout after {timeout}s on {method!r}"
            )

        if call.error is not None:
            raise call.error
        return call.result

    def notifications(self) -> Queue[Notification]:
        return self._notifications

    # ----- internals ---------------------------------------------------

    def _read_stdout(self) -> None:
        """Reader thread — parses each line, routes to pending future or notification queue."""
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        for raw in iter(proc.stdout.readline, ""):
            line = raw.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                _log.warning("%s: malformed line: %r", self.name, line[:200])
                continue
            self._handle_payload(payload)
        # Reader exiting — process closed stdout. If we have pending
        # calls and we're not in a stop() pathway, surface that as an
        # error.
        if not self._stopping:
            with self._pending_lock:
                pending = list(self._pending.items())
                self._pending.clear()
            for request_id, call in pending:
                call.error = BridgeError(
                    "bridge_crashed",
                    f"bridge {self.name} stdout closed with pending request {request_id}",
                )
                call.event.set()

    def _read_stderr(self) -> None:
        """Stderr drain — tag every line with the bridge name and forward to logger."""
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        for raw in iter(proc.stderr.readline, ""):
            line = raw.strip()
            if line:
                _log.warning("%s [stderr]: %s", self.name, line)

    def _handle_payload(self, payload: Any) -> None:
        if not isinstance(payload, dict):
            _log.warning("%s: non-dict payload: %r", self.name, payload)
            return
        request_id = payload.get("id")
        if request_id is None:
            self._enqueue_notification(payload)
            return
        with self._pending_lock:
            call = self._pending.pop(str(request_id), None)
        if call is None:
            _log.debug("%s: response for unknown id %r", self.name, request_id)
            return
        if "error" in payload and payload["error"]:
            err = payload["error"]
            if isinstance(err, dict):
                call.error = BridgeError(
                    code=str(err.get("code", "unknown")),
                    message=str(err.get("message", "")),
                    data=err.get("data"),
                )
            else:
                call.error = BridgeError("unknown", str(err))
        else:
            call.result = payload.get("result")
        call.event.set()

    def _enqueue_notification(self, payload: dict[str, Any]) -> None:
        method = payload.get("method")
        if not isinstance(method, str):
            _log.warning("%s: notification missing method: %r", self.name, payload)
            return
        params = payload.get("params") or {}
        if not isinstance(params, dict):
            params = {}
        self._notifications.put(Notification(method=method, params=params))
