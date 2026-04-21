"""``transfer`` — send ETH or ERC-20 tokens to a recipient.

First concrete tool in the catalog — primarily exists at this milestone to
exercise the ``@write_tool`` gating decorator and the
``register_with_ctx`` wiring. The actual on-chain dispatch lands once the
wallet bridge and ENS / decimals services are in place.
"""

from __future__ import annotations

from typing import Any

from clawmes.lib.params import read_str
from clawmes.lib.tool_result import error_result
from clawmes.services.wallet import get_wallet_state
from clawmes.tools.registry import register_with_ctx, write_tool

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["send", "estimate"],
            "description": "send executes; estimate returns gas + route only",
        },
        "to": {
            "type": "string",
            "description": "Recipient address or ENS name",
        },
        "amount": {
            "type": "string",
            "description": "Human-readable amount (e.g. '0.5')",
        },
        "token": {
            "type": "string",
            "description": "ERC-20 contract address; omit for ETH/native",
        },
        "policyConfirmationNonce": {
            "type": "string",
            "description": "Set when retrying after a POLICY HOLD response",
        },
    },
    "required": ["action", "to", "amount"],
}


@write_tool(
    name="transfer",
    toolset="clawmes-wallet",
    description=(
        "Send ETH or ERC-20 tokens to a recipient. Supports ENS names. "
        "Returns gas estimate and route when action='estimate'; submits "
        "the transaction when action='send'. Requires a connected wallet "
        "(/connect, /connect_bankr, or /connect_local)."
    ),
    schema=_SCHEMA,
    emoji="💸",
)
def transfer(args: dict[str, Any], **kwargs: Any) -> str:
    state = get_wallet_state()
    if not state.connected:
        return error_result(
            "No wallet connected. Run /connect (WalletConnect), "
            "/connect_bankr (custodial), or /connect_local (local key) "
            "first.",
            code="wallet_not_connected",
        )

    action = read_str(args, "action", required=True)
    if action == "send":
        return _handle_send(args, state)
    if action == "estimate":
        return _handle_estimate(args, state)
    return error_result(f"Unknown action: {action!r}", code="invalid_action")


def _handle_send(args: dict[str, Any], state: Any) -> str:
    return error_result(
        "transfer.send is not implemented in this milestone. "
        "Wallet bridge and gas/decimals services are forthcoming.",
        code="not_implemented",
    )


def _handle_estimate(args: dict[str, Any], state: Any) -> str:
    return error_result(
        "transfer.estimate is not implemented in this milestone. "
        "Wallet bridge and gas/decimals services are forthcoming.",
        code="not_implemented",
    )


def register(ctx) -> None:
    """Wire ``transfer`` into Hermes."""
    register_with_ctx(ctx, transfer)
