---
name: liquidity
description: Manage Uniswap V3 LP positions — provide, withdraw, compound, info, list pools
metadata:
  hermes:
    tags: [crypto, defi, uniswap, lp, liquidity]
    category: clawmes
    requires_tools: [liquidity, clawnchconnect]
---

# Uniswap V3 Liquidity Positions

The `liquidity` tool manages concentrated-liquidity positions on
Uniswap V3. Positions are NFTs minted by the V3 NonfungiblePositionManager
(same address on every chain — `0xC36442b4...`).

## When to use

- User says: "show me Uniswap pools for ETH/USDC on Base"
- User says: "what's my LP position for token id 12345"
- User says: "withdraw my LP position 12345"
- User says: "collect fees from my Uniswap position"
- User says: "open a new ETH/USDC LP position"

## Read actions (no wallet required)

### `pools` — list V3 pools for a pair

```json
{"action": "pools", "token0": "0x...", "token1": "0x...", "chain_id": 1}
```

Queries the Uniswap subgraph. Returns the pools sorted by fee tier
with TVL + liquidity. `token0` / `token1` are address strings;
canonical ordering doesn't matter (subgraph normalizes).

### `info` — read position metadata by NFT id

```json
{"action": "info", "token_id": "12345"}
```

Returns: token0/token1, fee tier (in 1/10000ths — 3000 = 0.3%),
tick range (lower/upper), and current liquidity. Read-only via
`positions()` view on the position manager.

## Write actions (wallet required)

### `provide` — open a new position

The tool encodes V3 MintParams from structured args via eth_abi:

```json
{
  "action": "provide",
  "token0": "0x...",
  "token1": "0x...",
  "fee": 3000,
  "tick_lower": -1000,
  "tick_upper": 1000,
  "amount0_desired": "1000000",
  "amount1_desired": "1000000",
  "amount0_min": "990000",
  "amount1_min": "990000"
}
```

Required fields: `token0`, `token1`, `fee`, `tick_lower`, `tick_upper`,
`amount0_desired`, `amount1_desired`. Optional: `amount0_min` /
`amount1_min` (default 0 for slippage; production should set these).

Fee tiers (in 1/10000ths): `100` (0.01%), `500` (0.05%), `3000`
(0.3%), `10000` (1%). Tick alignment to the fee tier's tick spacing
is the caller's responsibility — bad ticks revert at execution.

Caller can still override with raw `calldata` if encoding outside
the tool:

```json
{"action": "provide", "calldata": "0x..."}
```

For first-time mints where tick selection is unfamiliar, the
Uniswap UI is recommended — then use this tool to manage the
resulting NFT.

### `withdraw` — remove liquidity

```json
{"action": "withdraw", "token_id": "12345", "liquidity": "1000000000000000000"}
```

Calls `decreaseLiquidity()` on the position manager. The `liquidity`
amount is the V3 internal liquidity unit (not human ETH amount).
30-minute deadline applied automatically.

### `compound` — collect accrued fees

```json
{"action": "compound", "token_id": "12345"}
```

Calls `collect()` on the position with uint128-max for both amount0
and amount1, pulling all accrued fees back to the user's wallet.
Re-providing them as additional liquidity needs a separate
`increaseLiquidity` call (LLM can chain).

## Supported chains

Subgraph URLs configured for Ethereum, Base, Arbitrum, Optimism,
Polygon. Pools query rejects others as `unsupported_chain`. Position
read/write works on any chain Uniswap V3 deploys to (the position
manager address is identical CREATE2-determined).

## Errors

- `wallet_not_connected` — only for write actions.
- `param_error` — bad token id (must be integer string).
- `not_implemented` — `provide` without `calldata`.
- `unsupported_chain` — pools query for non-Uniswap chain.
- `rpc_error` — `info` failed to read positions().
- `api_error` — subgraph request failed.

## Common workflows

### "How much have my LP fees grown?"

1. `info` to see current position.
2. `compound` to collect fees back to wallet.
3. `defi_balance` to see post-collect token amounts.
