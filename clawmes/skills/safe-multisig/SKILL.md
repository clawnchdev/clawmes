---
name: safe-multisig
description: Gnosis Safe multisig — read info, list pending txs, propose / confirm signatures
metadata:
  hermes:
    tags: [crypto, defi, multisig, safe]
    category: clawmes
    requires_tools: [safe]
---

# Gnosis Safe Multisig

The `safe` tool talks to the Safe Transaction Service
(`safe.global`) — the public-good off-chain coordinator where
multisig signatures pool until the threshold is met.

## When to use

- User says: "show me our DAO Safe"
- User says: "what txs are pending on Safe 0xabc..."
- User says: "sign this Safe transaction"
- User says: "is the Safe at 0xabc... a multisig"

## `info` — read Safe metadata

```json
{"action": "info", "safe_address": "0xabc...", "chain_id": 1}
```

Returns:
- `owners` — array of signer addresses.
- `threshold` — how many signatures are needed (M-of-N).
- `nonce` — next tx nonce.
- `version` — Safe contract version (1.4.1, 1.3.0, etc.).
- `modules` — installed Safe modules.
- `fallback_handler`, `guard` — extension contracts.

## `pending` — list unexecuted transactions

```json
{"action": "pending", "safe_address": "0xabc...", "chain_id": 1}
```

Returns proposed txs awaiting signatures. Each entry has the
safe_tx_hash (the unique ID), to/value/data, current
confirmations_count vs. confirmations_required, and submission
date.

## `propose` / `confirm` — relay signatures

Both actions hit the same endpoint; the Service deduplicates. The
LLM / caller is responsible for building the EIP-712 payload (Safe's
`SafeTx` typed data with the right domain). After signing via
`sign_typed_data_v4`, submit the payload:

```json
{"action": "propose", "safe_address": "0xabc...", "payload": {
  "to": "0xtarget...",
  "value": "0",
  "data": "0x...",
  "operation": 0,
  "safeTxGas": 0,
  "baseGas": 0,
  "gasPrice": "0",
  "gasToken": "0x0000...",
  "refundReceiver": "0x0000...",
  "nonce": 5,
  "contractTransactionHash": "0x...",
  "sender": "0xowner...",
  "signature": "0x..."
}}
```

`confirm` is identical — just semantically "I'm signing an existing
proposal" rather than "I'm proposing new". The Service handles both.

## `execute` — broadcast a fully-signed multisig tx

Returns `not_implemented` at this milestone. Building
`execTransaction` calldata with all collected signatures requires
the Safe ABI integration which isn't fully wired. Use the Safe web
UI to execute, or extract the calldata manually from the
Transaction Service.

## Supported chains

Ethereum, Base, Arbitrum, Optimism, Polygon. Per the Safe Foundation's
public Transaction Service. Other chains return `unsupported_chain`.

## Common workflow

1. User says "I need to send 1 ETH from our DAO multisig".
2. LLM checks `info` to confirm Safe + show owners + threshold.
3. LLM proposes the tx (with the user's owner-signature). Safe Service
   stores it as pending.
4. Other owners use the Safe web UI (or their own tools) to add
   signatures.
5. Once threshold is met, anyone executes (Safe web UI, or this
   tool's `execute` once that lands).
