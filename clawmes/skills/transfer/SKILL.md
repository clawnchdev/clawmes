---
name: transfer
description: Send ETH or ERC-20 tokens to a recipient (address or ENS name)
metadata:
  hermes:
    tags: [crypto, evm, wallet, transfer]
    category: clawmes
    requires_tools: [transfer]
---

# Transfer Tokens

Use the `transfer` tool to send ETH or any ERC-20 token to another address or ENS name.

## When to use

- User says: "send 0.5 ETH to vitalik.eth"
- User says: "transfer 100 USDC to alice"
- User says: "pay <name> for <thing>"

## Required parameters

- `action` — `send` or `estimate`
- `to` — recipient address (`0x…`) or ENS name (`alice.eth`)
- `amount` — human-readable units (e.g. `"0.5"`)
- `token` — ERC-20 contract address; **omit for native ETH/MATIC/etc**

## Common flows

### Send ETH to an ENS name

1. Call `transfer(action="estimate", to="vitalik.eth", amount="0.5")` first to see the gas estimate and resolved address.
2. Show the user the action summary: from → to, amount, token, estimated gas in USD.
3. Wait for explicit user confirmation in chat.
4. Call `transfer(action="send", to="vitalik.eth", amount="0.5")`.
5. If the policy gate returns `POLICY HOLD`, relay the confirmation request to the user. Once confirmed, retry with the `policyConfirmationNonce` parameter.
6. Report the transaction hash + a block-explorer link (use `block_explorer` tool if needed).

### Send an ERC-20

Same as above, but pass `token=` with the ERC-20 contract address. Common token addresses:

- USDC on Base: `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913`
- USDC on Ethereum: `0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48`
- WETH on Base: `0x4200000000000000000000000000000000000006`

For unknown symbols, prefer asking the user for the contract address or use the `defi_price` tool to look one up.

## Pitfalls

- **Wrong chain**: ENS resolves on mainnet. If the user is on Base, an ENS name still works because clawmes resolves on mainnet first, then resolves the address on the active chain — but the *target address must hold value on the active chain*. Confirm with the user if unclear.
- **Decimals**: USDC is 6 decimals, ETH is 18. The `amount` parameter is in human units; clawmes handles base-unit conversion internally.
- **Insufficient balance**: `estimate` action will surface this before sending. Always estimate first for non-trivial amounts.
- **Reentrant clicks**: clawmes serializes write transactions, so a "send" while one is pending will queue. Tell the user if there's a pending tx (use `/pending` or `/tx`).

## Verification

- `estimate` returns `details.gas_estimate_wei`, `details.gas_estimate_usd`, `details.resolved_address`.
- `send` returns `details.tx_hash`, `details.block_number` (when receipt available).
- Cross-check by suggesting the user run `/tx <hash>` or click the explorer link.

## Related tools

- `defi_balance` — check current balance before sending
- `defi_price` — convert USD amounts to token amounts
- `block_explorer` — view tx receipt details
- `approvals` — needed before sending ERC-20s through a contract (transfer itself doesn't need approvals; swaps via `defi_swap` do)
