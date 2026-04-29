"""``bankr_launch`` — deploy a token via Bankr's launchpad.

Bankr-only feature (no non-Bankr fallback): Bankr sponsors deploy
gas on Base + Solana for token launches via their launchpad. Three
actions:

  * ``deploy`` — deploy a new ERC-20 (Base) or SPL token (Solana).
    Returns the contract address + Bankr-side tracking ID.
  * ``pair``   — create the Uniswap V4 pool for the token.
  * ``info``   — read launch metadata + status.

Requires ``BANKR_API_KEY`` configured. The endpoint shapes follow
Bankr's API contract; see https://docs.bankr.bot.
"""

from __future__ import annotations

from typing import Any

from clawmes.lib.logger import logger_for
from clawmes.lib.params import read_str
from clawmes.lib.tool_result import error_result, json_result
from clawmes.services.bankr_service import BankrError, get_bankr_service
from clawmes.tools.registry import register_with_ctx, write_tool

_log = logger_for("tools.bankr_launch")

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["deploy", "pair", "info"],
        },
        "name": {"type": "string", "description": "Token name (deploy)."},
        "symbol": {"type": "string", "description": "Token symbol (deploy)."},
        "supply": {"type": "string", "description": "Initial supply (deploy)."},
        "chain": {
            "type": "string",
            "enum": ["base", "solana"],
            "description": "Default base.",
        },
        "token": {
            "type": "string",
            "description": "Existing token address (pair / info).",
        },
        "policyConfirmationNonce": {
            "type": "string",
            "description": "Set when retrying after POLICY HOLD.",
        },
    },
    "required": ["action"],
}


@write_tool(
    name="bankr_launch",
    toolset="clawmes-bankr",
    description=(
        "Deploy a token on Base or Solana via Bankr launchpad. Bankr "
        "sponsors the deploy gas. Three actions: deploy, pair (create "
        "Uniswap V4 pool), info."
    ),
    schema=_SCHEMA,
    emoji="\U0001f680",
)
def bankr_launch(args: dict[str, Any], **kwargs: Any) -> str:
    action = read_str(args, "action", required=True)
    chain = read_str(args, "chain") or "base"

    try:
        if action == "deploy":
            payload = {
                "name": read_str(args, "name", required=True),
                "symbol": read_str(args, "symbol", required=True),
                "supply": read_str(args, "supply", required=True),
                "chain": chain,
            }
            result = get_bankr_service().request("POST", "/v1/launch/deploy", body=payload)
        elif action == "pair":
            payload = {
                "token": read_str(args, "token", required=True),
                "chain": chain,
            }
            result = get_bankr_service().request("POST", "/v1/launch/pair", body=payload)
        else:
            token = read_str(args, "token", required=True)
            result = get_bankr_service().request(
                "GET", f"/v1/launch/info?token={token}&chain={chain}"
            )
    except BankrError as exc:
        return error_result(exc.message, code=exc.code)

    return json_result(
        {"action": action, "chain": chain, "result": result},
        summary=f"bankr_launch {action}: {result}",
    )


def register(ctx) -> None:
    register_with_ctx(ctx, bankr_launch)
