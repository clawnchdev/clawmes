"""Clawmes logger — separate from Hermes' logger so output is clearly tagged.

Writes to:
  * stderr at INFO level (so it shows alongside Hermes output)
  * ``${HERMES_HOME}/clawmes/logs/clawmes.log`` at DEBUG level (full trace)

Module-specific logs (bridges, scheduler) get their own files via the
``logger_for(name)`` helper.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from clawmes.lib.paths import logs_dir

_INITIALIZED = False
_FORMATTER = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S%z",
)


def _ensure_initialized() -> None:
    global _INITIALIZED
    if _INITIALIZED:
        return

    root = logging.getLogger("clawmes")
    root.setLevel(logging.DEBUG)
    root.propagate = False

    # stderr handler — INFO and above
    stream = logging.StreamHandler(sys.stderr)
    stream.setLevel(logging.INFO)
    stream.setFormatter(_FORMATTER)
    root.addHandler(stream)

    # File handler — DEBUG and above
    try:
        log_path = logs_dir() / "clawmes.log"
        file = logging.FileHandler(log_path, encoding="utf-8")
        file.setLevel(logging.DEBUG)
        file.setFormatter(_FORMATTER)
        root.addHandler(file)
    except OSError:
        # Read-only filesystem or similar — keep stderr handler only
        pass

    _INITIALIZED = True


def logger_for(name: str) -> logging.Logger:
    """Return a child logger under the ``clawmes`` namespace.

    The returned logger emits to both stderr and the main log file. For
    components that want a separate file (e.g. bridge stdio capture),
    construct your own ``FileHandler`` and ``addHandler`` to the result.
    """
    _ensure_initialized()
    return logging.getLogger(f"clawmes.{name}")


def add_file_handler(name: str, path: Path, level: int = logging.DEBUG) -> None:
    """Attach a dedicated ``FileHandler`` to a child logger."""
    _ensure_initialized()
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setLevel(level)
    handler.setFormatter(_FORMATTER)
    logging.getLogger(f"clawmes.{name}").addHandler(handler)
