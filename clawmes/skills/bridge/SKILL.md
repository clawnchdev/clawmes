---
name: bridge
description: Cross-chain asset bridging via LiFi, Across, Stargate aggregators
metadata:
  hermes:
    tags: [crypto, evm, defi, bridge, cross-chain]
    category: clawmes
    requires_tools: [bridge, defi_balance, defi_price]
---

# Cross-chain Bridging

Use the `bridge` tool to move tokens between chains via aggregators that compare LiFi, Across, Stargate, and others. The aggregator picks the route with the best ratio of (output amount, ETA, gas cost).

## When to use

- "Bridge X from Ethereum to Base"
- "Move my USDC to Arbitrum"
- "What's the cheapest way to get to <chain>?"

## Actions

| Action | What | Required args |
|---|---|---|
| `quote` | Compare routes across bridges | `from_chain`, `to_chain`, `token`, `amount` |
| `bridge` | Execute the chosen route | (same args + optional `route` from a previous quote) |
| `status` | Track an in-flight bridge | `tx_hash` from the source chain |
| `routes` | Available routes for a token pair (no commitment) | `from_chain`, `to_chain`, `token` |

## Common flows

### Quote first, then bridge

1. Always start with `bridge action="quote"`. The result lists routes ranked by output amount.
2. Show the user: source amount → expected destination amount, ETA (some routes are seconds, others minutes), bridge fee, gas cost on source.
3. Ask for confirmation. Bridges are essentially irreversible from the user's POV — once funds leave the source chain, they're committed.
4. Call `bridge action="bridge"` with the chosen route. Returns the source-chain tx hash.
5. Use `bridge action="status"` periodically to check delivery on the destination.

### Native ETH ↔ L2

- L1 → L2 via canonical bridge (Optimism Standard Bridge, Arbitrum Bridge): typically 7+ days for L2 → L1, near-instant for L1 → L2 once confirmed.
- L1 → L2 via aggregator (LiFi, Across): minutes via liquidity-pool-backed bridges. Slightly worse rate than canonical, much faster.
- For small amounts, aggregator is almost always the right call. For very large amounts where speed doesn't matter, canonical bridge avoids the slippage.

### Stablecoin between L2s

- USDC has a "Cross-Chain Transfer Protocol" (CCTP) for native USDC moves between Ethereum, Arbitrum, Base, Optimism, Polygon. This is the safest path — Circle controls the burn/mint.
- The aggregator surfaces CCTP routes when applicable. Prefer those over wrapped-USDC routes.

## Pitfalls

- **Wrong destination address**: most bridges accept the same address on both chains, but some require explicit specification. Default to the connected wallet's address; require user override.
- **Native vs wrapped**: bridging "ETH" might actually deliver WETH on the destination, or vice versa. Surface this in the quote.
- **Slippage on swap-bridges**: aggregators that swap mid-bridge (e.g. ETH → USDC → bridge → USDC → ETH) can have surprising slippage. Watch the expected-out vs minimum-out spread.
- **Bridge fee != gas**: aggregator + bridge protocol take a fee in addition to chain gas. Total cost = source gas + bridge fee + (optional) destination gas if the destination chain isn't the user's home.
- **Failed bridges**: rare but real — funds can be stuck for hours/days requiring manual intervention. Recommend small test amounts for first-time routes.

## Verification

- Source-chain confirmation: tx hash from the bridge action; verify via `block_explorer`.
- Destination delivery: `bridge action="status"` polls the bridge's API. Most aggregators expose tracking.
- Final check: `defi_balance` on the destination chain post-arrival.

## Related

- `block_explorer` — verify source-chain tx
- `defi_balance` — verify destination delivery
- `defi_swap` — sometimes a better path: swap on the destination chain after bridging native
- `wayfinder` — when the route is non-obvious (e.g. ETH → ARB on Arbitrum), the wayfinder tool computes optimal multi-hop paths
