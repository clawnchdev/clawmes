---
name: nft
description: NFT operations — read collection / token data + mint / transfer / burn
metadata:
  hermes:
    tags: [crypto, nft, erc721]
    category: clawmes
    requires_tools: [nft, clawnchconnect]
---

# NFTs

The `nft` tool covers both read paths (Reservoir API) and write paths
(ERC-721 calldata).

## When to use

- User says: "show my NFTs on Base"
- User says: "what's the floor for BAYC"
- User says: "transfer my NFT #1234 to alice.eth"
- User says: "burn my Beanz #5"
- User says: "mint from this contract"

## Read actions (no wallet needed)

### `holdings` — what's in a wallet

```json
{"action": "holdings", "owner": "0xabc...", "chain_id": 1}
```

Defaults to the connected wallet's address if `owner` omitted. Returns
up to 50 tokens (override with `limit`, max 200). Each entry has
collection metadata + token id + ownership info.

### `info` — single token detail

```json
{"action": "info", "contract": "0xboredApes...", "token_id": "1234"}
```

Returns token metadata (name, image, attributes), current owner,
last sale price, top bid.

### `floor` — collection floor + 24h volume

```json
{"action": "floor", "contract": "0xboredApes..."}
```

Returns floor price in ETH, 24h volume, owner count, total supply.

## Write actions (wallet required)

### `transfer` — send an NFT

```json
{"action": "transfer", "contract": "0xnft...", "to": "0xrecipient...", "token_id": "42"}
```

Uses `safeTransferFrom`. The `to` field must be a 0x address — ENS
isn't supported here yet; resolve via the `transfer` tool first if
the user says a `.eth` name.

### `burn` — destroy an NFT

```json
{"action": "burn", "contract": "0xnft...", "token_id": "42"}
```

Sends to the `0xdEaD...dEaD` address (the OpenZeppelin convention
for "burn" since most NFTs don't expose a real `burn()` function).

### `mint` — call mint() on a contract

```json
{"action": "mint", "contract": "0xnft...", "value_wei": "1000000000000000000"}
```

By default uses the empty-arg `mint()` selector. For custom mint
signatures (common with allowlist / proof / etc.) pass an explicit
`calldata` arg pre-encoded by the user / LLM.

## Reservoir backend

All read actions use the Reservoir API. `RESERVOIR_API_KEY` is
optional; the free tier works for personal use. Per-chain hostnames
for Ethereum, Base, Arbitrum, Optimism, Polygon.

## Errors to know

- `unsupported_chain` — Reservoir not deployed for that chain.
- `not_found` — token / collection doesn't exist on Reservoir.
- `wallet_not_connected` — only for write actions.
- `param_error` — bad address or token_id format.
