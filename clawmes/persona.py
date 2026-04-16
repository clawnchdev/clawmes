"""Persona installation — idempotent SOUL.md copy.

Pattern matches openclawnch's ``ensureSoul()`` in ``bin/openclawnch.mjs``.
On every plugin start we make sure ``${HERMES_HOME}/SOUL.md`` exists. We
NEVER overwrite a file the user has touched — SOUL.md is sacred. The
bundled file is shipped at ``clawmes/data/SOUL.md`` and copied on first
run only.

A side-channel marker (``${HERMES_HOME}/.clawmes-soul-installed``)
records the version of clawmes that installed the SOUL.md so ``hermes
clawmes doctor`` can distinguish "user-edited from our bundled copy" vs
"user wrote their own from scratch" without re-reading file content.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from clawmes._version import __version__
from clawmes.lib.logger import logger_for
from clawmes.lib.paths import hermes_home

_log = logger_for("persona")

_BUNDLED_SOUL = Path(__file__).parent / "data" / "SOUL.md"
_INSTALL_MARKER_NAME = ".clawmes-soul-installed"


def soul_target() -> Path:
    """Path where SOUL.md should live in the user's Hermes home."""
    return hermes_home() / "SOUL.md"


def install_marker() -> Path:
    return hermes_home() / _INSTALL_MARKER_NAME


def ensure_soul_md() -> None:
    """Idempotent SOUL.md install.

    Skips if the target already exists. Always safe to call.
    """
    target = soul_target()
    if target.exists():
        _log.debug("SOUL.md already exists at %s, leaving it alone", target)
        return

    if not _BUNDLED_SOUL.exists():
        _log.warning("bundled SOUL.md missing at %s — skipping install", _BUNDLED_SOUL)
        return

    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(_BUNDLED_SOUL, target)
    install_marker().write_text(f"{__version__}\n", encoding="utf-8")
    _log.info("installed clawmes SOUL.md to %s", target)


def reinstall_soul_md(*, force: bool = False) -> bool:
    """Force-overwrite SOUL.md with the bundled copy.

    Surfaced via ``hermes clawmes persona reinstall`` — for users who
    customized their SOUL.md and want to reset to the clawmes default.
    Returns ``True`` if the file was overwritten.
    """
    target = soul_target()
    if target.exists() and not force:
        _log.warning("SOUL.md exists; pass force=True to overwrite")
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(_BUNDLED_SOUL, target)
    install_marker().write_text(f"{__version__}\n", encoding="utf-8")
    _log.info("reinstalled clawmes SOUL.md (force=%s)", force)
    return True
