"""``hermes clawmes init`` — interactive setup wizard.

5-step flow mirroring openclawnch's ``init.ts``:

  1. LLM provider — reuse Hermes' configured one or switch
  2. Channel — reuse Hermes' configured one or switch
  3. Wallet mode — walletconnect | local | bankr | skip
  4. Spending policies — accept default set or customize
  5. Optional API keys — Alchemy, 0x, Basescan, Herd, etc.

After the wizard, we:
  * write any new keys to ``~/.hermes/.env``
  * write any new clawmes config to ``~/.hermes/config.yaml``
  * enable the plugin in ``plugins.enabled`` if not already
  * call ``persona.ensure_soul_md()``
  * call ``services.bridges.installer.ensure_node_bridges()``
  * register the plan-tick cron job
"""

from __future__ import annotations

import argparse


_BANNER = """
╔══════════════════════════════════════╗
║          Welcome to clawmes          ║
╚══════════════════════════════════════╝

Hermes Agent for crypto. Five-step setup follows.
""".strip()


def run(args: argparse.Namespace) -> int:
    """Entry point for ``hermes clawmes init``.

    Stubbed at this milestone — the real interactive flow lands once the
    wallet bridges, policy parser, and key-validation HTTP calls are
    wired. For now we surface a placeholder message that explains the
    intended UX.
    """
    print(_BANNER)
    print()
    print("Setup wizard not yet implemented at this milestone.")
    print()
    print("Planned flow:")
    print("  [1/5] LLM provider     — reuse / switch")
    print("  [2/5] Channel          — reuse / switch")
    print("  [3/5] Wallet mode      — walletconnect | local | bankr")
    print("  [4/5] Spending policies — default or custom")
    print("  [5/5] Optional API keys — Alchemy, 0x, Basescan, ...")
    print()
    if args.check:
        print("(--check dry-run; no changes would be made)")
    if args.non_interactive:
        print("(--non-interactive; would read CLAWMES_INIT_* env vars)")
    print()
    print(
        "For now: enable manually via `hermes plugins enable clawmes` "
        "and edit ~/.hermes/.env directly. See README for required keys."
    )
    return 0
