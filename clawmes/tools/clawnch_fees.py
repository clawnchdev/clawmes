"""``clawnch_fees`` — claim Uniswap V4 LP fees from Clawnch launches.

Three actions:

  * ``claim``    — call the launchpad's claim(token) to collect
    accumulated fees.
  * ``summary``  — read pending unclaimed fees per launched token.
  * ``history``  — read past fee claims (via block_explorer logs).

Like ``clawnch_launch``, these need the launchpad ABI which isn't
fully wired. Calldata-override path is exposed for advanced use.
"""

from __future__ import annotations

import os
from typing import Any

from clawmes.lib.logger import logger_for
from clawmes.lib.params import read_str
from clawmes.lib.tool_result import error_result, json_result
from clawmes.services.wallet import get_wallet_state
from clawmes.tools.registry import register_with_ctx, write_tool

_log = logger_for("tools.clawnch_fees")

_DEFAULT_LAUNCHPAD = "0x" + "C1" * 20  # placeholder
_FEE_CLAIM_GAS_DEFAULT = 250_000

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["claim", "summary", "history"],
        },
        "token": {
            "type": "string",
            "description": "Launched token address.",
        },
        "calldata": {
            "type": "string",
            "description": "Override calldata for claim.",
        },
        "policyConfirmationNonce": {
            "type": "string",
            "description": "Set when retrying after POLICY HOLD.",
        },
    },
    "required": ["action"],
}


@write_tool(
    name="clawnch_fees",
    toolset="clawmes-defi",
    description=(
        "Claim Uniswap V4 LP fees from Clawnch launches. claim "
        "collects pending fees; summary shows unclaimed amounts; "
        "history reads past claims via block_explorer."
    ),
    schema=_SCHEMA,
    emoji="\U0001f4b0",
)
def clawnch_fees(args: dict[str, Any], **kwargs: Any) -> str:
    state = get_wallet_state()
    if not state.connected or not state.address:
        return error_result(
            "No wallet connected. Run /connect first.",
            code="wallet_not_connected",
        )

    action = read_str(args, "action", required=True)

    if action in ("summary", "history"):
        return error_result(
            f"clawnch_fees {action} requires the launchpad ABI and "
            "an indexer that aren't wired yet. Use block_explorer to "
            "inspect the launchpad contract directly.",
            code="not_implemented",
        )

    calldata = read_str(args, "calldata")
    if not calldata:
        return error_result(
            "claim requires an explicit 'calldata' override — Clawnch ABI not wired yet.",
            code="not_implemented",
        )

    from clawmes.services.wallet import get_wallet_service

    launchpad = os.environ.get("CLAWNCH_LAUNCHPAD_ADDRESS") or _DEFAULT_LAUNCHPAD
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
            gas=_FEE_CLAIM_GAS_DEFAULT,
            chain_id=8453,
        )
    except Exception as exc:  # noqa: BLE001
        return error_result(f"Fee claim failed: {exc}", code="send_failed")
    return json_result(
        {"tx_hash": tx_hash},
        summary=f"Clawnch fee claim submitted: {tx_hash}",
    )


def register(ctx) -> None:
    register_with_ctx(ctx, clawnch_fees)
