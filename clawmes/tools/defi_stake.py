"""``defi_stake`` — liquid ETH staking via Lido / Rocket Pool.

Three actions:

  * ``stake`` — send ETH to Lido (stETH) or Rocket Pool (rETH).
    Returns the tx hash; the receipt token mints in the same tx.
  * ``info``  — read-only summary: current receipt-token balance,
    contract address, protocol description.
  * ``unstake`` — placeholder. Withdrawal flows for both protocols
    are multi-step (Lido's queue + claim, Rocket Pool's burn). Not
    implemented at this milestone; returns ``not_implemented``.

Mainnet only. The wrapped tokens (cbETH on Base, wstETH on
Arbitrum/Optimism/Base) are accessed via the ``bridge`` tool — they
exist on L2s but aren't directly staked.

Why no ``claim``: Lido withdrawals require waiting for the queue
(typically days) and then claiming via an NFT-receipted request.
That's a separate workflow that needs its own multi-step UX.
"""

from __future__ import annotations

from typing import Any

from clawmes.lib.abi import decode_uint, encode_balance_of
from clawmes.lib.decimals import to_base_units
from clawmes.lib.logger import logger_for
from clawmes.lib.params import read_int, read_str
from clawmes.lib.tool_result import error_result, json_result
from clawmes.services.staking import (
    StakingError,
    deposit_target,
    protocol_name,
    receipt_token,
    supported_protocols,
)
from clawmes.services.wallet import get_wallet_state
from clawmes.tools.registry import register_with_ctx, write_tool

_log = logger_for("tools.defi_stake")

# Conservative — Lido submit ~120k, Rocket Pool deposit ~150k.
_STAKE_GAS_DEFAULT = 250_000

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["stake", "unstake", "info"],
            "description": (
                "stake: deposit ETH for receipt token. "
                "unstake: not yet implemented (multi-step withdraw). "
                "info: read-only protocol + balance summary."
            ),
        },
        "protocol": {
            "type": "string",
            "enum": ["lido", "rocketpool"],
            "description": "Liquid-staking protocol.",
        },
        "amount": {
            "type": "string",
            "description": "ETH amount in human units (e.g. '0.5'). Required for stake.",
        },
        "chain_id": {
            "type": "integer",
            "description": "Chain id (defaults to wallet's chain; both protocols are mainnet-only).",
        },
        "policyConfirmationNonce": {
            "type": "string",
            "description": "Set when retrying after POLICY HOLD.",
        },
    },
    "required": ["action", "protocol"],
}


@write_tool(
    name="defi_stake",
    toolset="clawmes-defi",
    description=(
        "Liquid ETH staking via Lido (stETH) or Rocket Pool (rETH). "
        "Stake deposits ETH and mints the protocol's receipt token in "
        "the same tx. Mainnet only. Unstaking is a multi-step queue "
        "(not yet supported); use 'info' to check balances."
    ),
    schema=_SCHEMA,
    emoji="\U0001f3df\ufe0f",
)
def defi_stake(args: dict[str, Any], **kwargs: Any) -> str:
    state = get_wallet_state()
    if not state.connected or not state.address:
        return error_result(
            "No wallet connected. Run /connect first.",
            code="wallet_not_connected",
        )

    action = read_str(args, "action", required=True)
    protocol = read_str(args, "protocol", required=True).lower()
    if protocol not in supported_protocols():
        return error_result(
            f"Unknown protocol {protocol!r}. Supported: {supported_protocols()}",
            code="param_error",
        )

    chain_id = _resolve_chain_id(args, state)

    if action == "info":
        return _handle_info(state, protocol, chain_id)
    if action == "unstake":
        return error_result(
            f"{protocol} unstake is multi-step (queue + claim) and not "
            "yet implemented. Use the protocol's web UI or wait for a "
            "later release.",
            code="not_implemented",
        )
    return _handle_stake(state, protocol, chain_id, args)


def _handle_info(state, protocol: str, chain_id: int) -> str:
    from clawmes.services.rpc import RpcError, get_rpc_service

    try:
        token = receipt_token(protocol, chain_id)
    except StakingError as exc:
        return error_result(str(exc), code="unsupported_chain")

    rpc = get_rpc_service()
    try:
        raw = rpc.eth_call(
            to=token,
            data=encode_balance_of(state.address),
            chain_id=chain_id,
        )
        balance = decode_uint(raw)
    except RpcError as exc:
        return error_result(
            f"Could not read receipt-token balance: {exc.message}",
            code="rpc_error",
        )

    return json_result(
        {
            "protocol": protocol,
            "name": protocol_name(protocol),
            "chain_id": chain_id,
            "receipt_token": token,
            "balance": str(balance),
            "balance_eth_estimate": balance / 10**18,
        },
        summary=(
            f"{protocol_name(protocol)} on chain {chain_id}\n"
            f"  Receipt token: {token}\n"
            f"  Your balance:  {balance / 10**18:.6f} (≈ ETH)"
        ),
    )


def _handle_stake(state, protocol: str, chain_id: int, args) -> str:
    from clawmes.services.wallet import get_wallet_service

    amount_raw = read_str(args, "amount", required=True)
    try:
        value_wei = to_base_units(amount_raw, 18)
    except (ValueError, ArithmeticError) as exc:
        return error_result(f"Bad amount {amount_raw!r}: {exc}", code="param_error")

    try:
        contract, calldata = deposit_target(protocol, chain_id)
    except StakingError as exc:
        return error_result(str(exc), code="unsupported_chain")

    svc = get_wallet_service()
    mode = svc.active_mode
    if mode is None:
        return error_result(
            "No active wallet mode; reconnect via /connect.",
            code="wallet_not_connected",
        )

    try:
        tx_hash = mode.send_transaction(
            to=contract,
            value=value_wei,
            data=calldata,
            gas=_STAKE_GAS_DEFAULT,
            chain_id=chain_id,
        )
    except Exception as exc:  # noqa: BLE001
        return error_result(f"Stake failed: {exc}", code="send_failed")

    result = {
        "tx_hash": tx_hash,
        "protocol": protocol,
        "name": protocol_name(protocol),
        "chain_id": chain_id,
        "amount_eth": amount_raw,
        "amount_wei": str(value_wei),
        "contract": contract,
    }
    # Desktop UI: clickable explorer link for the stake tx.
    from clawmes.lib.ui_artifacts import enrich_tx_links

    enrich_tx_links(result, tx_hash=tx_hash, chain_id=chain_id)
    return json_result(
        result,
        summary=(
            f"Staked {amount_raw} ETH via {protocol_name(protocol)}: {tx_hash}\n"
            f"  Receipt token will mint in the same block."
        ),
    )


def _resolve_chain_id(args: dict[str, Any], state) -> int:
    explicit = read_int(args, "chain_id")
    if explicit is not None:
        return explicit
    return int(state.chain_id) if state.chain_id is not None else 1


def register(ctx) -> None:
    """Wire ``defi_stake`` into Hermes."""
    register_with_ctx(ctx, defi_stake)
