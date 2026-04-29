---
name: cost-basis
description: FIFO P&L tracking from the local transaction ledger
metadata:
  hermes:
    tags: [crypto, evm, accounting, taxes, pnl, fifo]
    category: clawmes
    requires_tools: [cost_basis]
---

# Cost Basis & P&L

Track realized + unrealized P&L using FIFO matching across the local transaction ledger. Reads only — no API calls.

## When to use

- "what's my realized P&L this year?" → `summary`
- "P&L on my ETH bag" → `by_token`
- "show me my closed positions" → `realized`
- "what are my unrealized gains?" → `unrealized`
- "export my trades for tax software" → `export`

## Required parameters

- **`summary`**: no required args. Returns realized + unrealized totals across all tokens.
- **`by_token`**: `token` (address or symbol).
- **`realized` / `unrealized`**: optional `token` to scope.
- **`export`**: returns matched lots in CSV-friendly format.

## Common flows

### Year-end tax view

1. Call `cost_basis(action="summary")` for the headline number.
2. Call `cost_basis(action="export")` for the row-by-row breakdown.
3. Hand the export to the user with a note that it's FIFO-matched and they should consult their accountant for jurisdiction-specific treatment.

### Position-level P&L

1. User asks "how am I doing on ETH?" → call `cost_basis(action="by_token", token="ETH")`.
2. Show: cost basis (avg buy price), current price, unrealized P&L, realized P&L from prior sells.
3. If unrealized P&L is significant, suggest setting a `manage_orders` trailing stop.

### Specific transaction detail

The ledger lives at `${HERMES_HOME}/clawmes/ledger/`. For deeper drill-down, suggest the user use `block_explorer` on individual tx hashes from the export.

## Pitfalls

- **Ledger completeness**: cost basis is only as accurate as the ledger. If the user has imports from outside clawmes (CEX trades, airdrops, hard forks), those won't appear unless added manually. Suggest the user run `/import` (CSV) for past trades.
- **FIFO vs HIFO/LIFO**: clawmes uses FIFO. Some jurisdictions allow LIFO or specific-lot accounting. Note this in tax exports.
- **Wash sales / lot identification**: not modeled. The export is a starting point; consult a tax pro for adjustments.
- **Token decimals**: amounts in the export are in human units (already-decimaled), so you don't need to do any base-unit math.
- **Stablecoin trades**: USDC↔USDT trades produce near-zero P&L but still count as taxable events in most jurisdictions. They appear in the export at their fill price.

## Related tools

- `defi_swap` — produces ledger entries on every swap.
- `transfer` — produces ledger entries on every transfer (tax-relevant when sending to another taxpayer).
- `block_explorer` — for cross-checking tx detail.
- `defi_price` — for spot-checking current values.
