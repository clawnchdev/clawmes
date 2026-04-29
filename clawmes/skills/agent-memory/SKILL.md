---
name: agent-memory
description: Persistent cross-session memory — preferences, addresses, recurring intents
metadata:
  hermes:
    tags: [memory, persistence, hermes]
    category: clawmes
    requires_tools: [agent_memory]
---

# Agent Memory

Persistent memory that survives session resets. Use it to remember user preferences, named addresses, recurring trade setups, and anything else the user explicitly wants you to recall later.

## When to use

- "remember that vitalik.eth is my friend's address" → `add`.
- "what was I going to do with my staked ETH?" → `query`.
- "update my default slippage to 0.5%" → `replace`.
- "forget my Polymarket positions" → `remove`.

## Required parameters

- **`add`**: `key`, `value`. The `key` should be human-meaningful ("default-slippage", "alice-address").
- **`replace`**: `key`, `value`. Overwrites if key exists; creates if not.
- **`remove`**: `key`.
- **`query`**: optional `key` (exact match) or `search` (text search across all values).

## Common flows

### Save a named address

1. User: "remember alice = 0xabc...".
2. Call `agent_memory(action="add", key="alice", value="0xabc...123")`.
3. Confirm: "saved alice → 0xabc...123. I'll remember it across sessions."
4. Later: when the user says "send 1 ETH to alice", look up via `agent_memory(action="query", key="alice")` first.

### Save a default preference

1. User: "always use 0.3% slippage on swaps".
2. `agent_memory(action="replace", key="default-slippage-bps", value="30")`.
3. On future swaps, query the value before calling `defi_swap` and pass `slippage_bps=30`.

### Search memory

For free-form retrieval, prefer `search` over guessing the exact key:

1. User: "what was that token I was watching last week?"
2. `agent_memory(action="query", search="watching")`.
3. Surface any matches and ask the user to confirm.

## Pitfalls

- **Memory ≠ context**: memory persists across sessions; context (what was just said) doesn't always need to be saved. Save only what the user explicitly asks to remember, or what would be useful next session.
- **Overwriting is destructive**: `replace` doesn't merge; it overwrites. Confirm with the user before replacing keys with rich existing values.
- **Naming conventions**: keys are case-sensitive and exact-match. Suggest kebab-case for multi-word keys (`alice-base-address`, `default-slippage-bps`) so they're greppable.
- **Privacy**: memory is local to the user's machine, but it ends up in logs (redacted). Don't store mnemonics, private keys, or API keys in memory — those have dedicated, encrypted storage paths.
- **Drift**: prices, balances, positions change. Don't store snapshot values (e.g. "user's ETH balance: 1.5") — those go stale. Store stable facts (addresses, preferences, intents).

## Related tools

- `session_recall` — for searching across past chat sessions (different from explicit memory).
- `skill_evolve` — for proposing improvements to clawmes' own skills based on memory + behavior.
