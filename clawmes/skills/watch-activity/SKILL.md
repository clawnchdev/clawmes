---
name: watch-activity
description: Track addresses for ongoing on-chain activity + fetch recent events
metadata:
  hermes:
    tags: [crypto, defi, monitoring, alerts]
    category: clawmes
    requires_tools: [watch_activity, herd_intelligence]
---

# Watch On-chain Activity

The `watch_activity` tool maintains a persistent watch list and
exposes a one-shot read for any address. Hermes' cron daemon polls
the watch list and surfaces alerts.

For broader market intelligence (whale tracking, smart-money flows),
use `herd_intelligence` instead.

## When to use `watch_activity`

- User says: "watch the Aave treasury wallet"
- User says: "alert me on activity from 0x..."
- User says: "show recent activity for vitalik.eth"
- User says: "stop watching that address"

## Actions

### `watch` — register an address

```json
{"action": "watch", "address": "0x...", "chain_id": 8453, "label": "aave-treasury"}
```

Adds to the watch list at `${HERMES_HOME}/clawmes/watch/list.json`.
The label is optional but useful for the user. Same (address,
chain_id) pair won't double-add.

### `unwatch` — remove

```json
{"action": "unwatch", "address": "0x...", "chain_id": 8453}
```

### `list` — show watched

```json
{"action": "list"}
```

### `recent` — one-shot fetch (no registration)

```json
{"action": "recent", "address": "0x...", "chain_id": 1, "limit": 25}
```

Returns recent log events for the address via the block-explorer
logs endpoint. For a deeper history, use `block_explorer` directly.

## When to use `herd_intelligence`

- User says: "what are whales buying right now"
- User says: "trace 0x... — what wallets do they trust"
- User says: "alert me on $1M+ swaps"
- User says: "what flowed into Base from CEXes today"

Requires `HERD_ACCESS_TOKEN`. Aggregates wallet labels + DEX trades
+ flow data into a more analytical view than raw activity logs.

## Cron integration

Watched addresses fire alerts via Hermes' cron daemon. The default
cadence is every 60s; configure via `~/.hermes/cron/jobs.json` if
you want denser polling.

Alerts surface as messages in the active gateway (Telegram, Discord,
etc.). The actual alert dispatch is upstream Hermes territory; this
tool is the management interface for the watch list.

## Errors

- `param_error` — invalid address.
- `not_found` — unwatch against an address not in the list.
- `explorer_error` — logs endpoint rate-limited or unreachable.
