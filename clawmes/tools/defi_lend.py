"""``defi_lend`` — Aave V3 lending operations.

Five actions:

  * ``supply``         — deposit an asset as collateral.
  * ``withdraw``       — withdraw a deposit. ``amount='all'`` redeems
    the entire position.
  * ``borrow``         — borrow against supplied collateral. Variable
    rate only (Aave V3 deprecated stable rate post-2023 incident).
  * ``repay``          — repay borrowed debt. ``amount='all'`` repays
    the full balance.
  * ``health_factor``  — read-only position summary including the
    liquidation threshold and current health factor.

This tool encodes Aave V3 Pool calldata and routes through the
wallet mode. It does NOT handle the prerequisite ERC-20 ``approve``
call — that's the user's responsibility (or the LLM should chain
into ``approvals approve`` first). Aave's Pool needs allowance on
the asset before ``supply`` and ``repay``.
"""

from __future__ import annotations

from typing import Any

from clawmes.lib.abi import UNLIMITED_ALLOWANCE, decode_uint
from clawmes.lib.decimals import to_base_units
from clawmes.lib.logger import logger_for
from clawmes.lib.params import read_int, read_str
from clawmes.lib.tool_result import error_result, json_result
from clawmes.services.aave import (
    AaveError,
    decode_user_account_data,
    encode_borrow,
    encode_get_user_account_data,
    encode_repay,
    encode_supply,
    encode_withdraw,
    pool_address,
)
from clawmes.services.wallet import get_wallet_state
from clawmes.tools.registry import register_with_ctx, write_tool

_log = logger_for("tools.defi_lend")

# Conservative gas defaults — Aave operations are 200-400k typical.
_LEND_GAS_DEFAULT = 500_000

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["supply", "withdraw", "borrow", "repay", "health_factor"],
            "description": (
                "supply: deposit collateral. withdraw: redeem deposit "
                "(amount='all' for full). borrow: variable-rate against "
                "collateral. repay: pay back debt (amount='all' for "
                "full). health_factor: read-only position summary."
            ),
        },
        "asset": {
            "type": "string",
            "description": "ERC-20 contract address. Required for all actions except health_factor.",
        },
        "amount": {
            "type": "string",
            "description": (
                "Human amount (e.g. '100'). Use 'all' for "
                "withdraw / repay to use the full position."
            ),
        },
        "chain_id": {
            "type": "integer",
            "description": "Chain id (defaults to wallet's current chain).",
        },
        "policyConfirmationNonce": {
            "type": "string",
            "description": "Set when retrying after POLICY HOLD.",
        },
    },
    "required": ["action"],
}


@write_tool(
    name="defi_lend",
    toolset="clawmes-defi",
    description=(
        "Aave V3 lending: supply / withdraw / borrow / repay an asset, "
        "or check the wallet's health factor. Supports Ethereum, Base, "
        "Arbitrum, Optimism, Polygon. Borrow uses variable rate only "
        "(Aave deprecated stable rate after 2023). Health factor < 1 "
        "means the position is liquidatable."
    ),
    schema=_SCHEMA,
    emoji="\U0001f3e6",
)
def defi_lend(args: dict[str, Any], **kwargs: Any) -> str:
    state = get_wallet_state()
    if not state.connected or not state.address:
        return error_result(
            "No wallet connected. Run /connect first.",
            code="wallet_not_connected",
        )

    action = read_str(args, "action", required=True)
    chain_id = _resolve_chain_id(args, state)

    try:
        pool = pool_address(chain_id)
    except AaveError as exc:
        return error_result(str(exc), code="unsupported_chain")

    if action == "health_factor":
        return _handle_health(state.address, chain_id)

    asset = read_str(args, "asset", required=True)
    asset_check = _validate_address(asset, "asset")
    if asset_check is not None:
        return asset_check

    if action == "supply":
        return _handle_supply(state, pool, asset, args, chain_id)
    if action == "withdraw":
        return _handle_withdraw(state, pool, asset, args, chain_id)
    if action == "borrow":
        return _handle_borrow(state, pool, asset, args, chain_id)
    return _handle_repay(state, pool, asset, args, chain_id)


def _handle_health(owner: str, chain_id: int) -> str:
    from clawmes.services.rpc import RpcError, get_rpc_service

    # Caller already validated chain_id via the outer pool_address()
    # call before dispatching to this handler, so no try/except here.
    pool = pool_address(chain_id)
    rpc = get_rpc_service()
    try:
        raw = rpc.eth_call(
            to=pool,
            data=encode_get_user_account_data(owner),
            chain_id=chain_id,
        )
    except RpcError as exc:
        return error_result(f"Could not read Aave position: {exc.message}", code="rpc_error")

    data = decode_user_account_data(raw)
    health_human = data["health_factor"] / 10**18 if data["health_factor"] else 0.0
    risk = _classify_health(health_human, data["total_debt_base"])
    return json_result(
        {
            "chain_id": chain_id,
            "owner": owner,
            **data,
            "health_factor_human": health_human,
            "risk_level": risk,
        },
        summary=(
            f"Aave V3 position on chain {chain_id}\n"
            f"  Collateral:  ${data['total_collateral_base'] / 10**8:.2f}\n"
            f"  Debt:        ${data['total_debt_base'] / 10**8:.2f}\n"
            f"  Available:   ${data['available_borrows_base'] / 10**8:.2f}\n"
            f"  Health:      {health_human:.4f} ({risk})"
        ),
    )


def _classify_health(health: float, debt: int) -> str:
    if debt == 0:
        return "no_debt"
    if health < 1.0:
        return "liquidatable"
    if health < 1.1:
        return "critical"
    if health < 1.5:
        return "risky"
    return "safe"


def _handle_supply(state, pool, asset, args, chain_id):
    amount_base = _read_amount_base(args, asset, chain_id, allow_all=False)
    if isinstance(amount_base, str):
        return amount_base
    return _send(
        state=state,
        pool=pool,
        calldata=encode_supply(asset, amount_base, state.address),
        chain_id=chain_id,
        action="supply",
        asset=asset,
        amount_base=amount_base,
    )


def _handle_withdraw(state, pool, asset, args, chain_id):
    amount_base = _read_amount_base(args, asset, chain_id, allow_all=True)
    if isinstance(amount_base, str):
        return amount_base
    return _send(
        state=state,
        pool=pool,
        calldata=encode_withdraw(asset, amount_base, state.address),
        chain_id=chain_id,
        action="withdraw",
        asset=asset,
        amount_base=amount_base,
    )


def _handle_borrow(state, pool, asset, args, chain_id):
    amount_base = _read_amount_base(args, asset, chain_id, allow_all=False)
    if isinstance(amount_base, str):
        return amount_base
    return _send(
        state=state,
        pool=pool,
        calldata=encode_borrow(asset, amount_base, state.address),
        chain_id=chain_id,
        action="borrow",
        asset=asset,
        amount_base=amount_base,
    )


def _handle_repay(state, pool, asset, args, chain_id):
    amount_base = _read_amount_base(args, asset, chain_id, allow_all=True)
    if isinstance(amount_base, str):
        return amount_base
    return _send(
        state=state,
        pool=pool,
        calldata=encode_repay(asset, amount_base, state.address),
        chain_id=chain_id,
        action="repay",
        asset=asset,
        amount_base=amount_base,
    )


def _send(*, state, pool, calldata, chain_id, action, asset, amount_base) -> str:
    from clawmes.services.wallet import get_wallet_service

    svc = get_wallet_service()
    mode = svc.active_mode
    if mode is None:
        return error_result(
            "No active wallet mode; reconnect via /connect.",
            code="wallet_not_connected",
        )

    try:
        tx_hash = mode.send_transaction(
            to=pool,
            value=0,
            data=calldata,
            gas=_LEND_GAS_DEFAULT,
            chain_id=chain_id,
        )
    except Exception as exc:  # noqa: BLE001
        return error_result(f"{action} failed: {exc}", code="send_failed")

    return json_result(
        {
            "tx_hash": tx_hash,
            "chain_id": chain_id,
            "pool": pool,
            "action": action,
            "asset": asset,
            "amount": str(amount_base),
            "is_full_balance": amount_base == UNLIMITED_ALLOWANCE,
        },
        summary=(f"Aave {action}: {tx_hash}\n  asset={asset}\n  amount={amount_base}"),
    )


# --- helpers --------------------------------------------------------------


def _read_amount_base(
    args: dict[str, Any], asset: str, chain_id: int, *, allow_all: bool
) -> int | str:
    """Returns int on success, or an error_result string on failure."""
    raw = read_str(args, "amount", required=True)
    if raw.strip().lower() == "all":
        if not allow_all:
            return error_result(
                "amount='all' is only valid for withdraw / repay.",
                code="param_error",
            )
        return UNLIMITED_ALLOWANCE

    from clawmes.services.token_decimals import (
        TokenDecimalsError,
        get_token_decimals_service,
    )

    try:
        decimals = get_token_decimals_service().get_strict(asset, chain_id)
    except TokenDecimalsError as exc:
        return error_result(
            f"Could not determine decimals for {asset}: {exc.cause}",
            code="decimals_lookup_failed",
        )
    try:
        return to_base_units(raw, decimals)
    except (ValueError, ArithmeticError) as exc:
        return error_result(f"Bad amount {raw!r}: {exc}", code="param_error")


def _validate_address(value: str, label: str) -> str | None:
    if not value or not value.startswith(("0x", "0X")) or len(value) != 42:
        return error_result(f"Invalid {label} address: {value!r}", code="param_error")
    return None


def _resolve_chain_id(args: dict[str, Any], state) -> int:
    explicit = read_int(args, "chain_id")
    if explicit is not None:
        return explicit
    return int(state.chain_id) if state.chain_id is not None else 8453


# Re-export decode_uint so tests have access without leaking the import
__all__ = ["defi_lend", "decode_uint"]


def register(ctx) -> None:
    """Wire ``defi_lend`` into Hermes."""
    register_with_ctx(ctx, defi_lend)
