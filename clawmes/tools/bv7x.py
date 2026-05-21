"""``bv7x`` — read BV-7X public-API data.

Three read-only actions wrapping :class:`BV7XService`:

  * ``regime``   — BTC market regime (CRISIS / BEAR / NEUTRAL / BULL /
    EUPHORIA) + thresholds.
  * ``identity`` — BV-7X's ERC-8004 agent identity + on-chain reputation.
  * ``discover`` — A2A discovery card (skill list, version, capabilities).

Token-gated endpoints (``/oracle``, ``/copy-trade/*``) are NOT exposed
here. Users who hold ``$BV7X`` and want those should hit the API
directly with their own credentials. We don't make clawmes features
depend on third-party token holdings.

Read-only by design — ``@read_tool`` skips the policy gate. This tool
fetches public data and returns it; no on-chain side effects.
"""

from __future__ import annotations

from typing import Any

from clawmes.lib.params import ParamError, read_enum
from clawmes.lib.tool_result import error_result, json_result
from clawmes.services.bv7x import BV7XError, get_bv7x_service
from clawmes.tools.registry import read_tool, register_with_ctx

_VALID_ACTIONS = ["regime", "identity", "discover"]


_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": _VALID_ACTIONS,
            "description": (
                "regime = current BTC regime classification; "
                "identity = BV-7X's on-chain agent identity; "
                "discover = BV-7X's A2A skill card."
            ),
        },
    },
    "required": ["action"],
}


@read_tool(
    name="bv7x",
    toolset="clawmes-intelligence",
    description=(
        "Read BV-7X autonomous BTC oracle data via their public REST API. "
        "regime = market regime classification (CRISIS/BEAR/NEUTRAL/BULL/"
        "EUPHORIA), identity = ERC-8004 agent identity + reputation, "
        "discover = A2A skill card. Token-gated endpoints (signals, "
        "copy-trade) are NOT exposed here \u2014 use the public surface only."
    ),
    schema=_SCHEMA,
    emoji="\U0001f7ea",  # 🟪 — distinguishes from market_intel
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
        # action == "discover" — the only remaining valid option.
        data = svc.discover_a2a()
        return json_result(data, summary=_format_discover(data))
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


def _format_discover(data: dict[str, Any]) -> str:
    skills = data.get("skills") or []
    version = data.get("version") or data.get("a2a_version") or "?"
    return f"BV-7X A2A v{version} · {len(skills)} skill(s)"


def register(ctx) -> None:
    register_with_ctx(ctx, bv7x)
