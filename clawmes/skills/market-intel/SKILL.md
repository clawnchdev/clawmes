---
name: market-intel
description: Trending tokens, top movers, whale flows, and net inflow analytics
metadata:
  hermes:
    tags: [crypto, market, intel, trending, whales]
    category: clawmes
    requires_tools: [market_intel]
---

# Market Intelligence

Surface market signals from CoinGecko (trending), the Herd Intelligence API (whale flows), and price feeds (top movers). Read-only, no on-chain calls.

## When to use

- "what's trending?" → `trending`
- "what are the biggest gainers today?" → `top_movers` with `direction="gainers"`
- "what's pumping?" → `top_movers` with `direction="gainers"`
- "what whales are doing" → `whales`
- "where's smart money flowing?" → `flows`

## Required parameters

- **`trending`**: optional `limit` (default 10).
- **`top_movers`**: `direction` (`gainers` or `losers`), optional `limit`.
- **`whales`**: optional `chain_id` to scope, optional `min_value_usd`.
- **`flows`**: optional `chain_id`, optional `window_hours`.

## Common flows

### Daily market check-in

1. `market_intel(action="trending", limit=5)` — what's getting attention.
2. `market_intel(action="top_movers", direction="gainers", limit=5)` — what's actually pumping.
3. Cross-check with `defi_price` on any token the user wants more detail on.

### Spot smart-money rotation

1. `market_intel(action="flows", window_hours=24)` — net inflow / outflow per token.
2. Highlight tokens where whale wallets are accumulating but retail price hasn't moved yet — those are setup candidates.
3. Be explicit that "smart money" classification is heuristic; never frame it as guaranteed alpha.

### Pre-trade due diligence

Before suggesting a buy, run:
1. `market_intel(action="trending")` — is the token getting hyped?
2. `market_intel(action="whales", chain_id=8453)` — are whales loading or distributing?
3. `analytics(action="rsi", token=...)` — is it overbought (RSI > 70)?
4. `defi_price(action="quote", from_token=..., to_token=...)` — current spot.

If the token is trending + whales distributing + RSI > 70, that's a reversal setup, not a buy.

## Pitfalls

- **Trending ≠ good**: tokens trend on CoinGecko because of pump groups, exploits, listings, and rugs. Trending is attention, not signal. Always cross-reference with whales / flows / TA.
- **Whale labels are heuristic**: addresses get labeled "whale" by net assets + activity patterns, but the label can be wrong. Treat it as a hint.
- **Flow data lag**: net inflows are computed over windows (1h / 6h / 24h). A 24h flow can mask intra-period reversals.
- **Top movers selection bias**: low-cap tokens dominate the gainer / loser lists because % moves are largest at low market caps. Most aren't actionable for any meaningful position size.
- **API requirement**: `whales` and `flows` need `HERD_ACCESS_TOKEN`. Without it, those actions return `no_credentials` cleanly.

## Related tools

- `defi_price` — get current spot for any token.
- `analytics` — RSI / MACD / Bollinger on the same tokens.
- `herd_intelligence` — direct Herd API access for deeper queries.
- `nft` — NFT-specific volume / floor data via Reservoir.
