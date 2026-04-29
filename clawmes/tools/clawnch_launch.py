"""``clawnch_launch`` — non-Bankr token launch via Clawnch launchpad.

The non-Bankr alternative for token launches. Deploys an ERC-20 +
creates a Uniswap V4 pool on Base directly, signed by the user's
wallet. No gas sponsorship (user pays); no custodial layer.

Four actions:

  * ``deploy``   — deploy ERC-20 with name/symbol/supply.
  * ``pair``     — create the Uniswap V4 pool for the deployed token.
  * ``seed_lp``  — seed initial liquidity (user provides paired-token
    amount).
  * ``info``     — read launch metadata.

The Clawnch launchpad contract on Base handles all three on-chain
steps. Tool builds calldata and routes through the wallet mode.
``CLAWNCH_LAUNCHPAD_ADDRESS`` env var configures the contract; default
points to the canonical deployment.
"""

from __future__ import annotations

import os
from typing import Any

from clawmes.lib.logger import logger_for
from clawmes.lib.params import read_str
from clawmes.lib.tool_result import error_result, json_result
from clawmes.services.wallet import get_wallet_state
from clawmes.tools.registry import register_with_ctx, write_tool

_log = logger_for("tools.clawnch_launch")

# Clawnch launchpad address must be supplied via env. The launchpad
# contract isn't yet published; until then this tool requires
# CLAWNCH_LAUNCHPAD_ADDRESS to be set explicitly. Fail-loud is better
# than dispatching to a non-existent contract.
_LAUNCH_GAS_DEFAULT = 1_500_000  # ERC-20 deploy + V4 pool init

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["deploy", "pair", "seed_lp", "info"],
        },
        "name": {"type": "string", "description": "Token name."},
        "symbol": {"type": "string", "description": "Token symbol."},
        "supply": {"type": "string", "description": "Initial supply (base units)."},
        "token": {
            "type": "string",
            "description": "Existing token (for pair / seed_lp / info).",
        },
        "paired_amount": {
            "type": "string",
            "description": "ETH amount to pair with for seed_lp.",
        },
        "calldata": {
            "type": "string",
            "description": "Override calldata (advanced).",
        },
        "policyConfirmationNonce": {
            "type": "string",
            "description": "Set when retrying after POLICY HOLD.",
        },
    },
    "required": ["action"],
}


@write_tool(
    name="clawnch_launch",
    toolset="clawmes-defi",
    description=(
        "Non-custodial token launch via Clawnch launchpad on Base. "
        "Deploys ERC-20, creates Uniswap V4 pool, seeds liquidity. "
        "User signs every step (no gas sponsorship; use bankr_launch "
        "for that)."
    ),
    schema=_SCHEMA,
    emoji="\U0001f31f",
)
def clawnch_launch(args: dict[str, Any], **kwargs: Any) -> str:
    state = get_wallet_state()
    if not state.connected or not state.address:
        return error_result(
            "No wallet connected. Run /connect first.",
            code="wallet_not_connected",
        )

    action = read_str(args, "action", required=True)
    if action == "info":
        return _handle_info(args)

    calldata = read_str(args, "calldata")
    if not calldata:
        return error_result(
            f"{action} requires explicit 'calldata' override at this "
            "milestone — Clawnch ABI is not yet wired. Build the "
            "calldata externally and pass through.",
            code="not_implemented",
        )

    return _send(state, calldata)


def _send(state, calldata: str) -> str:
    from clawmes.services.wallet import get_wallet_service

    launchpad = os.environ.get("CLAWNCH_LAUNCHPAD_ADDRESS")
    if not launchpad:
        return error_result(
            "CLAWNCH_LAUNCHPAD_ADDRESS is not set. The Clawnch "
            "launchpad address must be configured before this tool "
            "can submit transactions. See the project README for "
            "configuration.",
            code="not_configured",
        )
    svc = get_wallet_service()
    mode = svc.active_mode
    if mode is None:
        return error_result(
            "No active wallet mode; reconnect via /connect.",
            code="wallet_not_connected",
        )
    try:
        tx_hash = mode.send_transaction(
            to=launchpad,
            value=0,
            data=calldata,
            gas=_LAUNCH_GAS_DEFAULT,
            chain_id=8453,  # Base
        )
    except Exception as exc:  # noqa: BLE001
        return error_result(f"Launch failed: {exc}", code="send_failed")
    return json_result(
        {"tx_hash": tx_hash, "launchpad": launchpad},
        summary=f"Clawnch launch tx submitted: {tx_hash}",
    )


def _handle_info(args) -> str:
    return error_result(
        "Clawnch launch info requires the launchpad ABI which isn't "
        "wired yet. Use the block_explorer tool to inspect the token "
        "directly.",
        code="not_implemented",
    )


def register(ctx) -> None:
    register_with_ctx(ctx, clawnch_launch)
