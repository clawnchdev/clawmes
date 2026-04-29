---
name: manage-orders
description: Place limit, stop, trailing-stop, and DCA orders that fire when triggers hit
metadata:
  hermes:
    tags: [crypto, evm, trading, orders, limit, stop, dca]
    category: clawmes
    requires_tools: [manage_orders]
---

# Manage Orders

Schedule conditional orders that the plan scheduler fires when their triggers fire. Orders persist to disk and survive restarts.

## When to use

- "buy 1 ETH if it drops to $1500" → `limit_buy`
- "sell my SOL when it hits $300" → `limit_sell`
- "stop-loss my ETH at $1400" → `stop`
- "trailing stop 5% on my MOG bag" → `trailing`
- "DCA $50 into ETH every Monday for 12 weeks" → `dca`
- "show me my open orders" → `list`
- "cancel order abc-123" → `cancel`

## Required parameters by action

- **`limit_buy` / `limit_sell` / `stop`**: `token`, `amount`, `trigger_price` (USD).
- **`trailing`**: `token`, `amount`, `trail_pct` (decimal — 0.05 = 5%).
- **`dca`**: `token`, `amount`, `chunks`, `interval_seconds`.
- **`cancel`**: `order_id`.
- **`list`**: no required args.

## Common flows

### Place a limit buy

1. Confirm with user: `"limit_buy 1 ETH @ $1500 — confirm? (Y/N)"`.
2. Call `manage_orders(action="limit_buy", token="ETH", amount="1", trigger_price="1500")`.
3. The order persists to `${HERMES_HOME}/clawmes/orders/<id>.json`.
4. Show the user the order ID + a hint to `list` later.

### Set up a DCA plan

1. Confirm: `"DCA $50 into ETH every 7 days, 12 chunks — confirm?"`.
2. Call `manage_orders(action="dca", token="ETH", amount="50", chunks=12, interval_seconds=604800)`.
3. The plan scheduler fires each chunk at the interval; each fire calls `defi_swap` under the hood.

### Cancel an order

1. Show open orders first: `manage_orders(action="list")`.
2. Confirm with user which one: `"cancel order abc-123? (Y/N)"`.
3. Call `manage_orders(action="cancel", order_id="abc-123")`.

## Pitfalls

- **Trigger evaluation**: orders need the price service running. If the price service is down (no `COINGECKO_API_KEY` set, network down) the trigger never fires. The scheduler logs a warning per evaluation cycle.
- **Slippage on fill**: orders fill at market via `defi_swap`, which means the executed price can differ from `trigger_price` by 0.1–1% depending on liquidity. For tight limits, reduce `slippage_bps` on the implicit swap (advanced).
- **DCA gas costs**: each chunk is a full swap, so 12 chunks = 12 swap fees. On L1, that adds up; consider Base / Arbitrum for DCA.
- **Trailing stops**: the `trail_pct` is from the **highest price seen since order placement**, not from the price-at-placement. New highs ratchet the stop up; new lows do not move it down.
- **Policy gate**: orders gate through the policy evaluator like any other write. A large limit buy may surface `POLICY HOLD` requiring a confirmation nonce.

## Related tools

- `defi_swap` — the order executor under the hood; `manage_orders` calls it on trigger fire.
- `defi_price` — show the current price before placing a price-triggered order so the user has a reference.
- `cost_basis` — track P&L on filled orders.
