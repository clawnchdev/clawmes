---
name: governance
description: DAO voting via Snapshot (off-chain, free) and Tally (on-chain)
metadata:
  hermes:
    tags: [crypto, defi, governance, dao]
    category: clawmes
    requires_tools: [governance]
---

# DAO Governance

The `governance` tool covers both major governance platforms:

- **Snapshot** — off-chain voting via signed messages. Free (no gas).
  Most DAOs use this.
- **Tally** — on-chain voting (Compound Bravo style). Requires gas
  for each vote.

## When to use

- User says: "what's happening in the Aave DAO"
- User says: "show active proposals for ENS"
- User says: "vote yes on Optimism proposal X"
- User says: "list DAOs on Arbitrum"

## Snapshot: list active proposals

Most common path. Snapshot organizes by "space" (one space per DAO,
e.g. `aave.eth`, `ens.eth`, `gitcoindao.eth`, `arbitrumfoundation.eth`).

```json
{"action": "proposals", "space": "aave.eth", "state": "active"}
```

Returns up to 10 active proposals with title, body, voting period,
choices, current scores. Surface to the user; they can ask for a
specific one by index or title.

## Snapshot: read proposal detail

```json
{"action": "info", "proposal_id": "0x..."}
```

Returns the full proposal — body, choices, scores breakdown, author,
end timestamp.

## Snapshot: vote

Voting requires the wallet to sign an EIP-712 message off-chain.
The LLM must:

1. Build the EIP-712 typed-data payload (Snapshot's
   `Vote(proposal,choice,space,voter,timestamp,reason)` schema).
2. Get the wallet to sign via `sign_typed_data_v4`.
3. Submit the signed payload via `governance vote`.

```json
{"action": "vote", "payload": {"address": "0x...", "msg": "{...}", "sig": "0x..."}}
```

This is multi-step. If unsure how to build the payload, direct the
user to the Snapshot web UI for now and surface the proposal URL.

## Tally: list DAOs by chain

```json
{"action": "proposals", "backend": "tally", "chain_id": 1}
```

Requires `TALLY_API_KEY` env var.

## Errors

- `not_found` — Snapshot space doesn't exist. Confirm the spelling.
- `rate_limited` — surface and back off.
- `not_implemented` — Tally voting / proposal-detail isn't wired yet
  (use the Tally web UI).
- `param_error` — missing payload for vote action.

## Delegate

Returns `not_implemented`. Delegation is per-DAO governance-token
contract; build the calldata and use `transfer` (with `data=` set)
to invoke `delegate(address)` on the token contract directly.

## Example DAO spaces

- `aave.eth` — Aave
- `ens.eth` — ENS
- `arbitrumfoundation.eth` — Arbitrum
- `opcollective.eth` — Optimism Collective
- `gitcoindao.eth` — Gitcoin
- `uniswapgovernance.eth` — Uniswap
- `lido-snapshot.eth` — Lido
- `apecoin.eth` — ApeCoin
