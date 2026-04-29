---
name: approvals
description: Manage ERC-20 token approvals — list, audit, approve, revoke
metadata:
  hermes:
    tags: [crypto, evm, wallet, security]
    category: clawmes
    requires_tools: [approvals]
---

# ERC-20 Approvals

ERC-20 approvals are the most common drainer attack vector. A user
grants a contract permission to spend their tokens; if the contract
gets compromised (or was malicious to begin with), the attacker can
empty every token allowance.

The `approvals` tool surfaces every active approval and lets users
revoke them in seconds.

## When to use

- User says: "audit my approvals"
- User says: "what approvals do I have on Base"
- User says: "revoke unlimited approvals"
- User says: "I want to revoke spender X"

## Recommended workflow: audit → revoke

1. **`audit`** — lists every active approval and flags risky ones
   (specifically: `unlimited` allowances). Show the user; let them
   pick which to revoke.

   ```json
   {"action": "audit", "chain_id": 8453}
   ```

2. **`revoke`** — sets the allowance to 0 for a specific
   (token, spender) pair.

   ```json
   {"action": "revoke", "token": "0xtoken...", "spender": "0xspender..."}
   ```

## Actions

- `list`    — every active approval (raw, no flagging).
- `audit`   — same as list but classifies each as `ok` or `high` risk.
- `approve` — grant an allowance. Use `amount: "unlimited"` for max
  (the convenience option most dApps want, but worth confirming with
  the user since it's the highest-risk grant).
- `revoke` — alias for `approve` with `amount=0`.

## Why this is important

Each ERC-20 approval is permanent until revoked. Common scenarios
where users have stale approvals:

- "I used Uniswap V2 once two years ago" → unlimited USDC approval
  to the V2 router still active.
- "I tried that random NFT mint" → unlimited USDC approval to a
  random contract, possibly compromised.
- "I bridged once" → unlimited token approval to a bridge router
  that may or may not still be the canonical version.

Running `audit` periodically (every few months) is good wallet
hygiene.

## How it works under the hood

The tool queries the explorer logs API for `Approval` events
emitted with the wallet as `owner`, then issues `eth_call` to the
token's `allowance()` view to confirm the current value. Approvals
that have been used up or revoked don't appear (the current
allowance is 0).

## Errors

- `wallet_not_connected` — connect first.
- `explorer_error` — the block explorer's logs API rate-limited or
  failed. Retry with a smaller from_block window.
- `decimals_lookup_failed` — only on `approve` with non-unlimited
  amounts; tool refuses to proceed without confirmed token decimals.

## Required env

- `BASESCAN_API_KEY` / `ETHERSCAN_API_KEY` etc. — Improves logs API
  rate limits. Free tier works for occasional audits.
