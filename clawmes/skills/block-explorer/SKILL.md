---
name: block-explorer
description: Look up transactions and addresses on Etherscan-family explorers
metadata:
  hermes:
    tags: [crypto, evm, intel, explorer]
    category: clawmes
    requires_tools: [block_explorer]
---

# Block Explorer Lookups

Use the `block_explorer` tool to verify transactions and inspect addresses on Etherscan, Basescan, Arbiscan, Optimistic Etherscan, and Polygonscan.

## When to use

- "Did my tx confirm?"
- "What's the status of 0x...?"
- "How many transactions has this address sent?"
- "Show me what this address has been doing"

## Actions

| Action | What | Required args |
|---|---|---|
| `tx` | Receipt status + error description + explorer URL | `value` = tx hash, `chain` |
| `address` | Native balance + tx count + explorer URL | `value` = address, `chain` |

## Common flows

### Verify a transaction

1. User pastes a hash or asks "did my swap go through?"
2. `block_explorer action="tx" value="<hash>" chain="<chain>"`.
3. Surface: receipt status (success / failed), error description (e.g. "out of gas", "execution reverted"), block-explorer URL for them to click through.
4. If failed and the user is unclear why, link to the explorer for the call trace — clawmes doesn't decode internal call traces yet.

### Inspect an address

1. `block_explorer action="address" value="<addr>" chain="<chain>"`.
2. Returns native balance + tx count + explorer URL.
3. For richer holdings (ERC-20s), use `defi_balance action="summary"` instead.
4. For a contract address, `address` action shows tx count which loosely indicates activity; for source code use the (TODO) contract sub-action.

### Cross-chain hash lookup

If the user pastes a hash but doesn't know the chain, try Base first (most common for clawmes), then Ethereum, then the user's recent activity chains. The hash itself is chain-agnostic — same format on every EVM — so you may need to probe.

## Pitfalls

- **Free tier rate limits**: without `BASESCAN_API_KEY` / `ETHERSCAN_API_KEY` env vars set, you're capped at ~5 req/sec. With keys, much higher. Tell the user to set keys for heavy use.
- **Recently-mined txs**: Etherscan can lag the chain by a few seconds. If a hash you just submitted shows "not found", retry after 10-30s before reporting failure.
- **Internal txs**: explorer "tx count" is the nonce-incrementing transactions sent FROM the address. It does NOT count internal calls or received transfers. Misinterpreting this is a common new-user error.
- **Pending txs**: receipt status is only available after the tx is mined. Pending txs will return an error or empty result; the tool surfaces this as "failed" — clarify by checking the explorer URL directly.
- **Reorgs**: rare on production chains (~12 confirmations is final on Ethereum), but L2s can have brief reorgs. A "confirmed" status that flips back to "failed" later is a reorg.

## Verification

- The `details.explorer_url` field always points to the canonical chain explorer. Encourage the user to click through for the full call trace, decoded events, and gas analysis.
- Receipt status is `success` for txs that completed without revert, `failed` for txs that ran but reverted (you still pay gas).

## Related

- `defi_balance` — for richer balance views including ERC-20s
- `watch_activity` — for ongoing monitoring of an address (TODO at this milestone)
- `herd_intelligence` — for whale-tracking and bulk address analysis
