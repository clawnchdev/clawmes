"""``bankr_automate`` — server-side automation rules via Bankr.

Five actions for managing Bankr-side automation rules (limit orders,
DCA, stop-loss, etc.). Bankr runs the keeper infrastructure; this
tool is the management API.

  * ``create``  — submit a new rule. Payload shape varies per rule
    type (DCA needs different fields than stop-loss, etc.); the LLM
    constructs the payload based on user intent.
  * ``list``    — list active rules.
  * ``pause``   — temporarily disable a rule.
  * ``resume``  — re-enable a paused rule.
  * ``delete``  — permanently delete a rule.
"""

from __future__ import annotations

from typing import Any

from clawmes.lib.logger import logger_for
from clawmes.lib.params import read_str
from clawmes.lib.tool_result import error_result, json_result
from clawmes.services.bankr_service import BankrError, get_bankr_service
from clawmes.tools.registry import register_with_ctx, write_tool

_log = logger_for("tools.bankr_automate")

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["create", "list", "pause", "resume", "delete"],
        },
        "rule_id": {
            "type": "string",
            "description": "Required for pause/resume/delete.",
        },
        "payload": {
            "type": "object",
            "description": "Rule definition (create). Shape varies by rule_type.",
        },
        "policyConfirmationNonce": {
            "type": "string",
            "description": "Set when retrying after POLICY HOLD.",
        },
    },
    "required": ["action"],
}


@write_tool(
    name="bankr_automate",
    toolset="clawmes-bankr",
    description=(
        "Bankr server-side automation rules — limit orders, DCA, stop-"
        "loss, etc. The clawmes plan_scheduler is the non-Bankr "
        "alternative for users who want local execution."
    ),
    schema=_SCHEMA,
    emoji="\U0001f916",
)
def bankr_automate(args: dict[str, Any], **kwargs: Any) -> str:
    action = read_str(args, "action", required=True)

    try:
        if action == "create":
            payload = args.get("payload")
            if not isinstance(payload, dict):
                return error_result(
                    "create requires a 'payload' dict",
                    code="param_error",
                )
            result = get_bankr_service().request("POST", "/v1/automate/create", body=payload)
        elif action == "list":
            result = get_bankr_service().request("GET", "/v1/automate/list")
        else:
            rule_id = read_str(args, "rule_id", required=True)
            result = get_bankr_service().request("POST", f"/v1/automate/{action}/{rule_id}")
    except BankrError as exc:
        return error_result(exc.message, code=exc.code)

    return json_result(
        {"action": action, "result": result},
        summary=f"bankr_automate {action}",
    )


def register(ctx) -> None:
    register_with_ctx(ctx, bankr_automate)
