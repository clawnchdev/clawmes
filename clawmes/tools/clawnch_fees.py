"""``clawnch_fees`` — read LP fee accrual + claim Clawnch-launch fees.

Reads-side companion to ``clawnch_launch``. Actions:

Base side (Clanker):

  * ``my_launches`` — list the authenticated agent's launches with
    aggregate fee accrual. Uses Clawnch's ``/api/agents/me`` endpoint.
  * ``launch_info`` — per-token launch detail (price, volume, fees so
    far). Uses Clawnch's ``/api/launches?address=…`` endpoint.

Robinhood Chain side (Bags.fm launch router):

  * ``rh_launches`` — the RHC launch feed (``/api/robinhood/launches``),
    optionally filtered by agent wallet. Every row carries bags.fm +
    Blockscout links.
  * ``rh_claimable`` — read a wallet's accrued RHC fees for a token
    (``GET /api/robinhood/claim``): claimer bps + claimable wei, plus
    the unsigned ``BagsFeeShare.claim(true)`` tx when the wallet is a
    claimer.
  * ``rh_claim`` — the unsigned claim tx for the registered agent wallet
    (``POST /api/robinhood/claim``). The agent signs and sends it from
    its own wallet; Clawnch never claims on the agent's behalf.

Base claim-side ops still aren't implemented in clawmes: Clanker pays
creator rewards via its own LP-fee accumulator (FeeLocker) on Base, and
that claim lives in the Clanker dashboard rather than the Clawnch HTTP
API. Robinhood Chain claims **are** served by the launchpad API, which
is what the ``rh_claim*`` actions wrap.

Requires ``CLAWNCH_API_KEY`` for ``my_launches`` and ``rh_claim``;
``launch_info``, ``rh_launches`` and ``rh_claimable`` are public and
work without a key.
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
            "enum": [
                "my_launches",
                "launch_info",
                "rh_launches",
                "rh_claimable",
                "rh_claim",
            ],
        },
        "token": {
            "type": "string",
            "description": "Token address (launch_info, rh_claimable, rh_claim).",
        },
        "address": {
            "type": "string",
            "description": (
                "Wallet to read claimable fees for (rh_claimable; defaults "
                "to the registered agent wallet when known)."
            ),
        },
        "agent": {
            "type": "string",
            "description": ("Filter the Robinhood Chain feed to one agent wallet (rh_launches)."),
        },
        "limit": {
            "type": "integer",
            "description": "Feed page size, 1-200 (rh_launches, default 50).",
        },
        "offset": {
            "type": "integer",
            "description": "Feed page offset (rh_launches, default 0).",
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
        "Base launches; launch_info reads detail for a single token; "
        "rh_launches reads the Robinhood Chain launch feed; "
        "rh_claimable reads accrued RHC fees + the unsigned claim tx for "
        "any wallet, and rh_claim returns the unsigned "
        "BagsFeeShare.claim(true) tx for the registered agent wallet."
    ),
    schema=_SCHEMA,
    emoji="\U0001f4b0",
)
def clawnch_fees(args: dict[str, Any], **kwargs: Any) -> str:
    action = read_str(args, "action", required=True)

    try:
        if action == "my_launches":
            return _handle_my_launches()
        if action == "rh_launches":
            return _handle_rh_launches(args)
        if action == "rh_claimable":
            return _handle_rh_claimable(args)
        if action == "rh_claim":
            return _handle_rh_claim(args)
        # action == "launch_info"
        return _handle_launch_info(args)
    except Exception as exc:  # noqa: BLE001 — service errors carry codes
        code = getattr(exc, "code", None) or "api_error"
        return error_result(str(exc), code=code)


def _handle_my_launches() -> str:
    from clawmes.services.clawnch import get_clawnch_service

    data = get_clawnch_service().get_my_launches()
    return json_result(data, summary=_format_my_launches(data))


def _handle_launch_info(args: dict[str, Any]) -> str:
    from clawmes.services.clawnch import get_clawnch_service

    token = read_str(args, "token")
    if not token:
        return error_result(
            "launch_info requires 'token' (address).",
            code="param_error",
        )
    data = get_clawnch_service().get_launch(token)
    return json_result(data, summary=f"Launch detail for {token}")


def _as_dict(value: Any) -> dict[str, Any]:
    """Narrow an untyped JSON value to a dict (empty when it isn't one)."""
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    """Narrow an untyped JSON value to a list (empty when it isn't one)."""
    return value if isinstance(value, list) else []


def _int_arg(value: Any, default: int) -> int:
    """Parse an optional integer tool argument (``None``/``""`` → default)."""
    if value is None or value == "":
        return default
    return int(value)


def _handle_rh_launches(args: dict[str, Any]) -> str:
    from clawmes.services.clawnch import get_clawnch_service

    agent = read_str(args, "agent") or None
    data = get_clawnch_service().rh_launches(
        agent=agent,
        limit=_int_arg(args.get("limit"), 50),
        offset=_int_arg(args.get("offset"), 0),
    )
    launches = _as_list(data.get("launches"))
    pagination = _as_dict(data.get("pagination"))
    total = pagination.get("total")
    scope = f" for {agent}" if agent else ""
    summary = (
        f"{len(launches)} Robinhood Chain launch(es){scope}"
        + (f" of {total}" if total is not None else "")
        + "."
    )
    return json_result(data, summary=summary)


def _handle_rh_claimable(args: dict[str, Any]) -> str:
    from clawmes.services.clawnch import get_clawnch_service

    token = read_str(args, "token")
    if not token:
        return error_result(
            "rh_claimable requires 'token' (the launched token's address).",
            code="param_error",
        )
    address = read_str(args, "address")
    if not address:
        return error_result(
            "rh_claimable requires 'address' (the wallet to inspect).",
            code="param_error",
        )
    data = get_clawnch_service().rh_claimable(token=token, address=address)
    claimable_wei = data.get("claimableWei", "0")
    try:
        claimable_eth = f"{int(claimable_wei) / 1e18:.6f}"
    except (TypeError, ValueError):
        claimable_eth = "?"
    if data.get("isClaimer"):
        summary = (
            f"{address} can claim {claimable_eth} ETH of fees for {token} "
            f"(claimer share {data.get('bps', 0)} bps)."
        )
    else:
        summary = f"{address} is not a fee claimer for {token}."
    return json_result(data, summary=summary)


def _handle_rh_claim(args: dict[str, Any]) -> str:
    from clawmes.services.clawnch import get_clawnch_service

    token = read_str(args, "token")
    if not token:
        return error_result(
            "rh_claim requires 'token' (the launched token's address).",
            code="param_error",
        )
    data = get_clawnch_service().rh_claim(token=token)
    if not data.get("ready"):
        summary = "Nothing claimable right now on Robinhood Chain" + (
            f" ({data.get('note')})" if data.get("note") else "."
        )
        return json_result(data, summary=summary)
    claim = _as_dict(data.get("claim"))
    summary = (
        f"Unsigned claim tx ready ({data.get('claimableWei', '0')} wei). "
        f"Sign and send it from the agent wallet to {claim.get('to')} "
        "to receive the accrued fees as native ETH."
    )
    return json_result(data, summary=summary)


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
