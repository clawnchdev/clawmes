---
name: privacy
description: Privacy-preserving deposits and withdrawals via Lobster privacy pools
metadata:
  hermes:
    tags: [crypto, privacy, lobster, pool, mixer]
    category: clawmes
    requires_tools: [privacy, lobster_cash]
---

# Privacy Pools

Deposit funds into a Lobster privacy pool, share a note out-of-band, and let the recipient withdraw to an unlinked address. The on-chain link between sender and recipient is broken by the pool's anonymity set.

## When to use

- "send X privately to Y" → multi-step: `deposit` (sender), share note, `withdraw` (receiver).
- "deposit to a privacy pool" → `deposit`.
- "withdraw from a pool" → `withdraw` with the secret note.
- "what's the anonymity set on the ETH pool?" → `info`.

## Required parameters by action

- **`info`**: `pool` (pool address). No write — read-only.
- **`deposit`**: `amount`. Returns a secret note that the depositor must save.
- **`withdraw`**: `note` (the secret), `destination` (the address to receive funds).

## Common flows

### Send privately to a friend

This is **multi-step** and requires out-of-band coordination — the privacy guarantee depends on the note traveling through a channel that doesn't link to the deposit tx.

1. Run `privacy(action="info", pool=<addr>)` — confirm the anonymity set is large enough (≥ 100 deposits) for meaningful privacy.
2. Sender: `privacy(action="deposit", amount="1.0")`. Tool returns a `note` like `lobster:base:1eth:0x...`.
3. Sender shares the note via Signal, in person, or another non-on-chain channel. **Never** copy/paste the note into a tx memo or public chat.
4. Recipient: `privacy(action="withdraw", note=<note>, destination=<their fresh address>)` from a wallet that has no on-chain link to the sender.
5. Recipient now has the funds at `destination` with no on-chain trail back to the sender.

### Self-deposit for a clean address

1. `privacy(action="deposit", amount="1.0")` — sender deposits to themselves.
2. Wait several days / weeks for the anonymity set to grow.
3. `privacy(action="withdraw", note=<note>, destination=<a fresh wallet you control>)`.
4. The fresh wallet has the same funds with no on-chain history back to your original wallet.

## Pitfalls

- **Anonymity-set size is the privacy**: a pool with 5 deposits gives near-zero privacy. Always check `info` first; only use pools with ≥ 100 deposits and active inflow.
- **Time correlation**: depositing 1 ETH and withdrawing 1 ETH within an hour from the same pool is trivially de-anonymizable by timing analysis. Wait at least a day, ideally a week, between deposit and withdraw.
- **Amount correlation**: if you're the only depositor of an unusual amount (e.g. 12.345 ETH), the link is preserved. Use round amounts (1, 10, 100 ETH) that match the pool's standard denomination.
- **Note safety**: losing the note = losing the funds. Treat it like a private key. Pools have no recovery flow.
- **Regulatory environment**: privacy pool usage is being scrutinized in some jurisdictions. Make sure the user understands their local regulatory exposure before suggesting deposits. Don't proactively push privacy operations on users who didn't ask.
- **Source-of-funds exposure**: privacy pools that accept tainted funds (sanctioned addresses, hack proceeds) can themselves get blacklisted. Lobster filters at deposit time, but be aware.

## Related tools

- `lobster_cash` — direct backend access if the user wants pool-specific actions not exposed by `privacy`.
- `transfer` — for non-private transfers that don't need a pool.
- `block_explorer` — to verify the deposit / withdraw transactions on-chain.
