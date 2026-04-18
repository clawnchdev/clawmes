"""``on_session_*`` hooks — session lifecycle.

Four callbacks bound to four Hermes hook names:

  * :func:`on_start`    — first turn of a fresh session
  * :func:`on_end`      — final turn before tear-down
  * :func:`on_finalize` — last chance to flush state (fires before reset
    on swap)
  * :func:`on_reset`    — session ID just changed, prepare for new one

Reset order in Hermes: ``finalize(old_id)`` → swap → ``reset(new_id)`` →
``start(new_id)`` on first inbound turn.
"""

from __future__ import annotations

from typing import Any

from clawmes.lib.logger import logger_for

_log = logger_for("hooks.on_session")


def on_start(*, session_id: str | None = None, **kwargs: Any) -> None:
    _log.debug("session start: %s", session_id)
    # TODO(v0.1.0): cost basis ingest of any deferred swaps from previous session


def on_end(*, session_id: str | None = None, **kwargs: Any) -> None:
    _log.debug("session end: %s", session_id)


def on_finalize(*, session_id: str | None = None, **kwargs: Any) -> None:
    _log.debug("session finalize: %s", session_id)
    # TODO(v0.1.0): flush ledger, persist session-recall index updates


def on_reset(*, session_id: str | None = None, **kwargs: Any) -> None:
    _log.debug("session reset: %s", session_id)
