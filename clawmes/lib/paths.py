"""Path helpers — always profile-aware.

All clawmes state lives under ``${HERMES_HOME}/clawmes/``. Never hardcode
``~/.hermes`` — Hermes' profile system rewrites ``HERMES_HOME`` per profile
and hardcoded paths break for users who run ``hermes --profile <name>``.

This module wraps ``hermes_constants.get_hermes_home()`` with our own
sub-tree builder. If ``hermes_constants`` is not importable (e.g. running
unit tests without Hermes installed), we fall back to the documented default
``~/.hermes`` so tests still pass.
"""

from __future__ import annotations

import os
from pathlib import Path


def _hermes_home() -> Path:
    """Return the active ``HERMES_HOME`` path.

    Defers to ``hermes_constants.get_hermes_home()`` when available so we
    pick up profile rewrites; falls back to ``$HERMES_HOME`` env, then
    ``~/.hermes``.
    """
    try:
        from hermes_constants import get_hermes_home  # type: ignore[import-not-found]

        return Path(get_hermes_home())
    except Exception:
        env_home = os.environ.get("HERMES_HOME")
        if env_home:
            return Path(env_home).expanduser()
        return Path.home() / ".hermes"


def hermes_home() -> Path:
    """Public accessor for the Hermes home directory."""
    return _hermes_home()


def clawmes_root() -> Path:
    """``${HERMES_HOME}/clawmes`` — the root of all clawmes state."""
    path = _hermes_home() / "clawmes"
    path.mkdir(parents=True, exist_ok=True)
    return path


def state_dir(*parts: str) -> Path:
    """Return (and create) a sub-directory under ``clawmes_root()``."""
    path = clawmes_root().joinpath(*parts)
    path.mkdir(parents=True, exist_ok=True)
    return path


def logs_dir() -> Path:
    return state_dir("logs")


def plans_dir() -> Path:
    return state_dir("plans")


def ledger_dir() -> Path:
    return state_dir("ledger")


def policy_dir() -> Path:
    return state_dir("policy")


def wallet_dir() -> Path:
    return state_dir("wallet")


def bridges_dir() -> Path:
    return state_dir("bridges")


def display_path(path: Path) -> str:
    """Pretty-print a path with ``HERMES_HOME`` collapsed to ``$HERMES_HOME``.

    Used in CLI output so different-profile users see the same generic
    path string.
    """
    home = _hermes_home()
    try:
        rel = path.relative_to(home)
        return f"$HERMES_HOME/{rel}"
    except ValueError:
        return str(path)
