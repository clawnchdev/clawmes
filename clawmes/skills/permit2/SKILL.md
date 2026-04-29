---
name: permit2
description: Uniswap's universal allowance system — gasless approvals via signed permits
metadata:
  hermes:
    tags: [crypto, evm, wallet, security, uniswap]
    category: clawmes
    requires_tools: [permit2, approvals]
---

# Permit2 — Universal Allowances

Permit2 is Uniswap's universal allowance contract. Once a user
approves Permit2 on a token (one-time per-token approve), every
subsequent dApp interaction is gasless via signed EIP-712 permits.

The Permit2 contract address is identical on every chain
(`0x000000000022D473030F116dDEE9F6B43aC78BA3`) due to deterministic
CREATE2 deployment.

## When to use

- User says: "set up Permit2 for swapping"
- User says: "sign a permit for the swap"
- User says: "revoke my Permit2 allowance"
- User says: "what permits do I have active"

## Two-tier flow

Permit2 separates allowances into two layers:

1. **Token → Permit2** — standard ERC-20 approve (one-time per token).
   Use the `approvals` tool for this.
2. **Permit2 → Spender** — signed off-chain permits with expiration.
   Use this tool.

The result: after the one-time approval, every dApp interaction
that uses Permit2 is gasless on the allowance side.

## Actions

### `sign` — request a Permit2 signature

```json
{"action": "sign", "token": "0xtoken...", "spender": "0xspender...", "amount": "1000000000000000000", "expiration": 1764892800}
```

Builds the EIP-712 typed-data, gets the wallet to sign via
`sign_typed_data_v4`, returns the signature + permit struct. The
caller forwards both to the spender contract.

`amount: "unlimited"` maps to `uint160` max (the convenience option).
`expiration` defaults to 30 days from now if omitted.

### `revoke` — kill an existing permit

```json
{"action": "revoke", "token": "0xtoken...", "spender": "0xspender..."}
```

On-chain `approve(token, spender, 0, 0)` on the Permit2 contract.
Sets both amount and expiration to zero. Costs a tx.

### `list` — read current allowance

```json
{"action": "list", "token": "0xtoken...", "spender": "0xspender..."}
```

Returns `{amount, expiration, nonce}` for the (owner, token, spender)
triple. Permit2 doesn't expose a "list all my allowances" view — the
caller must specify the (token, spender) pair.

To enumerate every Permit2 allowance, run the `approvals` tool with
`spender=0x000000000022D473030F116dDEE9F6B43aC78BA3` (filter by
Permit2 as the spender). That gives the token list; then loop this
tool's `list` over each.

## Errors

- `wallet_not_connected` — connect a wallet (any mode supports
  Permit2 sign).
- `param_error` — invalid token / spender address, or unparseable
  amount.
- `send_failed` — signature was rejected by the user, or the underlying
  wallet bridge errored.
- `rpc_error` — `list` action couldn't read the on-chain allowance.

## Security model

- Every signed permit carries an expiration. Set short expirations
  for one-shot use; longer ones for recurring dApp interactions.
- The Permit2 contract is audited (Uniswap Labs + Trail of Bits).
- Revoking via this tool sets both the on-chain Permit2 allowance
  AND blocks all outstanding signatures (since the new nonce
  invalidates old permits).
