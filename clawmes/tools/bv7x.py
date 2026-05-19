"""``bv7x`` — agent / commerce / A2A surface for the BV-7X oracle.

BV-7X is a clawnch-ecosystem project (``$BV7X`` was launched on the
Clawnch launchpad). This tool covers the agent-level read surface:

  * ``regime``           — current BTC market regime classification.
  * ``identity``         — BV-7X's ERC-8004 agent identity.
  * ``reputation``       — BV-7X's on-chain reputation score.
  * ``discover``         — A2A skill card.
  * ``a2a_task``         — fetch an A2A task status by task id.
  * ``commerce``         — list BV-7X's commerce offerings (x402).
  * ``copy_trade_status``— public copy-trade service status.

Signal / prediction / on-chain attestation endpoints live in the
companion :mod:`bv7x_oracle` tool. Raw BTC market data
(price / fear-greed / ETF flows) lives in :mod:`bv7x_market`.

Read-only by design (``@read_tool``). No on-chain side effects.
"""

from __future__ import annotations

from typing import Any

from clawmes.lib.params import ParamError, read_enum, read_str
from clawmes.lib.tool_result import error_result, json_result
from clawmes.services.bv7x import BV7XError, get_bv7x_service
from clawmes.tools.registry import read_tool, register_with_ctx

_VALID_ACTIONS = [
    "regime",
    "identity",
    "reputation",
    "discover",
    "a2a_task",
    "commerce",
    "copy_trade_status",
]


_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": _VALID_ACTIONS,
            "description": (
                "regime = current BTC regime; identity = ERC-8004 agent "
                "identity; reputation = on-chain reputation score; "
                "discover = A2A skill card; a2a_task = fetch task status "
                "(requires task_id); commerce = list commerce offerings; "
                "copy_trade_status = public copy-trade status."
            ),
        },
        "task_id": {
            "type": "string",
            "description": "A2A task id (required for action=a2a_task).",
        },
    },
    "required": ["action"],
}


@read_tool(
    name="bv7x",
    toolset="clawmes-intelligence",
    description=(
        "BV-7X agent/A2A/commerce surface. regime, identity, reputation, "
        "discover, a2a_task, commerce, copy_trade_status. Signal + "
        "prediction endpoints live in bv7x_oracle; raw market data in "
        "bv7x_market."
    ),
    schema=_SCHEMA,
    emoji="\U0001f7ea",  # 🟪
)
def bv7x(args: dict[str, Any], **kwargs: Any) -> str:
    try:
        action = read_enum(args, "action", _VALID_ACTIONS, required=True)
    except ParamError as exc:
        return error_result(str(exc), code="param_error")

    svc = get_bv7x_service()
    try:
        if action == "regime":
            data = svc.get_regime()
            return json_result(data, summary=_format_regime(data))
        if action == "identity":
            data = svc.get_agent_identity()
            return json_result(data, summary=_format_identity(data))
        if action == "reputation":
            data = svc.get_agent_reputation()
            return json_result(data, summary=_format_reputation(data))
        if action == "discover":
            data = svc.discover_a2a()
            return json_result(data, summary=_format_discover(data))
        if action == "a2a_task":
            try:
                task_id = read_str(args, "task_id", required=True)
            except ParamError as exc:
                return error_result(str(exc), code="param_error")
            assert task_id is not None
            data = svc.get_a2a_task(task_id)
            status = data.get("status") or data.get("state") or "unknown"
            return json_result(data, summary=f"task {task_id}: {status}")
        if action == "commerce":
            data = svc.get_commerce_offerings()
            offerings = data.get("offerings") or data.get("items") or []
            return json_result(
                data,
                summary=f"BV-7X commerce: {len(offerings)} offering(s)",
            )
        # action == "copy_trade_status" — the only remaining valid action.
        data = svc.get_copy_trade_status()
        status = data.get("status") or data.get("state") or "?"
        return json_result(data, summary=f"copy-trade: {status}")
    except BV7XError as exc:
        return error_result(exc.message, code=exc.code)


def _format_regime(data: dict[str, Any]) -> str:
    regime = data.get("regime") or data.get("classification") or "?"
    risk = data.get("risk_level") or data.get("risk") or ""
    return f"BV-7X regime: {regime}" + (f" (risk={risk})" if risk else "")


def _format_identity(data: dict[str, Any]) -> str:
    agent_id = data.get("agent_id") or data.get("id") or "?"
    reputation = data.get("reputation") or data.get("score")
    parts = [f"BV-7X agent #{agent_id}"]
    if reputation is not None:
        parts.append(f"reputation={reputation}")
    return " · ".join(parts)


def _format_reputation(data: dict[str, Any]) -> str:
    score = data.get("score") or data.get("reputation") or "?"
    return f"BV-7X reputation: {score}"


def _format_discover(data: dict[str, Any]) -> str:
    skills = data.get("skills") or []
    version = data.get("version") or data.get("a2a_version") or "?"
    return f"BV-7X A2A v{version} · {len(skills)} skill(s)"


def register(ctx) -> None:
    register_with_ctx(ctx, bv7x)
