"""``clawnch_fees`` — read LP fee accrual for Clawnch launches.

Reads-side companion to ``clawnch_launch``. Two actions:

  * ``my_launches`` — list the authenticated agent's launches with
    aggregate fee accrual. Uses Clawnch's ``/api/agents/me`` endpoint.
  * ``launch_info`` — per-token launch detail (price, volume, fees so
    far). Uses Clawnch's ``/api/launches?address=…`` endpoint.

Claim-side ops aren't implemented here today: Clanker pays creator
rewards via its own LP-fee accumulator (FeeLocker), not via a
launchpad-controlled "claim()" function. Users claim through their
Clanker dashboard or directly against the FeeLocker contract. Once
v2 ships (the ClawnchFactory fork that drops Clanker), we'll revisit
adding a launchpad-orchestrated claim action.

Requires ``CLAWNCH_API_KEY`` for ``my_launches``; ``launch_info`` is
public and works without a key.
"""

from __future__ import annotations

from typing import Any

from clawmes.lib.logger import logger_for
from clawmes.lib.params import read_str
from clawmes.lib.tool_result import error_result, json_result
from clawmes.tools.registry import register_with_ctx, write_tool

_log = logger_for("tools.clawnch_fees")

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["my_launches", "launch_info"],
        },
        "token": {
            "type": "string",
            "description": "Token address (for launch_info).",
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
        "Read LP-fee accrual + launch metadata for tokens deployed via "
        "the Clawnch launchpad. my_launches lists the active agent's "
        "launches; launch_info reads detail for a single token."
    ),
    schema=_SCHEMA,
    emoji="\U0001f4b0",
)
def clawnch_fees(args: dict[str, Any], **kwargs: Any) -> str:
    from clawmes.services.clawnch import ClawnchError, get_clawnch_service

    action = read_str(args, "action", required=True)
    svc = get_clawnch_service()

    try:
        if action == "my_launches":
            data = svc.get_my_launches()
            return json_result(data, summary=_format_my_launches(data))
        # action == "launch_info"
        token = read_str(args, "token")
        if not token:
            return error_result(
                "launch_info requires 'token' (address).",
                code="param_error",
            )
        data = svc.get_launch(token)
        return json_result(data, summary=f"Launch detail for {token}")
    except ClawnchError as exc:
        return error_result(exc.message, code=exc.code)
    except Exception as exc:  # noqa: BLE001
        return error_result(f"Read failed: {exc}", code="api_error")


def _format_my_launches(data: dict[str, Any]) -> str:
    launches = data.get("launches") or data.get("tokens") or []
    if not isinstance(launches, list):
        return "My launches retrieved."
    count = len(launches)
    if count == 0:
        return "No launches yet for this agent."
    return f"{count} launch(es) recorded for this agent."


def register(ctx) -> None:
    register_with_ctx(ctx, clawnch_fees)
