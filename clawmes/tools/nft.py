"""``nft`` — NFT operations via Reservoir API + ERC-721 calldata.

Six actions:

  * ``mint``      — call mint() on an ERC-721 contract. Requires
    explicit ``contract`` address; mint signature varies per project
    so this is a thin pass-through with optional ``calldata`` override.
  * ``transfer``  — ERC-721 ``safeTransferFrom(from, to, tokenId)``.
  * ``burn``      — ERC-721 ``transferFrom(owner, 0xdEaD..., tokenId)``
    (most NFTs don't expose a real burn() function; sending to dead
    is the conventional alternative).
  * ``info``      — Reservoir token detail: collection, owner,
    last sale, top bid, attributes.
  * ``floor``     — Reservoir collection floor price + 24h volume.
  * ``holdings``  — Reservoir tokens-by-owner: list every NFT the
    wallet owns on a given chain.

Reservoir is the canonical NFT data API — used by Coinbase Wallet,
Rabby, Phantom, and most NFT marketplaces. ``RESERVOIR_API_KEY`` is
optional (free tier ~120 req/min); production traffic should obtain
a key.
"""

from __future__ import annotations

import os
from typing import Any

from clawmes.lib.abi import encode_address, encode_uint
from clawmes.lib.http import http_get
from clawmes.lib.logger import logger_for
from clawmes.lib.params import read_int, read_str
from clawmes.lib.tool_result import error_result, json_result
from clawmes.services.wallet import get_wallet_state
from clawmes.tools.registry import register_with_ctx, write_tool

_log = logger_for("tools.nft")

# Per-chain Reservoir API base URLs. Same API contract; chain
# selection is by hostname.
_RESERVOIR_BASES: dict[int, str] = {
    1: "https://api.reservoir.tools",
    8453: "https://api-base.reservoir.tools",
    42161: "https://api-arbitrum.reservoir.tools",
    10: "https://api-optimism.reservoir.tools",
    137: "https://api-polygon.reservoir.tools",
}

# ERC-721 selectors. Pinned constants.
SELECTOR_SAFE_TRANSFER = "0x42842e0e"  # safeTransferFrom(address,address,uint256)
SELECTOR_TRANSFER_FROM = "0x23b872dd"  # transferFrom(address,address,uint256)

# Burn convention: send to 0xdead. Most NFT projects don't implement
# a real burn() function; transferFrom(owner, 0xdead, tokenId) is the
# OpenZeppelin-canonical alternative.
_DEAD_ADDRESS = "0x000000000000000000000000000000000000dEaD"

_NFT_GAS_DEFAULT = 200_000

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["mint", "transfer", "burn", "info", "floor", "holdings"],
        },
        "contract": {
            "type": "string",
            "description": "ERC-721 contract address. Required for write actions.",
        },
        "token_id": {
            "type": "string",
            "description": "Token ID. Required for transfer/burn/info.",
        },
        "to": {
            "type": "string",
            "description": "Recipient. Required for transfer.",
        },
        "calldata": {
            "type": "string",
            "description": (
                "Custom calldata for mint (since mint signatures vary). "
                "Defaults to empty (only useful when the contract has "
                "a no-arg public mint())."
            ),
        },
        "value_wei": {
            "type": "string",
            "description": "ETH value to send (for paid mints). Default 0.",
        },
        "owner": {
            "type": "string",
            "description": (
                "Wallet to query for holdings. Defaults to the connected wallet's address."
            ),
        },
        "chain_id": {
            "type": "integer",
            "description": "Chain id. Defaults to wallet's chain.",
        },
        "limit": {
            "type": "integer",
            "description": "Max items for holdings (default 50, max 200).",
        },
        "policyConfirmationNonce": {
            "type": "string",
            "description": "Set when retrying after POLICY HOLD.",
        },
    },
    "required": ["action"],
}


@write_tool(
    name="nft",
    toolset="clawmes-defi",
    description=(
        "NFT operations: mint / transfer / burn ERC-721s + read "
        "collection/token data via Reservoir. Read actions don't "
        "require a connected wallet; write actions require one."
    ),
    schema=_SCHEMA,
    emoji="\U0001f5bc\ufe0f",
)
def nft(args: dict[str, Any], **kwargs: Any) -> str:
    action = read_str(args, "action", required=True)
    state = get_wallet_state()
    chain_id = _resolve_chain_id(args, state)

    if action in ("info", "floor", "holdings"):
        return _handle_read(action, args, state, chain_id)

    if not state.connected or not state.address:
        return error_result(
            "No wallet connected. Run /connect first.",
            code="wallet_not_connected",
        )

    if action == "mint":
        return _handle_mint(args, state, chain_id)
    if action == "transfer":
        return _handle_transfer(args, state, chain_id)
    return _handle_burn(args, state, chain_id)


def _handle_mint(args, state, chain_id: int) -> str:
    contract = _validate_address(read_str(args, "contract", required=True))
    if isinstance(contract, str) and contract.startswith("__error__"):
        return contract[len("__error__") :]

    calldata = read_str(args, "calldata") or "0x1249c58b"  # mint() no-arg default
    value_raw = args.get("value_wei") or "0"
    try:
        value = int(value_raw)
    except (TypeError, ValueError):
        return error_result(f"Bad value_wei {value_raw!r}", code="param_error")

    return _send(
        state=state,
        to=contract,
        value=value,
        data=calldata,
        chain_id=chain_id,
        action="mint",
    )


def _handle_transfer(args, state, chain_id: int) -> str:
    contract = _validate_address(read_str(args, "contract", required=True))
    if isinstance(contract, str) and contract.startswith("__error__"):
        return contract[len("__error__") :]
    to = _validate_address(read_str(args, "to", required=True))
    if isinstance(to, str) and to.startswith("__error__"):
        return to[len("__error__") :]
    token_id_raw = read_str(args, "token_id", required=True)
    try:
        token_id = int(token_id_raw)
    except (TypeError, ValueError):
        return error_result(f"Bad token_id {token_id_raw!r}", code="param_error")

    calldata = (
        SELECTOR_SAFE_TRANSFER
        + encode_address(state.address)
        + encode_address(to)
        + encode_uint(token_id)
    )
    return _send(
        state=state,
        to=contract,
        value=0,
        data=calldata,
        chain_id=chain_id,
        action="transfer",
    )


def _handle_burn(args, state, chain_id: int) -> str:
    contract = _validate_address(read_str(args, "contract", required=True))
    if isinstance(contract, str) and contract.startswith("__error__"):
        return contract[len("__error__") :]
    token_id_raw = read_str(args, "token_id", required=True)
    try:
        token_id = int(token_id_raw)
    except (TypeError, ValueError):
        return error_result(f"Bad token_id {token_id_raw!r}", code="param_error")

    calldata = (
        SELECTOR_TRANSFER_FROM
        + encode_address(state.address)
        + encode_address(_DEAD_ADDRESS)
        + encode_uint(token_id)
    )
    return _send(
        state=state,
        to=contract,
        value=0,
        data=calldata,
        chain_id=chain_id,
        action="burn",
    )


def _send(*, state, to, value, data, chain_id, action) -> str:
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
            to=to,
            value=value,
            data=data,
            gas=_NFT_GAS_DEFAULT,
            chain_id=chain_id,
        )
    except Exception as exc:  # noqa: BLE001
        return error_result(f"{action} failed: {exc}", code="send_failed")

    result = {
        "tx_hash": tx_hash,
        "action": action,
        "contract": to,
        "chain_id": chain_id,
    }
    # Desktop UI: clickable explorer link for the NFT tx. (DexScreener/Clanker
    # don't apply to ERC-721s, so token-market links are intentionally omitted.)
    from clawmes.lib.ui_artifacts import enrich_tx_links

    enrich_tx_links(result, tx_hash=tx_hash, chain_id=chain_id)
    return json_result(result, summary=f"NFT {action}: {tx_hash}")


def _handle_read(action: str, args, state, chain_id: int) -> str:
    base = _RESERVOIR_BASES.get(chain_id)
    if base is None:
        return error_result(
            f"Reservoir doesn't support chain {chain_id}",
            code="unsupported_chain",
        )

    api_key = os.environ.get("RESERVOIR_API_KEY")
    headers = {"accept": "application/json"}
    if api_key:
        headers["x-api-key"] = api_key

    if action == "info":
        return _reservoir_token_info(base, headers, args)
    if action == "floor":
        return _reservoir_floor(base, headers, args)
    return _reservoir_holdings(base, headers, args, state)


def _reservoir_token_info(base, headers, args) -> str:
    contract = read_str(args, "contract", required=True)
    token_id = read_str(args, "token_id", required=True)
    try:
        data = http_get(
            f"{base}/tokens/v6",
            params={"tokens": f"{contract}:{token_id}"},
            headers=headers,
            timeout=15.0,
        )
    except Exception as exc:  # noqa: BLE001
        return error_result(f"Reservoir failed: {exc}", code="api_error")
    if not isinstance(data, dict):
        return error_result("Reservoir non-dict response", code="api_error")
    tokens = data.get("tokens") or []
    if not tokens:
        return error_result(f"Token {contract}:{token_id} not found", code="not_found")
    return json_result(
        {"token": tokens[0]},
        summary=f"NFT info for {contract}:{token_id}",
    )


def _reservoir_floor(base, headers, args) -> str:
    contract = read_str(args, "contract", required=True)
    try:
        data = http_get(
            f"{base}/collections/v7",
            params={"id": contract},
            headers=headers,
            timeout=15.0,
        )
    except Exception as exc:  # noqa: BLE001
        return error_result(f"Reservoir failed: {exc}", code="api_error")
    if not isinstance(data, dict):
        return error_result("Reservoir non-dict response", code="api_error")
    collections = data.get("collections") or []
    if not collections:
        return error_result(f"Collection {contract} not found", code="not_found")
    c = collections[0]
    floor = (c.get("floorAsk") or {}).get("price") or {}
    floor_eth = (floor.get("amount") or {}).get("native")
    return json_result(
        {
            "name": c.get("name"),
            "address": c.get("id"),
            "floor_price_eth": floor_eth,
            "volume_24h_eth": (c.get("volume") or {}).get("1day"),
            "owners": c.get("ownerCount"),
            "supply": c.get("tokenCount"),
        },
        summary=(
            f"{c.get('name', contract)}: floor {floor_eth} ETH, "
            f"24h vol {(c.get('volume') or {}).get('1day', 0)} ETH"
        ),
    )


def _reservoir_holdings(base, headers, args, state) -> str:
    owner = read_str(args, "owner") or state.address
    if not owner:
        return error_result(
            "owner address required (or connect a wallet)",
            code="param_error",
        )
    limit = read_int(args, "limit") or 50
    try:
        data = http_get(
            f"{base}/users/{owner}/tokens/v9",
            params={"limit": str(min(limit, 200))},
            headers=headers,
            timeout=15.0,
        )
    except Exception as exc:  # noqa: BLE001
        return error_result(f"Reservoir failed: {exc}", code="api_error")
    if not isinstance(data, dict):
        return error_result("Reservoir non-dict response", code="api_error")
    tokens = data.get("tokens") or []
    return json_result(
        {"owner": owner, "count": len(tokens), "tokens": tokens},
        summary=f"{owner} holds {len(tokens)} NFT(s)",
    )


def _validate_address(value: str | None) -> str:
    if not value or not value.startswith(("0x", "0X")) or len(value) != 42:
        return "__error__" + error_result(f"Invalid address: {value!r}", code="param_error")
    return value


def _resolve_chain_id(args: dict[str, Any], state) -> int:
    explicit = read_int(args, "chain_id")
    if explicit is not None:
        return explicit
    return int(state.chain_id) if state.chain_id is not None else 1


def register(ctx) -> None:
    """Wire ``nft`` into Hermes."""
    register_with_ctx(ctx, nft)
