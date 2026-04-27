---
name: defi-trading
description: Swap tokens via DEX aggregators, set limit/stop/DCA orders, manage trade routes
metadata:
  hermes:
    tags: [crypto, evm, defi, trading, swap]
    category: clawmes
    requires_tools: [defi_swap, manage_orders, defi_balance, defi_price]
---

# DeFi Trading

Use the `defi_swap` tool to trade tokens through aggregator routes (0x, 1inch, LiFi). Use `manage_orders` for limit/stop/trailing/DCA orders that the local plan scheduler watches.

## When to use

- User asks to "swap X to Y", "trade", "buy", "sell"
- "Set a limit order to buy ETH at 1800"
- "DCA $100 into ETH every Friday"
- "Stop-loss my BTC at 60k"

## Common flows

### Spot swap

1. Confirm chain and tokens. If user said "ETH to USDC", default to Base unless they specify.
2. Use `defi_price` to surface the current price + USD value of the trade.
3. Use `defi_swap` with `action="quote"` first to show route + slippage + gas.
4. Show the user: `from -> to`, amount in / minimum out at slippage, route (which DEX), gas estimate.
5. On user confirmation, retry with `action="swap"`. The policy gate may issue a `POLICY HOLD` for large swaps; relay the confirmation request and retry with `policyConfirmationNonce`.
6. Surface tx hash + block-explorer link from the result.

### Limit / stop / trailing order

`manage_orders` registers the order with the local plan scheduler. The order persists across restarts. Use:
- `action="limit_buy"` / `"limit_sell"` with `price` and `amount`
- `action="stop"` with `trigger_price` and `amount`
- `action="trailing"` with `trail_percent` and `amount`
- `action="dca"` with `amount`, `period` (e.g. "1w"), `iterations`
- `action="cancel"` with `order_id`
- `action="list"` to enumerate active orders

### Slippage and price impact

- Default slippage: 50 bps (0.5%). Increase to 100-300 bps for thin liquidity / volatile tokens.
- Surface price impact > 1% explicitly — this is your liquidity warning.
- For very large swaps (> $10k or >5% of pool depth), suggest splitting into chunks or using `manage_orders dca`.

## Pitfalls

- **Token not on chain**: many tickers exist on multiple chains (USDC on Ethereum vs Base). Always confirm chain.
- **Approval needed first**: ERC-20 swaps require an approval to the aggregator router. The aggregator typically handles this in a single combined tx, but fresh wallets may see two prompts. Don't be surprised by the first prompt being "approve" not "swap".
- **MEV exposure**: large swaps on Ethereum mainnet are vulnerable to sandwich attacks. Default to private mempool routes when available.
- **Wrapped tokens**: swapping ETH ↔ WETH is a wrap/unwrap, not a real swap; gas is much lower. Some aggregators handle this transparently.

## Verification

- Pre-trade: `defi_swap action="quote"` returns `details.expected_out`, `details.min_out`, `details.gas_estimate_usd`, `details.route`.
- Post-trade: `details.tx_hash` for explorer lookup.
- Cross-check by asking the user to verify on the block explorer (use `block_explorer` tool).

## Related

- `defi_balance` — confirm pre/post balances
- `defi_price` — current token prices
- `cost_basis` — track P&L over time
- `block_explorer` — verify the on-chain receipt
