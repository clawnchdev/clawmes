---
name: defi-swap
description: Swap tokens via the 0x DEX aggregator (cheapest route across all DEXes)
metadata:
  hermes:
    tags: [crypto, evm, defi, swap, dex]
    category: clawmes
    requires_tools: [defi_swap, clawnchconnect]
---

# Swap Tokens via 0x

The `defi_swap` tool routes through the 0x aggregator — checks every
on-chain DEX (Uniswap V2/V3/V4, Balancer, Curve, Sushiswap, etc.) for
the best price and packages the swap as a single signed transaction.

## When to use

- User says: "swap 0.1 ETH to USDC"
- User says: "trade 100 USDC for ETH"
- User says: "buy WETH with my USDC"
- User says: "what's the best price for ETH→USDC right now"

## Pre-flight

Confirm a wallet is connected. If `clawnchconnect status` reports
no wallet, run `clawnchconnect mode=walletconnect` first and surface
the pairing URI to the user.

## Recommended flow

1. **Quote first** — call with `action="quote"` to show the user
   what they'll get. No signing, no commitment.
2. **Confirm with the user** — surface buy_amount, min_buy_amount
   (after slippage), and gas estimate. Ask for explicit approval if
   the policy gate doesn't already require it.
3. **Execute** — call with `action="swap"` only after the user
   confirms. Pass `policyConfirmationNonce` if the gate held.

## Required parameters

- `action` — `quote` (preview, no signing) | `swap` (execute) | `route`
  (compare aggregators).
- `sell_token` — `"ETH"` for native, or the ERC-20 contract address.
- `buy_token` — same format as `sell_token`.
- One of:
  - `sell_amount` — human units of the sell token (e.g. `"0.5"`).
  - `buy_amount` — human units of the buy token (target output).

## Optional

- `slippage_bps` — basis points (default 100 = 1%). Higher for
  illiquid pairs; lower for stablecoin swaps.
- `chain_id` — override (defaults to wallet's chain).

## Native ETH

The string `"ETH"` (case-insensitive) maps to 0x's native sentinel
(`0xeeee...eeee`). Same for `"native"`, `"matic"`, `"pol"`. Use the
sentinel directly for chains where the gas token has a different
common name.

## Errors to know

- `wallet_not_connected` — chain through `clawnchconnect`.
- `insufficient_liquidity` — 0x can't find a path. Try a smaller
  amount or different pair.
- `rate_limited` — surface the error and suggest waiting; or set
  `ZEROX_API_KEY`.
- `unsupported_chain` — 0x supports Ethereum, Base, Arbitrum,
  Optimism, Polygon. For others, fall back to `bridge`.
- `decimals_lookup_failed` — RPC couldn't read the token's decimals.
  Refuses to proceed (silent fallback would multiply the user's
  amount by 10^12 for 6-decimal tokens like USDC). Suggest checking
  the token address or trying a different RPC.

## Example calls

```json
{"action": "quote", "sell_token": "ETH", "buy_token": "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", "sell_amount": "0.1"}

{"action": "swap", "sell_token": "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913", "buy_token": "ETH", "sell_amount": "100", "slippage_bps": 50}
```

## Related

- `bridge` — for cross-chain swaps (sell on one chain, buy on another).
- `approvals` — to revoke 0x's permit2 allowance after use.
- `defi_balance` — to check post-swap holdings.
