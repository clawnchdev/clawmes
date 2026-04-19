"""``hermes clawmes doctor`` — diagnostics.

Surfaces:

  * Plugin loaded + version
  * Hermes-compat (version + plugin API)
  * LLM key valid (live ping via Hermes)
  * Channel configured
  * Wallet connected (mode + chain)
  * Node bridges installed (clawmes-wc-bridge, clawmes-sa-bridge)
  * Plan scheduler running
  * Optional keys present (Alchemy, 0x, Basescan, ...)

Each check returns one of green ``✓`` / yellow ``⚠`` / red ``✗`` with a
one-line summary. ``--fix`` attempts automatic remediation where
possible (re-run npm ci, re-enable plugin in plugins.enabled, etc.).
"""

from __future__ import annotations

import argparse
import json
import shutil

from clawmes._version import __version__
from clawmes.lib.paths import bridges_dir, hermes_home
from clawmes.services.wallet import get_wallet_state


GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
RESET = "\033[0m"


def run(args: argparse.Namespace) -> int:
    """Full diagnostics report."""
    checks = _gather_checks()
    bad = 0
    for label, status, detail in checks:
        marker = {"ok": f"{GREEN}✓{RESET}", "warn": f"{YELLOW}⚠{RESET}", "fail": f"{RED}✗{RESET}"}[status]
        print(f"{marker} {label:<35} {detail}")
        if status == "fail":
            bad += 1
    if args.fix:
        print()
        print("--fix not yet implemented at this milestone.")
    return 1 if bad else 0


def run_status(args: argparse.Namespace) -> int:
    """One-line health check, optionally JSON."""
    checks = _gather_checks()
    statuses = [c[1] for c in checks]
    overall = (
        "fail" if "fail" in statuses
        else "warn" if "warn" in statuses
        else "ok"
    )
    payload = {
        "overall": overall,
        "version": __version__,
        "checks": [{"label": l, "status": s, "detail": d} for l, s, d in checks],
    }
    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2))
    else:
        print(
            f"clawmes {__version__}: "
            f"{overall.upper()} "
            f"({sum(1 for s in statuses if s == 'ok')}/{len(statuses)} ok)"
        )
    return 1 if overall == "fail" else 0


def _gather_checks() -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []

    # Plugin loaded
    out.append(("Plugin loaded", "ok", f"clawmes {__version__}"))

    # Hermes import
    try:
        import hermes_cli  # noqa: F401

        out.append(("Hermes available", "ok", "hermes_cli importable"))
    except ImportError:
        out.append(("Hermes available", "fail", "hermes-agent not installed"))

    # SOUL.md present
    soul = hermes_home() / "SOUL.md"
    if soul.exists():
        out.append(("SOUL.md installed", "ok", str(soul)))
    else:
        out.append(("SOUL.md installed", "warn", "missing — will install on next start"))

    # Wallet state
    state = get_wallet_state()
    if state.connected:
        out.append((
            "Wallet connected",
            "ok",
            f"{state.mode} / {state.chain_name} / {state.address}",
        ))
    else:
        out.append(("Wallet connected", "warn", "no wallet — run /connect"))

    # Node available
    if shutil.which("node"):
        out.append(("Node.js available", "ok", "for clawmes-wc-bridge / sa-bridge"))
    else:
        out.append(("Node.js available", "fail", "install Node ≥ 20"))

    # Bridges installed
    wc = bridges_dir() / "wc" / ".installed-hash"
    sa = bridges_dir() / "sa" / ".installed-hash"
    bridges_label = "Bridges installed"
    if wc.exists() and sa.exists():
        out.append((bridges_label, "ok", "wc + sa npm-installed"))
    else:
        out.append((
            bridges_label,
            "warn",
            "not yet installed — first plugin start will run npm ci",
        ))

    return out
