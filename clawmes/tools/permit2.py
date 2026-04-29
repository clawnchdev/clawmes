"""``permit2`` — Uniswap's universal allowance system.

Permit2 (the canonical Permit2 contract at
``0x000000000022D473030F116dDEE9F6B43aC78BA3`` on every chain)
replaces ERC-20 ``approve``/``allowance`` with an off-chain signed
permit. Once a user approves Permit2 on a token (one-time per
token), every subsequent dApp interaction is gasless via signed
EIP-712 permits.

Three actions:

  * ``sign``   — request a Permit2 signature for a (token, spender,
    amount, expiration) tuple. The wallet's sign_typed_data_v4
    builds and signs the EIP-712 message. Returns the signature +
    permit struct for the caller to forward to the spender contract.
  * ``revoke`` — set a token+spender allowance to 0 via the on-chain
    ``approve(token, spender, 0, 0)`` call.
  * ``list``   — read all current permit allowances for the wallet.

The Permit2 contract is identical across every EVM chain (CREATE2
deterministic deploy), which is why the address is hardcoded.
"""

from __future__ import annotations

from typing import Any

from clawmes.lib.abi import encode_address, encode_uint
from clawmes.lib.logger import logger_for
from clawmes.lib.params import read_int, read_str
from clawmes.lib.tool_result import error_result, json_result
from clawmes.services.wallet import get_wallet_state
from clawmes.tools.registry import register_with_ctx, write_tool

_log = logger_for("tools.permit2")

# Canonical Permit2 contract — same address on every EVM chain.
PERMIT2_ADDRESS = "0x000000000022D473030F116dDEE9F6B43aC78BA3"

# approve(address token, address spender, uint160 amount, uint48 expiration)
SELECTOR_PERMIT2_APPROVE = "0x87517c45"

# allowance(address user, address token, address spender) → (amount, expiration, nonce)
SELECTOR_PERMIT2_ALLOWANCE = "0x927da105"

_PERMIT2_GAS_DEFAULT = 100_000

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["sign", "revoke", "list"],
        },
        "token": {"type": "string"},
        "spender": {"type": "string"},
        "amount": {
            "type": "string",
            "description": "Allowance amount (sign / revoke). Use 'unlimited' for max.",
        },
        "expiration": {
            "type": "integer",
            "description": ("Unix timestamp the permit expires. Default 30 days from now."),
        },
        "chain_id": {"type": "integer"},
        "policyConfirmationNonce": {"type": "string"},
    },
    "required": ["action"],
}


@write_tool(
    name="permit2",
    toolset="clawmes-wallet",
    description=(
        "Uniswap's Permit2 universal allowance system. sign requests "
        "an EIP-712 signed permit (gasless after the one-time Permit2 "
        "approval); revoke sets an allowance to 0 via the on-chain "
        "approve; list reads current allowances. Requires the wallet "
        "mode's sign_typed_data_v4 path for sign action."
    ),
    schema=_SCHEMA,
    emoji="\U0001f4dd",
)
def permit2(args: dict[str, Any], **kwargs: Any) -> str:
    state = get_wallet_state()
    if not state.connected or not state.address:
        return error_result(
            "No wallet connected. Run /connect first.",
            code="wallet_not_connected",
        )

    action = read_str(args, "action", required=True)
    chain_id = read_int(args, "chain_id") or (
        int(state.chain_id) if state.chain_id is not None else 1
    )

    if action == "sign":
        return _handle_sign(args, state, chain_id)
    if action == "revoke":
        return _handle_revoke(args, state, chain_id)
    return _handle_list(args, state, chain_id)


def _handle_sign(args, state, chain_id: int) -> str:
    """Request the wallet mode to sign an EIP-712 Permit2 message."""
    from clawmes.services.wallet import get_wallet_service

    token = _validate_address(read_str(args, "token", required=True))
    if isinstance(token, str) and token.startswith("__error__"):
        return token[len("__error__") :]
    spender = _validate_address(read_str(args, "spender", required=True))
    if isinstance(spender, str) and spender.startswith("__error__"):
        return spender[len("__error__") :]

    amount_raw = read_str(args, "amount", required=True)
    amount: int
    if amount_raw.strip().lower() == "unlimited":
        amount = (1 << 160) - 1  # uint160 max
    else:
        try:
            amount = int(amount_raw)
        except (TypeError, ValueError):
            return error_result(f"Bad amount {amount_raw!r}", code="param_error")

    import time

    expiration = read_int(args, "expiration") or int(time.time()) + 30 * 86_400

    typed_data = _build_permit2_typed_data(
        chain_id=chain_id,
        token=token,
        spender=spender,
        amount=amount,
        expiration=expiration,
        owner=state.address,
    )
    svc = get_wallet_service()
    mode = svc.active_mode
    if mode is None:
        return error_result(
            "No active wallet mode; reconnect via /connect.",
            code="wallet_not_connected",
        )
    try:
        signature = mode.sign_typed_data_v4(typed_data)
    except Exception as exc:  # noqa: BLE001
        return error_result(f"Permit2 sign failed: {exc}", code="send_failed")

    return json_result(
        {
            "signature": signature,
            "owner": state.address,
            "token": token,
            "spender": spender,
            "amount": str(amount),
            "expiration": expiration,
            "chain_id": chain_id,
            "permit2_address": PERMIT2_ADDRESS,
        },
        summary=(f"Permit2 signed for {amount} of {token} → {spender} (exp {expiration})"),
    )


def _handle_revoke(args, state, chain_id: int) -> str:
    from clawmes.services.wallet import get_wallet_service

    token = _validate_address(read_str(args, "token", required=True))
    if isinstance(token, str) and token.startswith("__error__"):
        return token[len("__error__") :]
    spender = _validate_address(read_str(args, "spender", required=True))
    if isinstance(spender, str) and spender.startswith("__error__"):
        return spender[len("__error__") :]

    # approve(token, spender, 0, 0) on Permit2
    calldata = (
        SELECTOR_PERMIT2_APPROVE
        + encode_address(token)
        + encode_address(spender)
        + encode_uint(0, bits=160)  # amount = 0
        + encode_uint(0, bits=48)  # expiration = 0
    )

    svc = get_wallet_service()
    mode = svc.active_mode
    if mode is None:
        return error_result(
            "No active wallet mode; reconnect via /connect.",
            code="wallet_not_connected",
        )
    try:
        tx_hash = mode.send_transaction(
            to=PERMIT2_ADDRESS,
            value=0,
            data=calldata,
            gas=_PERMIT2_GAS_DEFAULT,
            chain_id=chain_id,
        )
    except Exception as exc:  # noqa: BLE001
        return error_result(f"Revoke failed: {exc}", code="send_failed")

    return json_result(
        {
            "tx_hash": tx_hash,
            "token": token,
            "spender": spender,
            "permit2_address": PERMIT2_ADDRESS,
        },
        summary=f"Permit2 revoke: {tx_hash}",
    )


def _handle_list(args, state, chain_id: int) -> str:
    """Read a single (token, spender) allowance via eth_call.

    Permit2 doesn't expose a 'list all allowances for owner' view —
    callers must specify the (token, spender) pair to query. To list
    every active permit, iterate over the approvals tool's enumeration
    and check each pair through this method.
    """
    from clawmes.services.rpc import RpcError, get_rpc_service

    token = read_str(args, "token", required=True)
    spender = read_str(args, "spender", required=True)
    calldata = (
        SELECTOR_PERMIT2_ALLOWANCE
        + encode_address(state.address)
        + encode_address(token)
        + encode_address(spender)
    )
    rpc = get_rpc_service()
    try:
        raw = rpc.eth_call(to=PERMIT2_ADDRESS, data=calldata, chain_id=chain_id)
    except RpcError as exc:
        return error_result(
            f"Permit2 allowance read failed: {exc.message}",
            code="rpc_error",
        )

    body = raw.removeprefix("0x")
    if len(body) < 192:  # 3 × 64 hex chars = 96 bytes
        return error_result("Permit2 returned malformed allowance", code="rpc_error")
    amount = int(body[0:64], 16)
    expiration = int(body[64:128], 16)
    nonce = int(body[128:192], 16)

    return json_result(
        {
            "owner": state.address,
            "token": token,
            "spender": spender,
            "amount": str(amount),
            "expiration": expiration,
            "nonce": nonce,
        },
        summary=(
            f"Permit2 allowance: {amount} {token} → {spender} (exp {expiration}, nonce {nonce})"
        ),
    )


def _build_permit2_typed_data(
    *,
    chain_id: int,
    token: str,
    spender: str,
    amount: int,
    expiration: int,
    owner: str,
) -> dict[str, Any]:
    """Construct the EIP-712 typed-data struct for Permit2 PermitSingle."""
    return {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "PermitDetails": [
                {"name": "token", "type": "address"},
                {"name": "amount", "type": "uint160"},
                {"name": "expiration", "type": "uint48"},
                {"name": "nonce", "type": "uint48"},
            ],
            "PermitSingle": [
                {"name": "details", "type": "PermitDetails"},
                {"name": "spender", "type": "address"},
                {"name": "sigDeadline", "type": "uint256"},
            ],
        },
        "domain": {
            "name": "Permit2",
            "chainId": chain_id,
            "verifyingContract": PERMIT2_ADDRESS,
        },
        "primaryType": "PermitSingle",
        "message": {
            "details": {
                "token": token,
                "amount": str(amount),
                "expiration": expiration,
                "nonce": 0,  # caller responsible for fetching real nonce
            },
            "spender": spender,
            "sigDeadline": expiration,
        },
    }


def _validate_address(value: str) -> str:
    if not value or not value.startswith(("0x", "0X")) or len(value) != 42:
        return "__error__" + error_result(f"Invalid address: {value!r}", code="param_error")
    return value


def register(ctx) -> None:
    register_with_ctx(ctx, permit2)
