"""``transfer`` — send ETH to a recipient and (optionally) wait for the receipt.

Native EVM transfers only at this milestone — ERC-20 transfers will land
when the token-transfer adapter and approval-gate plumbing are in place.

Two actions:

  * ``estimate`` — returns the gas estimate (21000 for native), the wei
    value, and the route summary. No on-chain submission.
  * ``send``    — broadcasts via the active wallet mode, returns the tx
    hash, and (when ``await_receipt`` is true, the default) blocks
    until the receipt arrives or the timeout elapses.

The tool reads the chain id from the connected wallet state — callers
that want to override pass ``chain_id`` explicitly. Token decimals come
from :mod:`clawmes.lib.chains` for native, so we don't pay for an
on-chain ``decimals()`` call when sending the gas token.

Policy gating note: the ``@write_tool`` decorator extracts
``value_wei``/``amount_wei`` for the policy evaluator. Tools using the
human-friendly ``amount`` field can also pass ``value_wei`` if they
want the quantitative gates (e.g. ``confirm_large_transfers``) to fire.
For now we leave that to the LLM caller; the gate is opt-in for the
amount path.
"""

from __future__ import annotations

from typing import Any

from clawmes.lib.chains import get_chain
from clawmes.lib.decimals import to_base_units
from clawmes.lib.ens import EnsError, is_ens_name
from clawmes.lib.ens import resolve as resolve_ens
from clawmes.lib.params import read_bool, read_str
from clawmes.lib.tool_result import error_result, json_result
from clawmes.services.wallet import get_wallet_state
from clawmes.tools.registry import register_with_ctx, write_tool

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["send", "estimate"],
            "description": "send executes; estimate returns gas + route only",
        },
        "to": {
            "type": "string",
            "description": (
                "Recipient address (0x...) or ENS name (e.g. vitalik.eth). "
                "ENS names are resolved via Ethereum mainnet regardless of "
                "the wallet's current chain."
            ),
        },
        "amount": {
            "type": "string",
            "description": "Human-readable amount (e.g. '0.5')",
        },
        "token": {
            "type": "string",
            "description": (
                "ERC-20 contract address; omit for native ETH/gas token. "
                "Not yet implemented at this milestone."
            ),
        },
        "chain_id": {
            "type": "integer",
            "description": (
                "Override the connected wallet's chain. Defaults to the "
                "wallet's current chain when omitted."
            ),
        },
        "await_receipt": {
            "type": "boolean",
            "description": (
                "When true (default), block until the tx is mined and "
                "report success/revert. When false, return immediately "
                "after broadcast with the tx hash."
            ),
        },
        "value_wei": {
            "type": "string",
            "description": (
                "Optional explicit wei value. When set, the policy "
                "evaluator uses it for quantitative gates."
            ),
        },
        "policyConfirmationNonce": {
            "type": "string",
            "description": "Set when retrying after a POLICY HOLD response",
        },
    },
    "required": ["action", "to", "amount"],
}


@write_tool(
    name="transfer",
    toolset="clawmes-wallet",
    description=(
        "Send native ETH (or the chain's gas token) to a recipient. "
        "Returns gas estimate when action='estimate'; submits the "
        "transaction and (by default) waits for the receipt when "
        "action='send'. Requires a connected wallet (/connect, "
        "/connect_bankr, or /connect_local). ERC-20 transfers are not "
        "yet implemented."
    ),
    schema=_SCHEMA,
    emoji="\U0001f4b8",
)
def transfer(args: dict[str, Any], **kwargs: Any) -> str:
    state = get_wallet_state()
    if not state.connected:
        return error_result(
            "No wallet connected. Run /connect (WalletConnect), "
            "/connect_bankr (custodial), or /connect_local (local key) "
            "first.",
            code="wallet_not_connected",
        )

    action = read_str(args, "action", required=True)
    if action == "send":
        return _handle_send(args, state)
    if action == "estimate":
        return _handle_estimate(args, state)
    return error_result(f"Unknown action: {action!r}", code="invalid_action")


def _handle_estimate(args: dict[str, Any], state: Any) -> str:
    token = read_str(args, "token")
    if token:
        return error_result(
            "ERC-20 estimate is not yet implemented; native only at this milestone.",
            code="not_implemented",
        )

    to_input = read_str(args, "to", required=True)
    amount = read_str(args, "amount", required=True)
    target_chain_id = _target_chain_id(args, state)
    chain = _lookup_chain(target_chain_id)
    if chain is None:
        return error_result(
            f"Unknown chain id {target_chain_id} — no native decimals known.",
            code="unsupported_chain",
        )

    resolved = _resolve_recipient(to_input)
    if isinstance(resolved, str):
        # An error result was returned in place of a resolved address
        return resolved
    to_addr, ens_name = resolved

    try:
        value_wei = to_base_units(amount, chain.native_decimals)
    except (ValueError, ArithmeticError) as exc:
        return error_result(f"Bad amount {amount!r}: {exc}", code="param_error")

    gas = 21000  # native transfer is always 21000 on EVM
    details: dict[str, Any] = {
        "chain_id": chain.chain_id,
        "chain": chain.name,
        "to": to_addr,
        "amount": amount,
        "value_wei": str(value_wei),
        "estimated_gas": gas,
        "token": "native",
    }
    if ens_name is not None:
        details["ens_name"] = ens_name
        details["resolved_address"] = to_addr
    summary_target = f"{ens_name} ({to_addr})" if ens_name else to_addr
    return json_result(
        details,
        summary=(
            f"Native {chain.native_symbol} transfer to {summary_target} on {chain.name}\n"
            f"  Amount:        {amount} {chain.native_symbol} ({value_wei} wei)\n"
            f"  Estimated gas: {gas}"
        ),
    )


def _handle_send(args: dict[str, Any], state: Any) -> str:
    from clawmes.services.rpc import RpcError, get_rpc_service
    from clawmes.services.wallet import get_wallet_service

    token = read_str(args, "token")
    if token:
        return error_result(
            "ERC-20 send is not yet implemented; native only at this milestone.",
            code="not_implemented",
        )

    to_input = read_str(args, "to", required=True)
    amount = read_str(args, "amount", required=True)
    await_receipt = read_bool(args, "await_receipt", default=True)
    target_chain_id = _target_chain_id(args, state)
    chain = _lookup_chain(target_chain_id)
    if chain is None:
        return error_result(
            f"Unknown chain id {target_chain_id} — no native decimals known.",
            code="unsupported_chain",
        )

    resolved = _resolve_recipient(to_input)
    if isinstance(resolved, str):
        return resolved
    to_addr, ens_name = resolved

    try:
        value_wei = to_base_units(amount, chain.native_decimals)
    except (ValueError, ArithmeticError) as exc:
        return error_result(f"Bad amount {amount!r}: {exc}", code="param_error")

    svc = get_wallet_service()
    mode = svc.active_mode
    if mode is None:
        # Defensive: state.connected was True but no active_mode means the
        # service got into an inconsistent state (e.g. mode replaced under
        # us between the state read and here). Treat as not-connected.
        return error_result(
            "Wallet state is connected but no active mode is set; reconnect via /connect.",
            code="wallet_not_connected",
        )

    try:
        tx_hash = mode.send_transaction(
            to=to_addr,
            value=value_wei,
            chain_id=chain.chain_id,
        )
    except Exception as exc:  # noqa: BLE001 — surface any signing/RPC error
        return error_result(f"Transaction failed: {exc}", code="send_failed")

    explorer_url = f"{chain.block_explorer_url}/tx/{tx_hash}"
    base_details: dict[str, Any] = {
        "tx_hash": tx_hash,
        "explorer_url": explorer_url,
        "chain_id": chain.chain_id,
        "chain": chain.name,
        "to": to_addr,
        "amount": amount,
        "value_wei": str(value_wei),
        "token": "native",
        "status": "pending",
    }
    if ens_name is not None:
        base_details["ens_name"] = ens_name
        base_details["resolved_address"] = to_addr

    if not await_receipt:
        return json_result(
            base_details,
            summary=(
                f"Submitted: {tx_hash}\n"
                f"View: {explorer_url}\n"
                f"(receipt polling skipped; await_receipt=false)"
            ),
        )

    rpc = get_rpc_service()
    try:
        receipt = rpc.wait_for_receipt(
            tx_hash,
            chain.chain_id,
            timeout=120.0,
            poll_interval=2.0,
        )
    except RpcError as exc:
        # Tx submitted but the receipt didn't arrive in time. Don't mark
        # this as an error tool result — the tx may still mine. Surface
        # as a "pending" result so the LLM can tell the user to check
        # the explorer in a few minutes.
        return json_result(
            base_details,
            summary=(
                f"Submitted: {tx_hash}\n"
                f"View: {explorer_url}\n"
                f"Receipt not seen within timeout: {exc.message}"
            ),
        )

    success, block_num, gas_used = _summarize_receipt(receipt)
    base_details.update(
        {
            "status": "success" if success else "reverted",
            "block_number": block_num,
            "gas_used": gas_used,
        }
    )

    if success:
        summary = (
            f"Confirmed in block {block_num}: {tx_hash}\nGas used: {gas_used}\nView: {explorer_url}"
        )
    else:
        summary = (
            f"Reverted on chain: {tx_hash}\n"
            f"Block: {block_num}, gas used: {gas_used}\n"
            f"View: {explorer_url}"
        )
    return json_result(base_details, summary=summary)


def _resolve_recipient(to_input: str) -> tuple[str, str | None] | str:
    """Validate / resolve a transfer recipient.

    Returns:
        A ``(checksummed_address, ens_name_or_None)`` tuple on success.
        A pre-rendered error result string on failure (the caller
        bubbles this back to the LLM unchanged).

    The checksum is best-effort — eth_utils is already in the wallet
    dep tree, but if checksumming raises for any reason we return the
    lowercase form so the tx still goes through.
    """
    if not to_input:
        return error_result("Missing recipient address", code="param_error")

    # Already a hex address — light validation only.
    if to_input.startswith(("0x", "0X")):
        if len(to_input) != 42:
            return error_result(
                f"Invalid address length: {to_input!r} (expected 42 chars)",
                code="param_error",
            )
        try:
            from eth_utils import to_checksum_address

            return to_checksum_address(to_input), None
        except Exception:  # noqa: BLE001 — eth_utils raises on bad hex
            return error_result(f"Invalid address: {to_input!r}", code="param_error")

    # Looks like an ENS name — resolve via mainnet.
    if is_ens_name(to_input):
        try:
            resolved = resolve_ens(to_input)
        except EnsError as exc:
            return error_result(
                f"Could not resolve {to_input!r}: {exc.message}",
                code=f"ens_{exc.code}",
            )
        return resolved, to_input

    return error_result(
        f"Recipient {to_input!r} is neither a 0x address nor an ENS name.",
        code="param_error",
    )


def _target_chain_id(args: dict[str, Any], state: Any) -> int:
    raw = args.get("chain_id")
    if raw is not None:
        try:
            return int(raw)
        except (TypeError, ValueError):
            pass
    return int(state.chain_id) if state.chain_id is not None else 8453


def _lookup_chain(chain_id: int):
    try:
        return get_chain(chain_id)
    except KeyError:
        return None


def _summarize_receipt(receipt: dict[str, Any]) -> tuple[bool, int, int]:
    """Pull (success, block_number, gas_used) out of a JSON-RPC receipt.

    Receipt fields can be hex strings or ints depending on the RPC; we
    normalize both. Pre-Byzantium receipts use ``root`` instead of
    ``status``; we treat root-style as "success" since reverts in those
    eras manifested as exceptions, not failed receipts.
    """
    status_raw = receipt.get("status")
    if status_raw is None:
        # Pre-Byzantium: presence of `root` ≈ success. We don't have to
        # be strictly correct here since modern chains all use status.
        success = True
    else:
        success = _hex_or_int(status_raw) == 1
    block_num = _hex_or_int(receipt.get("blockNumber") or 0)
    gas_used = _hex_or_int(receipt.get("gasUsed") or 0)
    return success, block_num, gas_used


def _hex_or_int(value: Any) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value, 16) if value.startswith(("0x", "0X")) else int(value, 10)
    return 0


def register(ctx) -> None:
    """Wire ``transfer`` into Hermes."""
    register_with_ctx(ctx, transfer)
