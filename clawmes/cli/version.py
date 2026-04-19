"""``hermes clawmes version`` — emit the package version + build info."""

from __future__ import annotations

import argparse
import sys

from clawmes._version import __version__


def run(args: argparse.Namespace) -> int:
    print(f"clawmes {__version__}")
    print(f"python   {sys.version.split()[0]}")
    try:
        import hermes_agent  # type: ignore[import-not-found]

        hermes_ver = getattr(hermes_agent, "__version__", "(unknown)")
    except ImportError:
        hermes_ver = "(not installed)"
    print(f"hermes   {hermes_ver}")
    return 0
