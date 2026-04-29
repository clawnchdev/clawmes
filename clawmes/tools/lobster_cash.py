"""``lobster_cash`` — Lobster privacy pool deposit / withdraw.

Three actions:

  * ``deposit``   — deposit assets into the pool. Returns a note hash
    used to reclaim later.
  * ``withdraw``  — withdraw to a specified address using the note +
    proof.
  * ``proof``     — generate a proof for a specific note.

Privacy operations require careful handling — both ``deposit`` and
``withdraw`` are write actions that gate through the policy
evaluator. ``LOBSTER_API_KEY`` required.
"""

from __future__ import annotations

import os
from typing import Any

from clawmes.lib.http import http_post
from clawmes.lib.logger import logger_for
from clawmes.lib.params import read_str
from clawmes.lib.tool_result import error_result, json_result
from clawmes.tools.registry import register_with_ctx, write_tool

_log = logger_for("tools.lobster_cash")

_LOBSTER_BASE = "https://api.lobster.cash"

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["deposit", "withdraw", "proof"]},
        "amount": {"type": "string"},
        "note": {"type": "string", "description": "Note hash for withdraw / proof."},
        "destination": {
            "type": "string",
            "description": "Withdraw destination address.",
        },
        "policyConfirmationNonce": {
            "type": "string",
            "description": "Set when retrying after POLICY HOLD.",
        },
    },
    "required": ["action"],
}


@write_tool(
    name="lobster_cash",
    toolset="clawmes-defi",
    description=(
        "Privacy pool deposit / withdraw via Lobster. deposit pools "
        "assets and returns a note; withdraw redeems with a proof; "
        "proof generates the redemption proof."
    ),
    schema=_SCHEMA,
    emoji="\U0001f99e",
)
def lobster_cash(args: dict[str, Any], **kwargs: Any) -> str:
    api_key = os.environ.get("LOBSTER_API_KEY")
    if not api_key:
        return error_result(
            "LOBSTER_API_KEY required. Get one at https://lobster.cash",
            code="no_credentials",
        )
    headers = {"Authorization": f"Bearer {api_key}"}
    action = read_str(args, "action", required=True)

    try:
        if action == "deposit":
            payload = {"amount": read_str(args, "amount", required=True)}
            result = http_post(
                f"{_LOBSTER_BASE}/v1/deposit",
                json=payload,
                headers=headers,
                timeout=30.0,
            )
        elif action == "withdraw":
            payload = {
                "note": read_str(args, "note", required=True),
                "destination": read_str(args, "destination", required=True),
            }
            result = http_post(
                f"{_LOBSTER_BASE}/v1/withdraw",
                json=payload,
                headers=headers,
                timeout=30.0,
            )
        else:
            payload = {"note": read_str(args, "note", required=True)}
            result = http_post(
                f"{_LOBSTER_BASE}/v1/proof",
                json=payload,
                headers=headers,
                timeout=30.0,
            )
    except Exception as exc:  # noqa: BLE001
        return error_result(f"Lobster request failed: {exc}", code="api_error")
    return json_result({"action": action, "result": result}, summary=f"lobster {action}")


def register(ctx) -> None:
    register_with_ctx(ctx, lobster_cash)
