"""Bridge installer.

On every plugin start we make sure the Node bridge sources are present
and ``npm ci``-installed under ``${HERMES_HOME}/clawmes/bridges/{wc,sa}``.

Strategy:

  1. Locate the bundled source under
     ``clawmes/bridges/sources/{wc,sa}/``.
  2. Hash ``package.json`` + ``package-lock.json``.
  3. Compare against ``${HERMES_HOME}/clawmes/bridges/{wc,sa}/.installed-hash``.
  4. If changed (or absent):
       a. Copy the bundled source into the install directory
       b. Run ``npm ci --omit=dev``
       c. Write the hash file
  5. Return absolute paths to ``dist/index.mjs`` for each bridge.

If Node is not installed, we log a clear warning and return None — the
plugin still loads, but tools that need bridges will fail with a
friendly error.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from clawmes.lib.logger import logger_for
from clawmes.lib.paths import bridges_dir

_log = logger_for("bridges.installer")


_SOURCES_ROOT = Path(__file__).parent / "sources"


@dataclass(frozen=True)
class BridgePaths:
    wc_entry: Path | None
    sa_entry: Path | None


def ensure_node_bridges(*, force: bool = False) -> BridgePaths:
    """Idempotent installer for both bridges. Safe to call on every boot."""
    node = shutil.which("node")
    if not node:
        _log.warning(
            "Node.js not found — install Node ≥ 20 for clawmes wallet bridges. "
            "Plugin will load but WC + SA tools will fail."
        )
        return BridgePaths(wc_entry=None, sa_entry=None)

    wc_entry = _ensure_one("wc", force=force)
    sa_entry = _ensure_one("sa", force=force)
    return BridgePaths(wc_entry=wc_entry, sa_entry=sa_entry)


def _ensure_one(name: str, *, force: bool) -> Path | None:
    src = _SOURCES_ROOT / name
    if not src.exists():
        _log.debug("bridge source for %s missing at %s — not yet bundled", name, src)
        return None

    target = bridges_dir() / name
    target.mkdir(parents=True, exist_ok=True)

    pj = src / "package.json"
    pl = src / "package-lock.json"
    if not pj.exists() or not pl.exists():
        _log.debug("bridge source for %s lacks package files yet — skipping install", name)
        return None

    expected_hash = _hash_files(pj, pl)
    marker = target / ".installed-hash"
    if not force and marker.exists() and marker.read_text(encoding="utf-8").strip() == expected_hash:
        _log.debug("bridge %s already installed (hash %s)", name, expected_hash[:8])
        return target / "dist" / "index.mjs"

    _log.info("installing bridge %s …", name)
    _copy_tree(src, target)
    try:
        subprocess.run(
            ["npm", "ci", "--omit=dev"],
            cwd=target,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        _log.error("npm ci failed for %s: %s", name, exc.stderr)
        return None

    marker.write_text(expected_hash + "\n", encoding="utf-8")
    return target / "dist" / "index.mjs"


def _hash_files(*paths: Path) -> str:
    h = hashlib.sha256()
    for p in paths:
        h.update(p.read_bytes())
    return h.hexdigest()


def _copy_tree(src: Path, dst: Path) -> None:
    """Mirror ``src`` into ``dst``, preserving file mtimes."""
    if dst.exists():
        # Don't blow away node_modules; just refresh source files.
        for child in src.iterdir():
            if child.name == "node_modules":
                continue
            target = dst / child.name
            if child.is_dir():
                if target.exists():
                    shutil.rmtree(target)
                shutil.copytree(child, target)
            else:
                shutil.copy2(child, target)
    else:
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns("node_modules"))
