"""``defi_swap`` — DEX aggregation via 0x.

Three actions:

  * ``quote`` — read-only price preview. Calls 0x ``/swap/permit2/price``
    with a sell or buy amount and returns the expected output, gas
    estimate, and route summary. No signing, no allowance commitment.

  * ``swap``  — full swap execution. Calls 0x ``/swap/permit2/quote``
    to get calldata + permit2 EIP-712 payload, signs the permit2 with
    the wallet, then broadcasts the swap calldata to 0x's exchange
    proxy. Returns the tx hash. Receipt polling can be added by the
    caller via the rpc service's ``wait_for_receipt``.

  * ``route`` — read-only route comparison. Currently shows the 0x
    quote only; multi-aggregator comparison (1inch, LiFi) lands in
    a follow-up.

Permit2 vs. legacy approve:

  0x's ``/swap/permit2/`` endpoints return calldata that's pre-signed
  via Permit2 (Uniswap's universal allowance system). The user signs
  ONE permit per transaction instead of the two-step
  ``approve → swap`` dance. This is the modern path; the legacy
  ``approve(allowanceTarget, max) → swap`` flow is supported via the
  ``approvals`` tool but not used here.

Gas: 0x's quote includes a gas estimate. We pass that through to the
wallet mode, which converts it into a tx with appropriate gas headroom.
"""

from __future__ import annotations

from typing import Any

from clawmes.lib.decimals import to_base_units
from clawmes.lib.logger import logger_for
from clawmes.lib.params import read_int, read_str
from clawmes.lib.tool_result import error_result, json_result
from clawmes.services.wallet import get_wallet_state
from clawmes.tools.registry import register_with_ctx, write_tool

_log = logger_for("tools.defi_swap")

# Native ETH / gas token sentinel — 0x uses this for native swaps.
_NATIVE_ADDRESS = "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"

# Default slippage when the LLM doesn't specify. 1% is the common DEX
# default; 0x silently caps higher values per chain.
_DEFAULT_SLIPPAGE_BPS = 100

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["quote", "swap", "route"],
            "description": (
                "quote: read-only price preview, no signing. "
                "swap: full execution with permit2 signature. "
                "route: read-only multi-aggregator comparison "
                "(0x only at this milestone)."
            ),
        },
        "sell_token": {
            "type": "string",
            "description": (
                "Token to sell (ERC-20 address) or 'ETH' / native sentinel for the gas token."
            ),
        },
        "buy_token": {
            "type": "string",
            "description": "Token to receive. Same format as sell_token.",
        },
        "sell_amount": {
            "type": "string",
            "description": (
                "Amount to sell (human units, e.g. '0.5'). "
                "Exactly one of sell_amount / buy_amount required."
            ),
        },
        "buy_amount": {
            "type": "string",
            "description": (
                "Target buy amount (human units). Exactly one of sell_amount / buy_amount required."
            ),
        },
        "slippage_bps": {
            "type": "integer",
            "description": ("Slippage tolerance in basis points (100 = 1%, default 100)."),
        },
        "chain_id": {
            "type": "integer",
            "description": "Override the wallet's chain id.",
        },
        "policyConfirmationNonce": {
            "type": "string",
            "description": "Set when retrying after a POLICY HOLD.",
        },
    },
    "required": ["action", "sell_token", "buy_token"],
}


@write_tool(
    name="defi_swap",
    toolset="clawmes-trading",
    description=(
        "DEX swap via 0x aggregator. quote returns a price preview "
        "without signing; swap executes the trade with a permit2 "
        "signature; route compares aggregators (0x only at this "
        "milestone). Supports ETH ↔ ERC-20 and ERC-20 ↔ ERC-20 across "
        "Ethereum, Base, Arbitrum, Optimism, Polygon."
    ),
    schema=_SCHEMA,
    emoji="\U0001f504",
)
def defi_swap(args: dict[str, Any], **kwargs: Any) -> str:
    state = get_wallet_state()
    if not state.connected:
        return error_result(
            "No wallet connected. Run /connect first.",
            code="wallet_not_connected",
        )

    action = read_str(args, "action", required=True)
    sell_token = _resolve_token(read_str(args, "sell_token", required=True))
    buy_token = _resolve_token(read_str(args, "buy_token", required=True))
    chain_id = _resolve_chain_id(args, state)
    slippage_bps = read_int(args, "slippage_bps") or _DEFAULT_SLIPPAGE_BPS

    sell_human = read_str(args, "sell_amount")
    buy_human = read_str(args, "buy_amount")
    if (sell_human is None) == (buy_human is None):
        return error_result(
            "Provide exactly one of sell_amount / buy_amount.",
            code="param_error",
        )

    try:
        sell_base, buy_base = _resolve_amounts(
            sell_token=sell_token,
            buy_token=buy_token,
            sell_human=sell_human,
            buy_human=buy_human,
            chain_id=chain_id,
        )
    except ValueError as exc:
        return error_result(str(exc), code="param_error")

    if action == "quote":
        return _handle_quote(
            sell_token=sell_token,
            buy_token=buy_token,
            sell_base=sell_base,
            buy_base=buy_base,
            chain_id=chain_id,
            slippage_bps=slippage_bps,
            taker=state.address,
        )
    if action == "route":
        return _handle_route(
            sell_token=sell_token,
            buy_token=buy_token,
            sell_base=sell_base,
            buy_base=buy_base,
            chain_id=chain_id,
            slippage_bps=slippage_bps,
            taker=state.address,
        )
    return _handle_swap(
        state=state,
        sell_token=sell_token,
        buy_token=buy_token,
        sell_base=sell_base,
        buy_base=buy_base,
        chain_id=chain_id,
        slippage_bps=slippage_bps,
    )


def _handle_quote(
    *,
    sell_token: str,
    buy_token: str,
    sell_base: int | None,
    buy_base: int | None,
    chain_id: int,
    slippage_bps: int,
    taker: str | None,
) -> str:
    from clawmes.services.zerox import ZeroxError, get_zerox_service

    try:
        price = get_zerox_service().get_price(
            chain_id=chain_id,
            sell_token=sell_token,
            buy_token=buy_token,
            sell_amount=sell_base,
            buy_amount=buy_base,
            taker=taker,
            slippage_bps=slippage_bps,
        )
    except ZeroxError as exc:
        return error_result(f"0x quote failed: {exc.message}", code=exc.code)

    return json_result(
        _shape_price(price, chain_id=chain_id, slippage_bps=slippage_bps),
        summary=_render_quote(price, sell_token, buy_token, chain_id),
    )


def _handle_route(
    *,
    sell_token: str,
    buy_token: str,
    sell_base: int | None,
    buy_base: int | None,
    chain_id: int,
    slippage_bps: int,
    taker: str | None,
) -> str:
    """Currently 0x-only; multi-aggregator comparison TBD.

    The result shape includes a ``routes`` array so callers don't
    have to change when 1inch + LiFi land — they'll just see more
    entries in the array.
    """
    from clawmes.services.zerox import ZeroxError, get_zerox_service

    routes: list[dict[str, Any]] = []
    try:
        zerox_price = get_zerox_service().get_price(
            chain_id=chain_id,
            sell_token=sell_token,
            buy_token=buy_token,
            sell_amount=sell_base,
            buy_amount=buy_base,
            taker=taker,
            slippage_bps=slippage_bps,
        )
        routes.append(
            {
                "aggregator": "0x",
                **_shape_price(zerox_price, chain_id=chain_id, slippage_bps=slippage_bps),
            }
        )
    except ZeroxError as exc:
        routes.append({"aggregator": "0x", "error": exc.message, "error_code": exc.code})

    successful = [r for r in routes if "error" not in r]
    if not successful:
        return error_result(
            "All aggregators failed. See route errors in details.",
            code="api_error",
        )

    return json_result(
        {
            "chain_id": chain_id,
            "sell_token": sell_token,
            "buy_token": buy_token,
            "routes": routes,
            "best": successful[0]["aggregator"],
        },
        summary=_render_routes(routes, sell_token, buy_token),
    )


def _handle_swap(
    *,
    state,
    sell_token: str,
    buy_token: str,
    sell_base: int | None,
    buy_base: int | None,
    chain_id: int,
    slippage_bps: int,
) -> str:
    from clawmes.services.wallet import get_wallet_service
    from clawmes.services.zerox import ZeroxError, get_zerox_service

    if state.address is None:
        return error_result(
            "Wallet has no address; reconnect via /connect.",
            code="wallet_not_connected",
        )

    try:
        quote = get_zerox_service().get_quote(
            chain_id=chain_id,
            sell_token=sell_token,
            buy_token=buy_token,
            taker=state.address,
            sell_amount=sell_base,
            buy_amount=buy_base,
            slippage_bps=slippage_bps,
        )
    except ZeroxError as exc:
        return error_result(f"0x quote failed: {exc.message}", code=exc.code)

    transaction = quote.get("transaction") or {}
    to = transaction.get("to")
    data = transaction.get("data")
    if not to or not data:
        return error_result(
            "0x returned a quote without transaction calldata.",
            code="api_error",
        )

    from clawmes.services.zerox import parse_0x_int

    try:
        value = parse_0x_int(transaction.get("value"))
        gas = parse_0x_int(transaction.get("gas"))
    except (TypeError, ValueError) as exc:
        return error_result(f"0x returned malformed gas/value: {exc}", code="api_error")

    svc = get_wallet_service()
    mode = svc.active_mode
    if mode is None:
        return error_result(
            "No active wallet mode; reconnect via /connect.",
            code="wallet_not_connected",
        )

    # Append Coinbase builder code suffix on Base mainnet so the plugin
    # earns builder rewards proportional to the swap volume it drives.
    # No-op on other chains; safe to apply universally.
    from clawmes.lib.base_builder import append_builder_code

    final_data = append_builder_code(data, chain_id)

    try:
        tx_hash = mode.send_transaction(
            to=to,
            value=value,
            data=final_data,
            gas=gas if gas > 0 else None,
            chain_id=chain_id,
        )
    except Exception as exc:  # noqa: BLE001 — surface signing/broadcast errors
        return error_result(f"Swap broadcast failed: {exc}", code="send_failed")

    result = {
        "tx_hash": tx_hash,
        "chain_id": chain_id,
        "sell_token": sell_token,
        "buy_token": buy_token,
        "sell_amount": str(quote.get("sellAmount", "")),
        "buy_amount": str(quote.get("buyAmount", "")),
        "min_buy_amount": str(quote.get("minBuyAmount", "")),
        "router": to,
    }
    # Desktop UI: surface a clickable explorer link for the tx and market
    # links for the bought token. These are passive Link artifacts (descriptive
    # keys, not preview-trigger keys) so they never auto-open the side rail —
    # safe to emit even from scheduler-driven swaps.
    from clawmes.lib.ui_artifacts import enrich_token_links, enrich_tx_links

    enrich_tx_links(result, tx_hash=tx_hash, chain_id=chain_id)
    if buy_token and buy_token.lower() != _NATIVE_ADDRESS:
        enrich_token_links(result, token=buy_token, chain_id=chain_id)

    return json_result(
        result,
        summary=(
            f"Swap submitted: {tx_hash}\n"
            f"  {quote.get('sellAmount', '?')} {sell_token} → "
            f"{quote.get('buyAmount', '?')} {buy_token}\n"
            f"  router: {to}"
        ),
    )


# --- helpers --------------------------------------------------------------


def _resolve_token(value: str) -> str:
    """Map 'ETH' / 'native' to the 0x native sentinel; pass addresses through."""
    if value is None:
        return value
    norm = value.strip().lower()
    if norm in ("eth", "native", "matic", "pol", "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"):
        return _NATIVE_ADDRESS
    return value


def _resolve_amounts(
    *,
    sell_token: str,
    buy_token: str,
    sell_human: str | None,
    buy_human: str | None,
    chain_id: int,
) -> tuple[int | None, int | None]:
    """Convert human amounts to base units for whichever side is set."""
    if sell_human is not None:
        decimals = _token_decimals(sell_token, chain_id)
        try:
            return to_base_units(sell_human, decimals), None
        except (ValueError, ArithmeticError) as exc:
            raise ValueError(f"Bad sell_amount {sell_human!r}: {exc}") from exc
    decimals = _token_decimals(buy_token, chain_id)
    try:
        return None, to_base_units(buy_human, decimals)
    except (ValueError, ArithmeticError) as exc:
        raise ValueError(f"Bad buy_amount {buy_human!r}: {exc}") from exc


def _token_decimals(token: str, chain_id: int) -> int:
    """Return decimals for a token. Native sentinel always 18.

    Uses the strict path so a lookup failure surfaces to the caller
    rather than silently returning 18 (which would multiply the user's
    intended sell amount by 10^12 for a 6-decimal token like USDC).
    """
    if token.lower() == _NATIVE_ADDRESS:
        return 18
    from clawmes.services.token_decimals import (
        TokenDecimalsError,
        get_token_decimals_service,
    )

    try:
        return get_token_decimals_service().get_strict(token, chain_id)
    except TokenDecimalsError as exc:
        raise ValueError(f"could not determine decimals for {token}: {exc.cause}") from exc


def _resolve_chain_id(args: dict[str, Any], state) -> int:
    explicit = read_int(args, "chain_id")
    if explicit is not None:
        return explicit
    return int(state.chain_id) if state.chain_id is not None else 8453


def _shape_price(price: dict[str, Any], *, chain_id: int, slippage_bps: int) -> dict[str, Any]:
    return {
        "chain_id": chain_id,
        "sell_amount": str(price.get("sellAmount", "")),
        "buy_amount": str(price.get("buyAmount", "")),
        "min_buy_amount": str(price.get("minBuyAmount", "")),
        "estimated_gas": str(price.get("gas", "")),
        "slippage_bps": slippage_bps,
        "sources": price.get("route", {}).get("fills", []),
    }


def _render_quote(price: dict[str, Any], sell_token: str, buy_token: str, chain_id: int) -> str:
    sell = price.get("sellAmount", "?")
    buy = price.get("buyAmount", "?")
    min_buy = price.get("minBuyAmount", "?")
    return (
        f"0x quote on chain {chain_id}\n"
        f"  Sell:   {sell} {sell_token}\n"
        f"  Buy:    {buy} {buy_token}\n"
        f"  Min:    {min_buy} (after slippage)\n"
        f"  Gas:    {price.get('gas', '?')} estimated"
    )


def _render_routes(routes: list[dict[str, Any]], sell_token: str, buy_token: str) -> str:
    lines = [f"Route comparison: {sell_token} → {buy_token}"]
    for r in routes:
        if "error" in r:
            lines.append(f"  • {r['aggregator']}: {r['error']}")
        else:
            lines.append(
                f"  • {r['aggregator']}: buy={r.get('buy_amount', '?')} "
                f"min={r.get('min_buy_amount', '?')} gas={r.get('estimated_gas', '?')}"
            )
    return "\n".join(lines)


def register(ctx) -> None:
    """Wire ``defi_swap`` into Hermes."""
    register_with_ctx(ctx, defi_swap)
