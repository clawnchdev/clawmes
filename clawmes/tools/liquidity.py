"""``liquidity`` — Uniswap V3 / V4 LP position management.

Five actions:

  * ``provide``  — open a new concentrated-liquidity position. Requires
    pre-encoded calldata (V3's mint signature is complex and varies
    by tick spacing / pair).
  * ``withdraw`` — remove liquidity from a position (NFT id-based).
  * ``compound`` — collect accrued fees and re-deposit.
  * ``info``     — read position metadata via NFT positionManager.
  * ``pools``    — list pools by token pair via Uniswap subgraph.

Uniswap V3's NonfungiblePositionManager handles all V3 positions.
Each position is an ERC-721 NFT. This tool exposes the management
interface; calldata for ``provide`` is complex enough that the LLM
should use the Uniswap UI for first-time mints, then use this tool
to manage the resulting positions.
"""

from __future__ import annotations

from typing import Any

from clawmes.lib.abi import decode_uint, encode_address, encode_uint
from clawmes.lib.http import http_post
from clawmes.lib.logger import logger_for
from clawmes.lib.params import read_int, read_str
from clawmes.lib.tool_result import error_result, json_result
from clawmes.services.wallet import get_wallet_state
from clawmes.tools.registry import register_with_ctx, write_tool

_log = logger_for("tools.liquidity")

# Uniswap V3 NonfungiblePositionManager. Same address on every chain
# Uniswap V3 deploys to (deterministic CREATE2).
V3_POSITION_MANAGER = "0xC36442b4a4522E871399CD717aBDD847Ab11FE88"

# positions(uint256) selector
SELECTOR_POSITIONS = "0x99fbab88"

# decreaseLiquidity((uint256,uint128,uint256,uint256,uint256))
SELECTOR_DECREASE_LIQUIDITY = "0x0c49ccbe"

# collect((uint256,address,uint128,uint128))
SELECTOR_COLLECT = "0xfc6f7865"

# Uniswap V3 subgraph (mainnet — other chains have their own URLs)
_SUBGRAPH_URLS: dict[int, str] = {
    1: "https://api.thegraph.com/subgraphs/name/uniswap/uniswap-v3",
    42161: "https://api.thegraph.com/subgraphs/name/ianlapham/arbitrum-minimal",
    10: "https://api.thegraph.com/subgraphs/name/ianlapham/optimism-post-regenesis",
    137: "https://api.thegraph.com/subgraphs/name/ianlapham/uniswap-v3-polygon",
    8453: "https://api.thegraph.com/subgraphs/name/ianlapham/uniswap-base",
}

_LIQUIDITY_GAS_DEFAULT = 400_000

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["provide", "withdraw", "compound", "info", "pools"],
        },
        "token_id": {"type": "string", "description": "NFT id (write actions)."},
        "calldata": {
            "type": "string",
            "description": "Override calldata for provide (complex mint).",
        },
        "liquidity": {
            "type": "string",
            "description": "Liquidity to remove (withdraw).",
        },
        "token0": {"type": "string", "description": "Pool token0 (pools query)."},
        "token1": {"type": "string", "description": "Pool token1 (pools query)."},
        "chain_id": {"type": "integer"},
        "policyConfirmationNonce": {"type": "string"},
    },
    "required": ["action"],
}


@write_tool(
    name="liquidity",
    toolset="clawmes-defi",
    description=(
        "Uniswap V3 LP position management. provide opens a position "
        "(complex; calldata override required); withdraw / compound "
        "act on existing NFT-backed positions; info reads position "
        "metadata; pools lists pools via subgraph."
    ),
    schema=_SCHEMA,
    emoji="\U0001f30a",
)
def liquidity(args: dict[str, Any], **kwargs: Any) -> str:
    action = read_str(args, "action", required=True)
    state = get_wallet_state()
    chain_id = read_int(args, "chain_id") or (
        int(state.chain_id) if state.chain_id is not None else 1
    )

    if action == "pools":
        return _handle_pools(args, chain_id)

    if action == "info":
        return _handle_info(args, chain_id)

    # write actions need a wallet
    if not state.connected or not state.address:
        return error_result("No wallet connected.", code="wallet_not_connected")

    if action == "provide":
        return _handle_provide(args, state, chain_id)
    if action == "withdraw":
        return _handle_withdraw(args, state, chain_id)
    return _handle_compound(args, state, chain_id)


def _handle_provide(args, state, chain_id: int) -> str:
    calldata = read_str(args, "calldata")
    if not calldata:
        return error_result(
            "provide requires explicit 'calldata' — Uniswap V3 mint() "
            "is complex (tick range, fee tier, slippage). Use the "
            "Uniswap UI to build the calldata or compute it externally.",
            code="not_implemented",
        )
    return _send(state, calldata, chain_id, "provide")


def _handle_withdraw(args, state, chain_id: int) -> str:
    token_id_raw = read_str(args, "token_id", required=True)
    liquidity_raw = read_str(args, "liquidity", required=True)
    try:
        token_id = int(token_id_raw)
        liquidity = int(liquidity_raw)
    except (TypeError, ValueError):
        return error_result("token_id / liquidity must be integers", code="param_error")

    # decreaseLiquidity tuple: (tokenId, liquidity, amount0Min, amount1Min, deadline)
    import time

    deadline = int(time.time()) + 1800  # 30min
    calldata = (
        SELECTOR_DECREASE_LIQUIDITY
        + encode_uint(0x20)  # offset to tuple
        + encode_uint(token_id)
        + encode_uint(liquidity, bits=128)
        + encode_uint(0)  # amount0Min
        + encode_uint(0)  # amount1Min
        + encode_uint(deadline)
    )
    return _send(state, calldata, chain_id, "withdraw")


def _handle_compound(args, state, chain_id: int) -> str:
    """Compound = collect fees back into the position. The collect() call
    on the position manager pulls the accrued fees as ERC-20 to the
    caller; re-providing them as additional liquidity needs a second
    increaseLiquidity call. This tool issues just the collect — the LLM
    can chain a follow-up provide for the re-deposit step.
    """
    token_id_raw = read_str(args, "token_id", required=True)
    try:
        token_id = int(token_id_raw)
    except (TypeError, ValueError):
        return error_result("token_id must be an integer", code="param_error")

    # collect tuple: (tokenId, recipient, amount0Max, amount1Max)
    calldata = (
        SELECTOR_COLLECT
        + encode_uint(0x20)
        + encode_uint(token_id)
        + encode_address(state.address)
        + encode_uint((1 << 128) - 1, bits=128)  # amount0Max = uint128 max
        + encode_uint((1 << 128) - 1, bits=128)  # amount1Max = uint128 max
    )
    return _send(state, calldata, chain_id, "compound")


def _handle_info(args, chain_id: int) -> str:
    from clawmes.services.rpc import RpcError, get_rpc_service

    token_id_raw = read_str(args, "token_id", required=True)
    try:
        token_id = int(token_id_raw)
    except (TypeError, ValueError):
        return error_result("token_id must be an integer", code="param_error")

    calldata = SELECTOR_POSITIONS + encode_uint(token_id)
    rpc = get_rpc_service()
    try:
        raw = rpc.eth_call(to=V3_POSITION_MANAGER, data=calldata, chain_id=chain_id)
    except RpcError as exc:
        return error_result(f"Position info failed: {exc.message}", code="rpc_error")

    body = raw.removeprefix("0x")
    if len(body) < 64 * 12:
        return error_result("Malformed position response", code="rpc_error")
    # positions() returns 12 fields; we extract the meaningful subset.
    try:
        token0 = "0x" + body[64 * 2 + 24 : 64 * 3]
        token1 = "0x" + body[64 * 3 + 24 : 64 * 4]
        fee = decode_uint("0x" + body[64 * 4 : 64 * 5])
        tick_lower = decode_uint("0x" + body[64 * 5 : 64 * 6])
        tick_upper = decode_uint("0x" + body[64 * 6 : 64 * 7])
        liquidity = decode_uint("0x" + body[64 * 7 : 64 * 8])
    except (ValueError, IndexError):
        return error_result("Could not decode position response", code="rpc_error")

    return json_result(
        {
            "token_id": token_id,
            "token0": token0,
            "token1": token1,
            "fee_tier": fee,
            "tick_lower": tick_lower,
            "tick_upper": tick_upper,
            "liquidity": str(liquidity),
        },
        summary=(
            f"Position {token_id}: {token0}/{token1} fee={fee / 10000:.2f}% liquidity={liquidity}"
        ),
    )


def _handle_pools(args, chain_id: int) -> str:
    subgraph = _SUBGRAPH_URLS.get(chain_id)
    if subgraph is None:
        return error_result(
            f"Uniswap V3 subgraph not configured for chain {chain_id}",
            code="unsupported_chain",
        )
    token0 = read_str(args, "token0")
    token1 = read_str(args, "token1")
    if not token0 or not token1:
        return error_result("pools requires token0 + token1", code="param_error")

    query = """
    query Pools($t0: String!, $t1: String!) {
      pools(where: { token0: $t0, token1: $t1 }, first: 10, orderBy: feeTier) {
        id
        feeTier
        liquidity
        totalValueLockedUSD
      }
    }
    """
    try:
        resp = http_post(
            subgraph,
            json={
                "query": query,
                "variables": {
                    "t0": token0.lower(),
                    "t1": token1.lower(),
                },
            },
            timeout=15.0,
        )
    except Exception as exc:  # noqa: BLE001
        return error_result(f"Subgraph request failed: {exc}", code="api_error")
    if not isinstance(resp, dict):
        return error_result("Subgraph non-dict response", code="api_error")
    pools = (resp.get("data") or {}).get("pools") or []
    return json_result(
        {"token0": token0, "token1": token1, "count": len(pools), "pools": pools},
        summary=f"{len(pools)} V3 pool(s) for {token0}/{token1}",
    )


def _send(state, calldata: str, chain_id: int, action: str) -> str:
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
            to=V3_POSITION_MANAGER,
            value=0,
            data=calldata,
            gas=_LIQUIDITY_GAS_DEFAULT,
            chain_id=chain_id,
        )
    except Exception as exc:  # noqa: BLE001
        return error_result(f"{action} failed: {exc}", code="send_failed")
    return json_result(
        {"tx_hash": tx_hash, "action": action},
        summary=f"liquidity {action}: {tx_hash}",
    )


def register(ctx) -> None:
    register_with_ctx(ctx, liquidity)
