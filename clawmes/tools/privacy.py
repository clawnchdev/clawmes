"""``privacy`` — privacy-preserving transfers via Lobster + alternatives.

Four actions:

  * ``transfer``  — privacy-preserving transfer through a pool.
  * ``deposit``   — alias for the deposit half of a transfer.
  * ``withdraw``  — withdraw from a pool to a destination.
  * ``info``      — read pool stats (current anonymity set, fees).

Most actions delegate to lobster_cash + privacy-pool services. This
tool unifies the interface so users don't have to know which backend
to call.
"""

from __future__ import annotations

import os
from typing import Any

from clawmes.lib.http import http_get
from clawmes.lib.logger import logger_for
from clawmes.lib.params import read_str
from clawmes.lib.tool_result import error_result, json_result
from clawmes.tools.registry import register_with_ctx, write_tool

_log = logger_for("tools.privacy")

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["transfer", "deposit", "withdraw", "info"],
        },
        "amount": {"type": "string"},
        "destination": {"type": "string"},
        "note": {"type": "string"},
        "pool": {"type": "string", "description": "Pool address."},
        "policyConfirmationNonce": {"type": "string"},
    },
    "required": ["action"],
}


@write_tool(
    name="privacy",
    toolset="clawmes-defi",
    description=(
        "Privacy-preserving operations via privacy-pool integrations. "
        "transfer / deposit / withdraw use lobster_cash under the hood; "
        "info reports anonymity-set size + pool stats. Privacy "
        "operations gate through policy evaluator like other writes."
    ),
    schema=_SCHEMA,
    emoji="\U0001f47b",
)
def privacy(args: dict[str, Any], **kwargs: Any) -> str:
    action = read_str(args, "action", required=True)

    if action == "info":
        return _handle_info(args)

    # transfer / deposit / withdraw all delegate to lobster_cash for
    # the actual execution. We re-route the args to that tool.
    from clawmes.tools.lobster_cash import lobster_cash

    if action == "transfer":
        # transfer = deposit + withdraw + sender-keep-note flow.
        # For now, surface as a deposit; the LLM coordinates withdraw
        # via a separate call once the receiver has the note.
        return error_result(
            "privacy transfer is multi-step (deposit + share note + "
            "receiver withdraws). Use action=deposit and share the "
            "returned note out-of-band.",
            code="not_implemented",
        )
    if action == "deposit":
        return lobster_cash({"action": "deposit", "amount": args.get("amount")}, **kwargs)
    return lobster_cash(
        {
            "action": "withdraw",
            "note": args.get("note"),
            "destination": args.get("destination"),
        },
        **kwargs,
    )


def _handle_info(args) -> str:
    api_key = os.environ.get("LOBSTER_API_KEY")
    if not api_key:
        return error_result(
            "LOBSTER_API_KEY required for pool info.",
            code="no_credentials",
        )
    pool = read_str(args, "pool")
    url = "https://api.lobster.cash/v1/pool"
    if pool:
        url = f"{url}/{pool}"
    try:
        result = http_get(
            url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=15.0,
        )
    except Exception as exc:  # noqa: BLE001
        return error_result(f"Pool info failed: {exc}", code="api_error")
    return json_result({"result": result}, summary="privacy pool info")


def register(ctx) -> None:
    register_with_ctx(ctx, privacy)
