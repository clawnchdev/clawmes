"""Hermes plugin entry shim (repo-root).

Hermes' plugin discovery has two paths with different requirements:

  * ``hermes plugins install <git>`` clones the repo to
    ``~/.hermes/plugins/<name>/``. The CLI's ``plugins list`` command
    looks for ``<plugin-dir>/plugin.yaml`` at THAT level (no recursion).
    The actual loader (``PluginManager.discover_and_load``) requires
    both ``plugin.yaml`` AND ``__init__.py`` at the plugin dir to load
    the plugin's ``register(ctx)``.

  * ``pip install <pkg>`` registers via setuptools entry points.
    Discovers via ``importlib.metadata.entry_points()``; the actual
    Python package is at ``./clawmes/``.

This shim satisfies the first path. It loads the inner ``clawmes/``
package via :func:`importlib.util.spec_from_file_location` and
re-exports ``register`` + ``__version__``.

The shim is NOT part of the ``clawmes`` Python package: setuptools'
``find_packages`` scans subdirectories of the repo root for packages,
so the inner ``clawmes/`` directory is what ships in the wheel.

Pytest interaction: removing the ``tests/__init__.py`` files at the
repo level was necessary so pytest doesn't try to interpret tests as
``clawmes.tests.*`` (which would happen if both ``./__init__.py`` and
``tests/__init__.py`` existed). Without ``tests/__init__.py`` files,
pytest uses sys.path-based test discovery and tests import the
``clawmes`` package via the inner directory.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_inner_dir = Path(__file__).resolve().parent / "clawmes"
_inner_init = _inner_dir / "__init__.py"

# Load the inner package by file path. Register it under the canonical
# ``clawmes`` name BEFORE executing the inner __init__.py so its
# absolute imports (``from clawmes.X import Y``) resolve back to itself.
_spec = importlib.util.spec_from_file_location(
    "clawmes",
    _inner_init,
    submodule_search_locations=[str(_inner_dir)],
)
if _spec is None or _spec.loader is None:  # pragma: no cover — defensive
    raise ImportError(f"Cannot load clawmes from {_inner_init}")

_module = importlib.util.module_from_spec(_spec)
sys.modules["clawmes"] = _module
_spec.loader.exec_module(_module)

register = _module.register
__version__ = _module.__version__

__all__ = ["register", "__version__"]
