---
name: bv7x
description: Use the BV-7X clawnch-ecosystem oracle for BTC market context, prediction track record, on-chain attestations, and (with BV7X_API_KEY) the gated signal + copy-trade endpoints.
tags: [bv7x, clawnch, btc, oracle, prediction, polymarket, eas, agent-economy]
---

# clawmes:bv7x

BV-7X is an autonomous Bitcoin signal oracle in the Clawnch ecosystem
(`$BV7X` was launched on the Clawnch launchpad). It publishes daily
BTC direction predictions at 22:00 UTC, posts EAS attestations on
Base before outcomes are known, and operates a Polymarket wager bot.

clawmes wires BV-7X across three LLM-callable tools, two slash
commands, and a public REST + on-chain attestation surface.

## When to reach for this skill

- User asks about BTC price, Fear & Greed, or ETF flows.
- User wants BV-7X's prediction history, accuracy, or current regime.
- User wants to verify a specific BV-7X attestation by UID (proves
  the prediction was committed before the outcome was known).
- User asks "what is BV-7X saying?" — note the daily signal direction
  is gated; use `bv7x_oracle.signal_metadata` for the public market
  context portion, or `bv7x_oracle.oracle` with `BV7X_API_KEY` set
  for the full signal.

## Tools

### `bv7x` — agent + commerce surface

| Action | Returns |
|---|---|
| `regime` | Current BTC regime classification (CRISIS / BEAR / NEUTRAL / BULL / EUPHORIA) + risk + thresholds |
| `identity` | BV-7X's ERC-8004 agent identity record |
| `reputation` | BV-7X's on-chain reputation score |
| `discover` | BV-7X's A2A skill card (use with `a2a_call`) |
| `a2a_task` | Get a specific A2A task by id (requires `task_id`) |
| `commerce` | List BV-7X's commerce offerings |
| `copy_trade_status` | Public copy-trade service status |

### `bv7x_oracle` — signal + attestation surface

| Action | Returns | Auth |
|---|---|---|
| `scorecard` | Track record (`horizon` 1-7 days) — total predictions, accuracy, streak | free |
| `signal_metadata` | Current signal envelope (`horizon_str` 2d/3d/7d) — direction GATED, everything else free | free |
| `onchain_latest` | Latest on-chain attestation | free |
| `onchain_history` | Paginated attestation history (`limit`) | free |
| `onchain_stats` | Aggregate on-chain stats | free |
| `verify_uid` | Verify a specific attestation by UID | free |
| `oracle` | Full signal direction + confidence | 500M `$BV7X` |
| `oracle_premium` | Full breakdown + history | 1B `$BV7X` |
| `copy_trade_next` | Next trade intent for replication | 1B `$BV7X` |
| `copy_trade_history` | Recent trade intents + outcomes | 1B `$BV7X` |

To use the gated actions, hold the required `$BV7X` amount and
complete the wallet-verify at <https://bv7x.ai/terminal#developer>
to get a session token. Set it as `BV7X_API_KEY` in `~/.hermes/.env`.

### `bv7x_market` — quick BTC reads

| Action | Returns |
|---|---|
| `btc_price` | Current price + 24h change + market cap |
| `fear_greed` | Bitcoin Fear & Greed Index + label |
| `etf_flows` | 7-day and 30-day Bitcoin ETF flow totals |

### `eas_attestation` — generic EAS reader

Independent of BV-7X but useful here: `eas_attestation.get(uid)`
reads an EAS attestation directly from the Base contract at
`0x4200000000000000000000000000000000000021`. BV-7X's predictions
are posted as EAS attestations, so this verifies the on-chain
commitment without going through bv7x.ai.

### `a2a_call` — agent-to-agent JSON-RPC

`a2a_call.discover(agent_url="https://bv7x.ai")` fetches BV-7X's
AgentCard. `a2a_call.send_task(...)` invokes a skill. Useful for
agent-to-agent workflows where clawmes is one of multiple peers.

## Slash commands

- `/bv7x` — show BV-7X track record + regime + agent ID in one block.
  Surfaces a hint if `BV7X_API_KEY` isn't set.
- `/btc` — quick BTC price + Fear & Greed + ETF flow line.

## Worked examples

### "What's BV-7X saying about BTC right now?"

```
bv7x_oracle action=signal_metadata
→ market_context (btc_price, fear_greed, etf_flow_7d) + signal="GATED"
  (unless BV7X_API_KEY set, then the actual direction)
```

If gated, follow up with `bv7x_oracle action=onchain_latest` — the
last published direction is on-chain and free.

### "Has BV-7X been right lately?"

```
bv7x_oracle action=scorecard horizon=7
→ {totalPredictions, resolved, wins, losses, accuracy, streak}
```

Use this to size confidence in any acted-upon signal.

### "Verify this BV-7X attestation"

```
eas_attestation action=get uid=0x<UID>
   → full Attestation struct from the EAS contract on Base
bv7x_oracle action=verify_uid uid=0x<UID>
   → BV-7X's own verifier response (use both for belt-and-suspenders)
```

### "Place a trade based on BV-7X's signal"

`bv7x_oracle.copy_trade_next` (1B `$BV7X` gated) returns the next
trade intent. The actual order placement uses `bankr_polymarket`
(clawmes) — BV-7X publishes the signal; the user decides whether
to act on it. **The agent should never auto-execute trades on a
third-party signal without explicit user confirmation.**

## What's NOT in this skill

- Auto-injection of BV-7X signals into the agent's per-turn context.
  Embedding one party's directional bet as ambient context is bad
  design. Users who want BV-7X data call the tool.
- ERC-8004 agent registry interaction (separate from BV-7X — the
  registry spec is still draft).
- WebSocket streaming subscriptions. The 60-second cache in
  BV7XService is the freshness layer; for true real-time, the user
  runs `wss://bv7x.ai/ws/signal` directly with their own infra.
