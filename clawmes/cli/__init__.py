"""``hermes clawmes <subcommand>`` argparse tree.

Hermes plugins extend the top-level ``hermes`` argparse tree via
``ctx.register_cli_command(name, help, setup_fn, handler_fn)``. We
register a single ``clawmes`` subcommand that fans out to the per-action
modules in this package.

User runs:

.. code-block:: bash

    hermes clawmes init
    hermes clawmes doctor
    hermes clawmes wallet
    hermes clawmes plans
    hermes clawmes policy
    hermes clawmes persona reinstall
    hermes clawmes skills install
    hermes clawmes update
    hermes clawmes version
    hermes clawmes status
    hermes clawmes logs <bridge>
    hermes clawmes uninstall
"""

from __future__ import annotations

from clawmes.cli import _argparse

__all__ = ["register_all"]


def register_all(ctx) -> None:
    """Register the ``clawmes`` subcommand with Hermes' argparse tree."""
    ctx.register_cli_command(
        name="clawmes",
        help="Manage clawmes — setup, wallet, plans, policy, diagnostics",
        setup_fn=_argparse.setup,
        handler_fn=_argparse.handle,
        description=(
            "Clawmes plugin management. Run `hermes clawmes init` to "
            "configure, `hermes clawmes doctor` for diagnostics, or any "
            "of the per-area subcommands for finer-grained control."
        ),
    )
