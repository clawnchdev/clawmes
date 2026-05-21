"""``clawnch_launch`` — deploy a token via the Clawnch launchpad.

LLM-callable surface for the Clawnch deploy flow. Wraps
:class:`clawmes.services.clawnch.ClawnchService` which talks to the
launchpad HTTP API. Two actions:

  * ``deploy`` — submit a deploy. Service handles the captcha
    challenge (sign message + read storage slot + compute keccak
    proof), then posts the solution. Clawnch's deployer wallet pays
    gas + submits the underlying Clanker tx server-side. Optional
    ``bypass_tx_hash`` skips the 24h cooldown by paying ETH to the
    bypass recipient (see ``CLAWNCH_BYPASS_RECIPIENT``).
  * ``info`` — read launch metadata for an existing token.

What used to be ``pair`` and ``seed_lp`` collapsed into ``deploy``:
Clanker handles ERC-20 deploy + Uniswap V4 pool + initial liquidity
seeding atomically in one call, so the multi-step ``pair`` /
``seed_lp`` actions don't apply against the live launchpad. They're
left out rather than stubbed.

Requires ``CLAWNCH_API_KEY``. Register an agent + obtain the key via
``/register_agent`` or directly against ``POST /api/agents/register``
+ ``POST /api/agents/verify`` on clawn.ch.
"""

from __future__ import annotations

from typing import Any

from clawmes.lib.logger import logger_for
from clawmes.lib.params import read_str
from clawmes.lib.tool_result import error_result, json_result
from clawmes.tools.registry import register_with_ctx, write_tool

_log = logger_for("tools.clawnch_launch")

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["deploy", "info"],
        },
        "name": {"type": "string", "description": "Token name (deploy)."},
        "symbol": {"type": "string", "description": "Token symbol (deploy)."},
        "description": {
            "type": "string",
            "description": "One-line description of the token (deploy).",
        },
        "image": {
            "type": "string",
            "description": "Image URL for token metadata (deploy, optional).",
        },
        "bypass_tx_hash": {
            "type": "string",
            "description": (
                "Tx hash of >= 0.001 ETH paid to the Clawnch bypass "
                "recipient. Skips the 24h deploy cooldown (deploy, "
                "optional)."
            ),
        },
        "token": {
            "type": "string",
            "description": "Token address (info).",
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
        "Deploy a token on Base via the Clawnch launchpad. Clawnch "
        "handles the deploy + initial liquidity atomically via the "
        "Clanker SDK; the user's wallet signs a captcha challenge to "
        "prove identity. Requires CLAWNCH_API_KEY (register an agent "
        "with /register_agent)."
    ),
    schema=_SCHEMA,
    emoji="\U0001f31f",
)
def clawnch_launch(args: dict[str, Any], **kwargs: Any) -> str:
    action = read_str(args, "action", required=True)

    if action == "info":
        return _handle_info(args)
    return _handle_deploy(args)


def _handle_deploy(args: dict[str, Any]) -> str:
    from clawmes.services.clawnch import ClawnchError, get_clawnch_service

    name = read_str(args, "name")
    symbol = read_str(args, "symbol")
    if not name or not symbol:
        return error_result(
            "deploy requires both 'name' and 'symbol'.",
            code="param_error",
        )

    token_params: dict[str, Any] = {
        "name": name,
        "symbol": symbol,
    }
    if description := read_str(args, "description"):
        token_params["description"] = description
    if image := read_str(args, "image"):
        token_params["image"] = image

    bypass = read_str(args, "bypass_tx_hash") or None

    try:
        result = get_clawnch_service().deploy(
            token_params=token_params,
            bypass_tx_hash=bypass,
        )
    except ClawnchError as exc:
        return error_result(exc.message, code=exc.code)
    except Exception as exc:  # noqa: BLE001
        return error_result(f"Launch failed: {exc}", code="api_error")

    tx_hash = result.get("txHash") or result.get("tx_hash")
    token_address = result.get("tokenAddress") or result.get("token_address")
    summary_parts = [f"Launched {symbol} via Clawnch."]
    if token_address:
        summary_parts.append(f"Token: {token_address}")
    if tx_hash:
        summary_parts.append(f"Tx: {tx_hash}")
    return json_result(result, summary=" ".join(summary_parts))


def _handle_info(args: dict[str, Any]) -> str:
    from clawmes.services.clawnch import ClawnchError, get_clawnch_service

    token = read_str(args, "token")
    if not token:
        return error_result(
            "info requires 'token' (the launched token's address).",
            code="param_error",
        )
    try:
        data = get_clawnch_service().get_launch(token)
    except ClawnchError as exc:
        return error_result(exc.message, code=exc.code)
    except Exception as exc:  # noqa: BLE001
        return error_result(f"Info read failed: {exc}", code="api_error")
    return json_result(data, summary=f"Launch info for {token}")


def register(ctx) -> None:
    register_with_ctx(ctx, clawnch_launch)
