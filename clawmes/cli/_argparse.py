"""argparse subparser wiring for ``hermes clawmes ...``.

Kept in its own module so individual handler files (``init.py``,
``doctor.py``, …) can import lazily without circular-import risk.
"""

from __future__ import annotations

import argparse
import sys

from clawmes.lib.logger import logger_for

_log = logger_for("cli.argparse")


def setup(parser: argparse.ArgumentParser) -> None:
    """Build the ``hermes clawmes`` subparser tree.

    Hermes calls this with the freshly-created ``clawmes`` subparser so
    we attach our own subparsers. ``set_defaults(func=...)`` gives the
    dispatcher in :func:`handle` a function reference.
    """
    subs = parser.add_subparsers(
        dest="clawmes_command",
        metavar="<subcommand>",
    )

    # init
    p_init = subs.add_parser("init", help="Interactive setup wizard")
    p_init.add_argument(
        "--reconfigure",
        action="store_true",
        help="Re-ask every question even if already configured",
    )
    p_init.add_argument(
        "--skip-wallet",
        action="store_true",
        help="Skip the wallet-mode step",
    )
    p_init.add_argument(
        "--check",
        action="store_true",
        help="Dry-run — report what would change without writing",
    )
    p_init.add_argument(
        "--non-interactive",
        action="store_true",
        help="Read all values from CLAWMES_INIT_* env vars; never prompt",
    )

    # doctor
    p_doctor = subs.add_parser("doctor", help="Diagnostics")
    p_doctor.add_argument(
        "--fix",
        action="store_true",
        help="Attempt automatic remediation of detected issues",
    )

    # version
    subs.add_parser("version", help="Show clawmes version")

    # status
    p_status = subs.add_parser("status", help="One-line health check")
    p_status.add_argument("--json", action="store_true", help="Emit machine-readable JSON")

    # wallet
    p_wallet = subs.add_parser("wallet", help="Wallet status / mode switch")
    p_wallet.add_argument("action", nargs="?", choices=["status", "switch", "balance"])
    p_wallet.add_argument("mode", nargs="?", choices=["walletconnect", "local", "bankr"])

    # plans
    p_plans = subs.add_parser("plans", help="Plan status / list / cancel")
    p_plans.add_argument(
        "action",
        nargs="?",
        choices=["list", "show", "cancel", "logs"],
        default="list",
    )
    p_plans.add_argument("plan_id", nargs="?")

    # policy
    p_policy = subs.add_parser("policy", help="Spending policies")
    p_policy.add_argument(
        "action",
        nargs="?",
        choices=["list", "set", "clear", "pause", "resume"],
        default="list",
    )
    p_policy.add_argument("rules", nargs="?", help="Natural-language policy rules (for set)")

    # persona
    p_persona = subs.add_parser("persona", help="Persona / SOUL.md management")
    p_persona.add_argument("action", choices=["show", "reinstall"])

    # skills
    p_skills = subs.add_parser("skills", help="Bundled skills management")
    p_skills.add_argument("action", choices=["list", "install", "show"])
    p_skills.add_argument("skill", nargs="*", help="Skill name(s) — omit for all")

    # logs
    p_logs = subs.add_parser("logs", help="Tail a bridge log")
    p_logs.add_argument("bridge", choices=["wc", "sa", "scheduler", "main"])
    p_logs.add_argument("-f", "--follow", action="store_true", help="Follow new lines")

    # update
    subs.add_parser("update", help="pip install -U clawmes + bridge refresh")

    # uninstall
    p_uninstall = subs.add_parser(
        "uninstall",
        help="Disable clawmes in plugins.enabled (state preserved)",
    )
    p_uninstall.add_argument(
        "--purge",
        action="store_true",
        help="Also nuke ${HERMES_HOME}/clawmes/ state and Node bridges",
    )


def handle(args: argparse.Namespace) -> int:
    """Dispatch to the appropriate handler module.

    Lazy imports keep the CLI fast — argparse parses args without
    importing every handler.
    """
    cmd = getattr(args, "clawmes_command", None)
    if cmd is None:
        print("Usage: hermes clawmes <subcommand>", file=sys.stderr)
        print("Run `hermes clawmes --help` for the list.", file=sys.stderr)
        return 2

    try:
        if cmd == "init":
            from clawmes.cli import init as init_mod

            return init_mod.run(args)
        if cmd == "doctor":
            from clawmes.cli import doctor as doctor_mod

            return doctor_mod.run(args)
        if cmd == "version":
            from clawmes.cli import version as version_mod

            return version_mod.run(args)
        if cmd == "status":
            from clawmes.cli import doctor as doctor_mod

            return doctor_mod.run_status(args)
        # Stubs for others
        print(f"`hermes clawmes {cmd}` is scaffolded but not yet implemented.")
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130
    except Exception as exc:  # noqa: BLE001 — top-level CLI catch
        _log.exception("hermes clawmes %s failed", cmd)
        print(f"Error: {exc}", file=sys.stderr)
        return 1
