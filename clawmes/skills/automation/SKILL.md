---
name: automation
description: Multi-step plans (DCA, conditional triggers, loops) via compound_action and manage_orders
metadata:
  hermes:
    tags: [crypto, defi, automation, dca, limit-orders]
    category: clawmes
    requires_tools: [compound_action, manage_orders]
---

# Automation — Plans and Orders

Two complementary tools:

- **`manage_orders`** — single-trigger orders (limit, stop, trailing,
  DCA). Persisted to disk; the plan scheduler picks them up via cron.
- **`compound_action`** — multi-step compound plans (conditionals,
  loops, parallel branches). Routes to the same scheduler with richer
  IR.

Use `manage_orders` for "buy X when Y price". Use `compound_action`
for "if A then B else C, retry up to N times".

## When to use `manage_orders`

- User says: "buy 1 ETH when it drops to $1500"  → `limit_buy`
- User says: "sell my SOL at $300"               → `limit_sell`
- User says: "set a stop-loss on my UNI at $5"   → `stop`
- User says: "trailing 5% stop on my AAVE"       → `trailing`
- User says: "DCA $100 into ETH weekly for 12 weeks" → `dca`

### Schemas

```json
{"action": "limit_buy", "token": "0x...", "amount": "1", "trigger_price": "1500"}
{"action": "limit_sell", "token": "0x...", "amount": "10", "trigger_price": "300"}
{"action": "stop", "token": "0x...", "amount": "5", "trigger_price": "5"}
{"action": "trailing", "token": "0x...", "amount": "5", "trail_pct": 0.05}
{"action": "dca", "token": "0x...", "amount": "1200", "chunks": 12, "interval_seconds": 604800}
```

`list` shows active orders; `cancel` removes one by `order_id`.

## When to use `compound_action`

- User says: "if ETH > $4000 swap half to USDC, otherwise hold"
- User says: "every Monday, send the LP fees to my treasury"
- User says: "DCA into ETH and BTC simultaneously over 4 weeks"
- User says: "validate this plan before running"

### Schemas

```json
{"action": "create", "plan": "<NL or DSL>"}
{"action": "validate", "plan": "<NL or DSL>"}
{"action": "dry_run", "plan": "<NL or DSL>"}
{"action": "list"}
{"action": "logs", "plan_id": "p1"}
{"action": "cancel", "plan_id": "p1"}
```

Plans accept either natural-language descriptions (the IR compiler
parses them) or the explicit DSL. Validate first if unsure;
dry_run simulates without on-chain side effects.

## Storage + execution

Both tools persist to `${HERMES_HOME}/clawmes/orders/` (per-order
JSON files) or the plan scheduler's database. Hermes' cron daemon
ticks every minute and the plan scheduler evaluates triggers each
tick.

When a trigger fires, the tool executes via the appropriate
underlying tool — `defi_swap` for swaps, `transfer` for sends, etc.

## Errors

- `not_implemented` — `compound_action` actions that the plan
  scheduler doesn't yet support (the scheduler is partial; full
  feature support lands across subsequent commits).
- `not_found` — cancel/logs against a nonexistent order/plan.
- `param_error` — bad amount, trigger, or interval.
- `storage_error` — disk write failed; check `HERMES_HOME` is
  writable.
