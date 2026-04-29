---
name: bankr
description: Bankr-tier features — token launches, automation, Polymarket, leverage
metadata:
  hermes:
    tags: [crypto, bankr, custodial]
    category: clawmes
    requires_tools: [bankr_launch, bankr_automate, bankr_polymarket, bankr_leverage]
---

# Bankr Tools

Four Bankr-tier tools that route through the Bankr custodial backend.
All four require `BANKR_API_KEY` env var (sign up at bankr.bot).

## When to use which

| Goal | Tool |
|---|---|
| Deploy a new token (gas-sponsored) | `bankr_launch` |
| Server-side automation (DCA, limit, etc.) | `bankr_automate` |
| Polymarket prediction-market bets | `bankr_polymarket` |
| Avantis leveraged perps | `bankr_leverage` |

## Bankr-only vs alternatives

These features are **Bankr-only** (no non-Bankr equivalent):

- Token launches with Bankr-sponsored gas — `bankr_launch`.
- Avantis perpetuals (Base perp DEX) — `bankr_leverage`.
- Polymarket execution on Polygon — `bankr_polymarket`.

These have **non-Bankr alternatives**:

- `bankr_automate` ⟷ `compound_action` + `manage_orders` (local
  scheduler runs on user's machine, no third party).

## `bankr_launch`

```json
{"action": "deploy", "name": "Test", "symbol": "TST", "supply": "1000000", "chain": "base"}
{"action": "pair", "token": "0x...", "chain": "base"}
{"action": "info", "token": "0x...", "chain": "base"}
```

Deploys ERC-20 (Base) or SPL (Solana). Bankr sponsors gas; user pays
nothing on chain. The `pair` action creates the Uniswap V4 pool for
the deployed token.

## `bankr_automate`

```json
{"action": "create", "payload": {"rule_type": "dca", ...}}
{"action": "list"}
{"action": "pause", "rule_id": "r-123"}
{"action": "resume", "rule_id": "r-123"}
{"action": "delete", "rule_id": "r-123"}
```

Payload shape varies by `rule_type` (DCA, limit, stop-loss, etc.).
Server-side execution; rules survive even if the user's clawmes
session is offline.

For local execution with the same shape, use `compound_action` /
`manage_orders` instead.

## `bankr_polymarket`

```json
{"action": "markets"}
{"action": "positions"}
{"action": "bet", "payload": {"market_id": "...", "outcome": "yes", "amount": "100"}}
{"action": "sell", "payload": {"position_id": "..."}}
{"action": "claim", "payload": {"position_id": "..."}}
```

Bankr handles the underlying CLOB on Polygon. Bets settle in USDC.

## `bankr_leverage`

```json
{"action": "open", "payload": {"market": "ETH-PERP", "direction": "long", "size": "1000", "leverage": 5}}
{"action": "close", "payload": {"position_id": "..."}}
{"action": "adjust", "payload": {"position_id": "...", "leverage": 3}}
{"action": "positions"}
{"action": "funding", "payload": {"market": "ETH-PERP"}}
```

1-10x leverage on Avantis (Base perp DEX). Bankr provides funding
rate intel via the `funding` action.

## Errors

All four tools surface Bankr's error codes verbatim:

- `no_credentials` — `BANKR_API_KEY` not set.
- `network` — Bankr API unreachable.
- `api_error` — generic Bankr error (read message for detail).
- `param_error` — missing payload for actions that require one.
