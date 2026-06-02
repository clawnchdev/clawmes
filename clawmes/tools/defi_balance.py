"""``defi_balance`` — read native and ERC-20 token balances.

Read-only tool, no wallet required. Three actions:

  * ``native``   — single chain, native-token balance for an address
  * ``token``    — single chain, single ERC-20 contract balance
  * ``summary``  — single chain, native + a curated common-token list
                   (USDC, WETH, USDT, DAI), skipping tokens with zero
                   balance for compactness

All actions accept ``address`` and ``chain`` (chain id or short name).
Token contracts are validated as hex addresses; ENS resolution for
recipient/holder addresses is a follow-up commit (needs the ENS
service which depends on RPC, which now exists).
"""

from __future__ import annotations

from typing import Any

from clawmes.lib.abi import decode_uint, encode_balance_of
from clawmes.lib.addr import is_hex_address
from clawmes.lib.chains import get_chain
from clawmes.lib.decimals import format_human, from_base_units
from clawmes.lib.params import read_enum, read_str
from clawmes.lib.tool_result import error_result, json_result
from clawmes.services.rpc import RpcError, get_rpc_service
from clawmes.services.token_decimals import get_token_decimals_service
from clawmes.tools.registry import read_tool, register_with_ctx

# Common tokens to surface in the ``summary`` action. Lowercase
# addresses; mapped per chain. Anything not in this map is skipped
# from summary output (users can use ``token`` for one-offs).
_SUMMARY_TOKENS: dict[int, list[tuple[str, str]]] = {
    1: [
        ("USDC", "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"),
        ("USDT", "0xdac17f958d2ee523a2206206994597c13d831ec7"),
        ("DAI", "0x6b175474e89094c44da98b954eedeac495271d0f"),
        ("WETH", "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"),
    ],
    8453: [
        ("USDC", "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"),
        ("WETH", "0x4200000000000000000000000000000000000006"),
        ("DAI", "0x50c5725949a6f0c72e6c4a641f24049a917db0cb"),
    ],
    42161: [
        ("USDC", "0xaf88d065e77c8cc2239327c5edb3a432268e5831"),
        ("USDT", "0xfd086bc7cd5c481dcc9c85ebe478a1c0b69fcbb9"),
        ("WETH", "0x82af49447d8a07e3bd95bd0d56f35241523fbab1"),
    ],
    10: [
        ("USDC", "0x0b2c639c533813f4aa9d7837caf62653d097ff85"),
        ("WETH", "0x4200000000000000000000000000000000000006"),
    ],
}


_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["native", "token", "summary"],
            "description": (
                "native = native-token balance only; token = a single "
                "ERC-20; summary = native + common ERC-20s on the chain"
            ),
        },
        "address": {
            "type": "string",
            "description": "Holder address (0x-prefixed hex)",
        },
        "chain": {
            "type": "string",
            "description": "Chain id or short name (e.g. '8453' or 'base'). Default: base.",
        },
        "token": {
            "type": "string",
            "description": "ERC-20 contract address (required for action='token')",
        },
    },
    "required": ["action", "address"],
}


@read_tool(
    name="defi_balance",
    toolset="clawmes-trading",
    description=(
        "Read native and ERC-20 token balances. Read-only, no wallet "
        "required. Pass any holder address; you don't need to be the "
        "owner. Use action='summary' for a quick portfolio view across "
        "common tokens, 'native' for just ETH/native, or 'token' for "
        "a specific ERC-20 contract."
    ),
    schema=_SCHEMA,
    emoji="📊",
)
def defi_balance(args: dict[str, Any], **kwargs: Any) -> str:
    action = read_enum(args, "action", ["native", "token", "summary"], required=True)
    address = read_str(args, "address", required=True)
    assert action is not None and address is not None

    if not is_hex_address(address):
        return error_result(
            f"Not a valid hex address: {address!r}. ENS names are not "
            "yet supported by defi_balance — use a 0x-prefixed address.",
            code="invalid_address",
        )

    chain_arg = read_str(args, "chain") or "base"
    try:
        chain = get_chain(chain_arg if chain_arg.isdigit() is False else int(chain_arg))
    except KeyError:
        return error_result(f"Unknown chain: {chain_arg!r}", code="invalid_chain")

    if not get_rpc_service().has_endpoint(chain.chain_id):
        return error_result(
            f"No RPC endpoint configured for {chain.name}. "
            f"Set CLAWMES_RPC_{chain.chain_id} or clawmes.rpc.{chain.chain_id} "
            "in config.yaml.",
            code="rpc_unconfigured",
        )

    if action == "native":
        return _handle_native(address, chain)
    if action == "token":
        return _handle_token(args, address, chain)
    return _handle_summary(address, chain)


# --- handlers -------------------------------------------------------------


def _handle_native(address: str, chain) -> str:
    try:
        wei = get_rpc_service().get_balance(address, chain.chain_id)
    except RpcError as exc:
        return error_result(f"RPC error: {exc.message}", code="rpc_error")

    pretty = format_human(wei, chain.native_decimals, chain.native_symbol)
    return json_result(
        {
            "address": address,
            "chain_id": chain.chain_id,
            "chain": chain.short_name,
            "native_balance_wei": str(wei),
            "native_balance": pretty,
        },
        summary=f"{pretty} on {chain.name}",
    )


def _handle_token(args: dict[str, Any], address: str, chain) -> str:
    token = read_str(args, "token", required=True)
    assert token is not None
    if not is_hex_address(token):
        return error_result(
            f"Token contract is not a valid address: {token!r}",
            code="invalid_token",
        )

    rpc = get_rpc_service()
    try:
        raw = rpc.eth_call(
            to=token,
            data=encode_balance_of(address),
            chain_id=chain.chain_id,
        )
    except RpcError as exc:
        return error_result(f"RPC error: {exc.message}", code="rpc_error")

    base = decode_uint(raw)
    decimals = get_token_decimals_service().get(token, chain.chain_id)
    pretty = from_base_units(base, decimals)
    return json_result(
        {
            "address": address,
            "chain_id": chain.chain_id,
            "token": token,
            "balance_base_units": str(base),
            "decimals": decimals,
            "balance": pretty,
        },
        summary=f"{pretty} (token {token[:10]}…) on {chain.name}",
    )


def _handle_summary(address: str, chain) -> str:
    rpc = get_rpc_service()
    try:
        native_wei = rpc.get_balance(address, chain.chain_id)
    except RpcError as exc:
        return error_result(f"RPC error: {exc.message}", code="rpc_error")

    rows: list[tuple[str, int, int]] = [
        (chain.native_symbol, native_wei, chain.native_decimals),
    ]

    decimals_svc = get_token_decimals_service()
    for symbol, contract in _SUMMARY_TOKENS.get(chain.chain_id, []):
        try:
            raw = rpc.eth_call(
                to=contract,
                data=encode_balance_of(address),
                chain_id=chain.chain_id,
            )
        except RpcError:
            continue  # Skip unreachable tokens; surface what works.
        base = decode_uint(raw)
        if base == 0:
            continue
        decimals = decimals_svc.get(contract, chain.chain_id)
        rows.append((symbol, base, decimals))

    lines = [f"Balances for {address} on {chain.name}:"]
    payload: list[dict[str, Any]] = []
    for symbol, base, decimals in rows:
        pretty = from_base_units(base, decimals, precision=6)
        lines.append(f"  {symbol:<8}  {pretty}")
        payload.append(
            {
                "symbol": symbol,
                "base_units": str(base),
                "decimals": decimals,
                "human": pretty,
            }
        )
    if len(rows) == 1 and rows[0][1] == 0:
        lines.append("  (no balances)")

    result: dict[str, Any] = {
        "address": address,
        "chain_id": chain.chain_id,
        "chain": chain.short_name,
        "balances": payload,
    }
    # Desktop UI: render a portfolio card and surface its path at the envelope
    # top level (via json_result's ``preview=``) so the desktop chat tool-card
    # opens it in the preview pane. defi_balance is never on a scheduler path,
    # so this only fires on user/LLM-invoked balance summaries. UI enrichment
    # must never break the underlying read, so failures are swallowed.
    preview_path: str | None = None
    try:
        from clawmes.lib.ui_cards import portfolio_card, write_card

        holdings = [{"symbol": p["symbol"], "amount": p["human"]} for p in payload]
        card_html = portfolio_card(
            address=address, chain=chain.name, total_usd=None, holdings=holdings
        )
        preview_path = str(write_card(card_html, f"portfolio-{chain.short_name}"))
    except Exception:  # noqa: BLE001 — UI is best-effort, never fail the read
        preview_path = None

    return json_result(result, summary="\n".join(lines), preview=preview_path)


def register(ctx) -> None:
    register_with_ctx(ctx, defi_balance)
