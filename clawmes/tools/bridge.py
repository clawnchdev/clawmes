"""``bridge`` — cross-chain bridging via LiFi.

Four actions:

  * ``quote``  — read-only route preview. No signing, no commitment.
  * ``bridge`` — execute the bridge: gets a quote, signs the tx,
    and broadcasts. Returns the source-chain tx hash.
  * ``status`` — check a bridge tx's progress across both chains.
    A bridge tx has TWO confirmations to wait for: source-chain
    submission and destination-chain settlement (typically minutes).
  * ``routes`` — list LiFi-supported chain pairs (debugging /
    discovery).

LiFi handles the bridge selection internally: given a (source chain,
source token, destination chain, destination token, amount), it
picks the cheapest / fastest path across 30+ providers (Stargate,
Hop, Across, Connext, Synapse, etc.). The LLM doesn't pick a bridge
by name; it asks for a result and trusts LiFi to route.
"""

from __future__ import annotations

from typing import Any

from clawmes.lib.decimals import to_base_units
from clawmes.lib.logger import logger_for
from clawmes.lib.params import read_int, read_str
from clawmes.lib.tool_result import error_result, json_result
from clawmes.services.wallet import get_wallet_state
from clawmes.tools.registry import register_with_ctx, write_tool

_log = logger_for("tools.bridge")

# Native token sentinel — LiFi uses 0xeeee... like 0x.
_NATIVE_ADDRESS = "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
_DEFAULT_SLIPPAGE = 0.005  # 0.5%

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["quote", "bridge", "status", "routes"],
            "description": (
                "quote: route preview, no signing. "
                "bridge: full execution. "
                "status: track a tx's source + destination confirmations. "
                "routes: list supported chain pairs."
            ),
        },
        "from_chain": {
            "type": "integer",
            "description": "Source chain id. Defaults to the wallet's chain.",
        },
        "to_chain": {
            "type": "integer",
            "description": "Destination chain id.",
        },
        "from_token": {
            "type": "string",
            "description": "Source token (0x address or 'ETH').",
        },
        "to_token": {
            "type": "string",
            "description": "Destination token (0x address or 'ETH').",
        },
        "amount": {
            "type": "string",
            "description": "Source amount in human units.",
        },
        "to_address": {
            "type": "string",
            "description": (
                "Destination address. Defaults to the wallet's address "
                "(send to yourself on the destination chain)."
            ),
        },
        "tx_hash": {
            "type": "string",
            "description": "Tx hash to look up. Required for action=status.",
        },
        "slippage": {
            "type": "number",
            "description": "Slippage as a fraction (0.005 = 0.5%). Default 0.005.",
        },
        "policyConfirmationNonce": {
            "type": "string",
            "description": "Set when retrying after POLICY HOLD.",
        },
    },
    "required": ["action"],
}


@write_tool(
    name="bridge",
    toolset="clawmes-trading",
    description=(
        "Cross-chain bridge via LiFi aggregator. quote returns a route "
        "preview; bridge executes the cross-chain transfer; status "
        "tracks an in-flight bridge across source + destination "
        "confirmations; routes lists supported chain pairs. LiFi "
        "auto-selects from 30+ underlying bridges (Stargate, Across, "
        "Hop, etc.) for the cheapest / fastest path."
    ),
    schema=_SCHEMA,
    emoji="\U0001f309",
)
def bridge(args: dict[str, Any], **kwargs: Any) -> str:
    state = get_wallet_state()

    action = read_str(args, "action", required=True)

    # status + routes don't require a connected wallet
    if action == "status":
        return _handle_status(args)
    if action == "routes":
        return _handle_routes(args)

    # quote + bridge need a wallet
    if not state.connected or not state.address:
        return error_result(
            "No wallet connected. Run /connect first.",
            code="wallet_not_connected",
        )

    if action == "quote":
        return _handle_quote(args, state)
    return _handle_bridge(args, state)


def _handle_quote(args, state) -> str:
    from clawmes.services.lifi import LifiError, get_lifi_service

    inputs = _read_quote_inputs(args, state)
    if isinstance(inputs, str):
        return inputs

    try:
        result = get_lifi_service().get_quote(**inputs)
    except LifiError as exc:
        return error_result(f"LiFi quote failed: {exc.message}", code=exc.code)

    return json_result(
        _shape_quote(result),
        summary=_render_quote(result, inputs),
    )


def _handle_bridge(args, state) -> str:
    from clawmes.services.lifi import LifiError, get_lifi_service
    from clawmes.services.wallet import get_wallet_service

    inputs = _read_quote_inputs(args, state)
    if isinstance(inputs, str):
        return inputs

    try:
        quote = get_lifi_service().get_quote(**inputs)
    except LifiError as exc:
        return error_result(f"LiFi quote failed: {exc.message}", code=exc.code)

    tx_req = quote.get("transactionRequest") or {}
    to = tx_req.get("to")
    data = tx_req.get("data")
    value_hex = tx_req.get("value") or "0x0"
    gas_hex = tx_req.get("gasLimit") or "0x0"
    chain_hex = tx_req.get("chainId") or hex(inputs["from_chain"])
    if not to or not data:
        return error_result(
            "LiFi returned a quote without transaction calldata.",
            code="api_error",
        )

    try:
        value = int(value_hex, 16) if isinstance(value_hex, str) else int(value_hex)
        gas = int(gas_hex, 16) if isinstance(gas_hex, str) else int(gas_hex)
        chain_id_for_send = int(chain_hex, 16) if isinstance(chain_hex, str) else int(chain_hex)
    except (TypeError, ValueError) as exc:
        return error_result(f"LiFi returned malformed tx fields: {exc}", code="api_error")

    svc = get_wallet_service()
    mode = svc.active_mode
    if mode is None:
        return error_result(
            "No active wallet mode; reconnect via /connect.",
            code="wallet_not_connected",
        )

    try:
        tx_hash = mode.send_transaction(
            to=to,
            value=value,
            data=data,
            gas=gas if gas > 0 else None,
            chain_id=chain_id_for_send,
        )
    except Exception as exc:  # noqa: BLE001
        return error_result(f"Bridge broadcast failed: {exc}", code="send_failed")

    estimate = quote.get("estimate") or {}
    result = {
        "tx_hash": tx_hash,
        "from_chain": inputs["from_chain"],
        "to_chain": inputs["to_chain"],
        "from_token": inputs["from_token"],
        "to_token": inputs["to_token"],
        "from_amount": str(estimate.get("fromAmount", "")),
        "to_amount_min": str(estimate.get("toAmountMin", "")),
        "execution_duration_seconds": estimate.get("executionDuration"),
        "tool": quote.get("tool"),
        "router": to,
    }
    # Desktop UI: clickable explorer link for the source-chain tx. get_chain
    # accepts an int id or short name, so this is robust to either form of
    # from_chain; an unknown chain simply yields no link.
    from clawmes.lib.ui_artifacts import enrich_tx_links

    enrich_tx_links(result, tx_hash=tx_hash, chain_id=inputs["from_chain"])
    return json_result(
        result,
        summary=(
            f"Bridge submitted on chain {inputs['from_chain']}: {tx_hash}\n"
            f"  → chain {inputs['to_chain']}, expected ≈ "
            f"{estimate.get('toAmountMin', '?')} (after slippage)\n"
            f"  via {quote.get('tool', 'unknown')} "
            f"(~{estimate.get('executionDuration', '?')}s)\n"
            f"  Track with: bridge status tx_hash={tx_hash}"
        ),
    )


def _handle_status(args) -> str:
    from clawmes.services.lifi import LifiError, get_lifi_service

    tx_hash = read_str(args, "tx_hash", required=True)
    try:
        status = get_lifi_service().get_status(tx_hash=tx_hash)
    except LifiError as exc:
        return error_result(f"LiFi status failed: {exc.message}", code=exc.code)

    return json_result(
        {
            "tx_hash": tx_hash,
            "status": status.get("status"),
            "substatus": status.get("substatus"),
            "from_chain": status.get("fromChainId"),
            "to_chain": status.get("toChainId"),
            "sending": status.get("sending"),
            "receiving": status.get("receiving"),
        },
        summary=(
            f"Bridge {tx_hash} status: {status.get('status', 'unknown')}"
            f" / {status.get('substatus', '')}"
        ),
    )


def _handle_routes(args) -> str:
    from clawmes.services.lifi import LifiError, get_lifi_service

    from_chain = read_int(args, "from_chain")
    to_chain = read_int(args, "to_chain")
    try:
        connections = get_lifi_service().get_connections(from_chain=from_chain, to_chain=to_chain)
    except LifiError as exc:
        return error_result(f"LiFi connections failed: {exc.message}", code=exc.code)

    pairs = connections.get("connections") or []
    return json_result(
        {
            "from_chain": from_chain,
            "to_chain": to_chain,
            "count": len(pairs),
            "connections": pairs[:50],  # truncate for context size
        },
        summary=(
            f"{len(pairs)} bridge route(s) supported"
            + (f" {from_chain} → {to_chain}" if from_chain or to_chain else "")
        ),
    )


# --- helpers --------------------------------------------------------------


def _read_quote_inputs(args, state):
    """Validate + normalize quote/bridge inputs. Returns a dict ready
    for ``LifiService.get_quote(**inputs)`` or an error_result string."""
    from_chain = read_int(args, "from_chain") or (
        int(state.chain_id) if state.chain_id is not None else 8453
    )
    to_chain = read_int(args, "to_chain")
    if to_chain is None:
        return error_result("to_chain is required", code="param_error")
    if to_chain == from_chain:
        return error_result(
            "from_chain and to_chain must differ for a bridge.",
            code="param_error",
        )

    from_token = _resolve_token(read_str(args, "from_token", required=True))
    to_token = _resolve_token(read_str(args, "to_token", required=True))

    amount_raw = read_str(args, "amount", required=True)
    try:
        from_amount = _to_base(from_token, from_chain, amount_raw)
    except ValueError as exc:
        return error_result(str(exc), code="param_error")

    slippage_raw = args.get("slippage")
    try:
        slippage = float(slippage_raw) if slippage_raw is not None else _DEFAULT_SLIPPAGE
    except (TypeError, ValueError):
        slippage = _DEFAULT_SLIPPAGE

    return {
        "from_chain": from_chain,
        "to_chain": to_chain,
        "from_token": from_token,
        "to_token": to_token,
        "from_amount": from_amount,
        "from_address": state.address,
        "to_address": read_str(args, "to_address") or state.address,
        "slippage": slippage,
    }


def _resolve_token(value: str) -> str:
    if value is None:
        return value
    norm = value.strip().lower()
    if norm in ("eth", "native", "matic", "pol", "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"):
        return _NATIVE_ADDRESS
    return value


def _to_base(token: str, chain_id: int, amount: str) -> int:
    """Convert a human amount to base units. Native sentinel always 18."""
    if token.lower() == _NATIVE_ADDRESS:
        return to_base_units(amount, 18)
    from clawmes.services.token_decimals import (
        TokenDecimalsError,
        get_token_decimals_service,
    )

    try:
        decimals = get_token_decimals_service().get_strict(token, chain_id)
    except TokenDecimalsError as exc:
        raise ValueError(f"could not determine decimals for {token}: {exc.cause}") from exc
    return to_base_units(amount, decimals)


def _shape_quote(quote: dict[str, Any]) -> dict[str, Any]:
    estimate = quote.get("estimate") or {}
    return {
        "id": quote.get("id"),
        "tool": quote.get("tool"),
        "from_amount": str(estimate.get("fromAmount", "")),
        "to_amount": str(estimate.get("toAmount", "")),
        "to_amount_min": str(estimate.get("toAmountMin", "")),
        "fees": estimate.get("feeCosts", []),
        "execution_duration_seconds": estimate.get("executionDuration"),
        "included_steps": [s.get("tool") for s in (quote.get("includedSteps") or [])],
    }


def _render_quote(quote: dict[str, Any], inputs: dict[str, Any]) -> str:
    estimate = quote.get("estimate") or {}
    return (
        f"LiFi quote: chain {inputs['from_chain']} → {inputs['to_chain']}\n"
        f"  In:    {estimate.get('fromAmount', '?')} {inputs['from_token']}\n"
        f"  Out:   {estimate.get('toAmount', '?')} {inputs['to_token']}\n"
        f"  Min:   {estimate.get('toAmountMin', '?')}\n"
        f"  Tool:  {quote.get('tool', 'unknown')}\n"
        f"  Time:  ~{estimate.get('executionDuration', '?')}s"
    )


def register(ctx) -> None:
    """Wire ``bridge`` into Hermes."""
    register_with_ctx(ctx, bridge)
