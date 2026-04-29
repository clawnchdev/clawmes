---
name: airdrop
description: Check airdrop eligibility and claim via Merkle distributor
metadata:
  hermes:
    tags: [crypto, defi, airdrop]
    category: clawmes
    requires_tools: [airdrop]
---

# Airdrops

The `airdrop` tool covers eligibility checking + claim execution
against the OpenZeppelin Merkle distributor pattern (used by ~95% of
airdrops).

## When to use

- User says: "am I eligible for the LDO airdrop"
- User says: "claim my Arbitrum airdrop"
- User says: "what airdrops can I claim"

## `eligibility` — query a per-airdrop API

Most airdrops have an eligibility endpoint that takes the wallet
address and returns `{eligible: true|false, amount: ..., proof: [...]}`
(or similar). The tool issues a GET with `?address=...` appended to
the user-provided URL.

```json
{"action": "eligibility", "endpoint": "https://drop.example.com/api/check"}
```

HTTPS only (security guard). The response shape varies per airdrop —
surface the raw payload to the user so they can interpret.

## `claim` — execute the Merkle distributor claim

```json
{"action": "claim", "distributor": "0xdistributor...", "index": 42, "amount": "1000000000000000000", "proof": ["0x...", "0x..."]}
```

Builds calldata for OZ's `claim(uint256 index, address account, uint256 amount, bytes32[] merkleProof)`. The proof comes from the eligibility step.

For airdrops with non-standard distributors, override via
`calldata` directly:

```json
{"action": "claim", "distributor": "0x...", "calldata": "0x..."}
```

## `list` — discovery

Returns `not_implemented`. Most airdrop aggregators (DefiLlama
Airdrops, Earnifi, Drops) require auth. Direct the user to those
sites for discovery; come back here to claim.

## Common workflow

1. User: "did I get the [airdrop] airdrop?"
2. LLM: looks up the airdrop's eligibility URL, calls
   `eligibility` with that endpoint.
3. If eligible: surface amount + extract index + proof from the
   response.
4. Confirm with user: "X tokens at distributor 0x... — claim?"
5. Call `claim` with the extracted parameters.

## Errors

- `wallet_not_connected` — connect first.
- `param_error` — non-HTTPS endpoint, missing index/amount/proof,
  or non-hex proof entries.
- `api_error` — eligibility endpoint failed.
- `send_failed` — claim tx rejected (already claimed, expired,
  proof mismatch, etc.).

## Security notes

- Always verify the distributor address against the airdrop's
  official source. Phishing via fake distributor contracts is common.
- The proof + index together prove the user is at the listed slot.
  If the API returns the wrong index (or you pass it wrong), the
  claim reverts.
- `unlimited` proofs aren't a thing — every Merkle leaf has a fixed
  amount.
