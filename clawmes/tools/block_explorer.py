"""``block_explorer`` — read transaction and address details via Etherscan-family APIs.

Read-only tool. Two actions in v0.1.0:

  * ``tx <hash>``     — fetch transaction status + receipt status
  * ``address <addr>`` — fetch native balance + tx count for an address

Reads from the explorer matching the requested chain (Basescan, Etherscan,
Arbiscan, Optimistic Etherscan, Polygonscan). Falls back to free-tier
rate limits when no API key is set; users can configure ``BASESCAN_API_KEY``,
``ETHERSCAN_API_KEY``, etc. via env vars.
"""

from __future__ import annotations

from typing import Any

from clawmes.lib.addr import is_hex_address
from clawmes.lib.chains import get_chain
from clawmes.lib.decimals import format_human
from clawmes.lib.params import read_enum, read_str
from clawmes.lib.tool_result import error_result, json_result
from clawmes.services.explorer import ExplorerError, get_explorer_service
from clawmes.tools.registry import read_tool, register_with_ctx

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["tx", "address"],
            "description": (
                "tx = look up a transaction by hash; address = balance + tx count for an address"
            ),
        },
        "value": {
            "type": "string",
            "description": "Tx hash (for action='tx') or address (for action='address')",
        },
        "chain": {
            "type": "string",
            "description": "Chain id or short name (default: base)",
        },
    },
    "required": ["action", "value"],
}


@read_tool(
    name="block_explorer",
    toolset="clawmes-intel",
    description=(
        "Look up transactions and addresses on block explorers (Basescan, "
        "Etherscan, Arbiscan, Optimistic Etherscan, Polygonscan). "
        "Read-only. Pass action='tx' with a tx hash, or action='address' "
        "with an address. Specify chain by id (1, 8453, ...) or short "
        "name (ethereum, base, ...)."
    ),
    schema=_SCHEMA,
    emoji="🔍",
)
def block_explorer(args: dict[str, Any], **kwargs: Any) -> str:
    action = read_enum(args, "action", ["tx", "address"], required=True)
    value = read_str(args, "value", required=True)
    assert action is not None and value is not None

    chain_arg = read_str(args, "chain") or "base"
    try:
        chain = get_chain(int(chain_arg) if chain_arg.isdigit() else chain_arg)
    except KeyError:
        return error_result(f"Unknown chain: {chain_arg!r}", code="invalid_chain")

    svc = get_explorer_service()
    if not svc.supports_chain(chain.chain_id):
        return error_result(
            f"No block explorer configured for {chain.name}",
            code="explorer_unconfigured",
        )

    if action == "tx":
        return _handle_tx(value, chain, svc)
    return _handle_address(value, chain, svc)


def _handle_tx(tx_hash: str, chain, svc) -> str:
    if not tx_hash.startswith("0x") or len(tx_hash) != 66:
        return error_result(
            f"Not a valid tx hash: {tx_hash!r}. Must be 0x + 64 hex chars.",
            code="invalid_tx_hash",
        )

    try:
        receipt_status = svc.get_tx_receipt_status(tx_hash, chain.chain_id)
        tx_status = svc.get_tx_status(tx_hash, chain.chain_id)
    except ExplorerError as exc:
        return error_result(f"Explorer error: {exc}", code="explorer_error")

    # Etherscan returns {"status": "1"} for success, "0" for failure.
    receipt_ok = isinstance(receipt_status, dict) and receipt_status.get("status") == "1"
    error_descr = ""
    if isinstance(tx_status, dict):
        error_descr = tx_status.get("errDescription") or ""

    summary_lines = [
        f"Transaction {tx_hash}",
        f"  Chain:       {chain.name}",
        f"  Receipt:     {'success' if receipt_ok else 'failed'}",
    ]
    if error_descr:
        summary_lines.append(f"  Error:       {error_descr}")
    explorer_url = f"{chain.block_explorer_url}/tx/{tx_hash}"
    summary_lines.append(f"  Explorer:    {explorer_url}")

    return json_result(
        {
            "tx_hash": tx_hash,
            "chain_id": chain.chain_id,
            "receipt_status": "success" if receipt_ok else "failed",
            "error_description": error_descr,
            "explorer_url": explorer_url,
        },
        summary="\n".join(summary_lines),
    )


def _handle_address(address: str, chain, svc) -> str:
    if not is_hex_address(address):
        return error_result(
            f"Not a valid hex address: {address!r}. ENS resolution not "
            "yet supported by block_explorer.",
            code="invalid_address",
        )

    try:
        balance_wei = svc.get_address_balance(address, chain.chain_id)
        tx_count = svc.get_address_tx_count(address, chain.chain_id)
    except ExplorerError as exc:
        return error_result(f"Explorer error: {exc}", code="explorer_error")

    pretty_balance = format_human(balance_wei, chain.native_decimals, chain.native_symbol)
    explorer_url = f"{chain.block_explorer_url}/address/{address}"

    return json_result(
        {
            "address": address,
            "chain_id": chain.chain_id,
            "native_balance_wei": str(balance_wei),
            "native_balance": pretty_balance,
            "tx_count": tx_count,
            "explorer_url": explorer_url,
        },
        summary=(
            f"Address {address}\n"
            f"  Chain:       {chain.name}\n"
            f"  Balance:     {pretty_balance}\n"
            f"  Tx count:    {tx_count}\n"
            f"  Explorer:    {explorer_url}"
        ),
    )


def register(ctx) -> None:
    register_with_ctx(ctx, block_explorer)
