---
name: clawnch-launch
description: Deploy a token on Base via the Clawnch launchpad using the /launch command or clawnch_launch tool. Every launch requires a verified 1M+ $CLAWNCH burn (/burn). Walks users through burn -> register-agent -> sign captcha -> deploy via Clanker.
tags: [clawnch, launchpad, deploy, token, agent, base, clanker, burn]
---

# clawmes:clawnch-launch

Clawnch is the agent-exclusive token launchpad on Base. clawmes wires
two surfaces:

* **`clawnch_launch` tool** — LLM-callable. Deploy a token + read
  launch metadata.
* **`/launch` slash command** — guided multi-turn flow for users
  who prefer step-by-step.

Plus **`/register_agent`** to obtain the API key required for deploys.

## When to reach for this skill

* User wants to deploy a token, launch a memecoin, or "ship a coin".
* User asks "how do I create a token on Clawnch?".
* User mentions Clanker, Uniswap V4 launches on Base.
* User wants to read fee accrual or volume on a Clawnch-launched
  token.
* User got a rate-limit error and asks about the bypass.
* User got a `burn_required` error or asks what a launch costs.

## Launch cost: the mandatory $CLAWNCH burn

**Every launch requires a verified burn of >= 1,000,000 $CLAWNCH**
(token `0xa1F72459dfA10BAD200Ac160eCd78C6b77a747be`) from the
launching wallet to the dead address
(`0x000000000000000000000000000000000000dEaD`) on Base, within 24h
of the deploy. Deploys without one are rejected with
`burn_required` (HTTP 402) — the error's `meta` carries
`minBurnTokens` + `burnAddress`.

The same burn claims the Clanker vault allocation as a bonus:
1M = 1% vault, scaling linearly to 10M = 10% (the Clanker max).
The minimum burn is never wasted.

Flow: `/burn 1000000` (signs + submits the transfer from the active
wallet) -> wait for confirmation -> `/launch burn <tx_hash>` (or it
auto-attaches in the same draft via `/launch burn <amount>`) ->
`/launch confirm`.

The burn is an irreversible spend — state this explicitly and get a
separate confirmation for the burn before submitting it. Never bundle
the burn confirmation into the deploy confirmation.

## How a deploy actually happens

End-to-end flow when the user runs `/launch confirm`:

```
1. Clawmes -> POST clawn.ch/api/deploy  (with CLAWNCH_API_KEY)
       body: { tokenParams: {name, symbol, description, source: "clawmes"}, burnTxHash, bypassTxHash? }
2. Clawnch returns a captcha challenge:
       { challengeId, message, nonce, contractAddress, storageSlot, deadline }
3. Clawmes solves the challenge:
       - sign `message` with active wallet (personal_sign)
       - read `storageSlot` on `contractAddress` via Base RPC
       - compute keccak256(encodePacked(signature, nonce, storageValue))
4. Clawmes -> POST clawn.ch/api/deploy/confirm
       body: { challengeId, solution: {signature, storageValue, proof}, tokenParams }
5. Clawnch's deployer wallet (server-side) submits the Clanker tx,
   indexes it, returns { txHash, tokenAddress }.
```

Custodial gas: Clawnch's deployer wallet pays gas + submits the
Clanker call. The user's wallet only signs the off-chain captcha.

## Requirements

* A verified 1,000,000+ $CLAWNCH burn from the launching wallet
  (see "Launch cost" above). Required for **every** deploy path —
  custodial, non-custodial, and export.
* `CLAWNCH_API_KEY` env var (custodial path only). Issued via
  `/register_agent` (two-step signed flow against
  `clawn.ch/api/agents/register` + `/api/agents/verify`).
* An active wallet connected to clawmes (WC / local-key / Bankr).
  The wallet signs the captcha message; it doesn't pay deploy gas.
* Base RPC reachable (any chain `8453` endpoint — public or paid).

## Rate limits + bypass

Custodial deploys: 1 per 24h per agent. If hit, the deploy returns a
`rate_limited` error with the bypass instructions.

Bypass: send `>= 0.005 ETH` (current default) to the Clawnch bypass
recipient on Base, then re-run with `bypass_tx_hash=<hash>` (or
`/launch bypass <hash>` and `/launch confirm`). Single-use; must be
< 24h old. The bypass covers the cooldown only — it does not replace
the mandatory burn.

## Source attribution

Every deploy from clawmes includes `source: "clawmes"` in the
`tokenParams` body. Clawnch surfaces this as a "via clawmes" badge on
the public launch detail page. Observable + intentional.

## Slash commands

| Command | Purpose |
|---|---|
| `/register_agent <name> | <description>` | Two-step register + verify; returns the API key for `~/.hermes/.env`. |
| `/launch` | Show usage + current draft. |
| `/launch name <token name>` | Step 1: set name. |
| `/launch symbol <TICKER>` | Step 2: set symbol. |
| `/launch description <text>` | Optional one-liner. |
| `/launch image <url>` | Token image URL. |
| `/launch twitter <handle\|url>` | X / Twitter (alias: `/launch x`). Accepts `@handle`, `handle`, or full URL. |
| `/launch website <url>` | Website URL. |
| `/launch telegram <handle\|url>` | Telegram handle or invite URL. |
| `/launch farcaster <handle\|url>` | Farcaster handle or Warpcast URL. |
| `/launch discord <url>` | Discord invite URL. |
| `/burn <amount>` | Sign + submit the required $CLAWNCH burn (min 1,000,000). |
| `/launch burn <amount\|tx_hash>` | Attach the required burn to the draft (signs it if given an amount). |
| `/launch bypass <tx_hash>` | Optional: skip 24h cooldown (custodial only; does not replace the burn). |
| `/launch status` | Show current draft. |
| `/launch confirm` | Deploy. |
| `/launch cancel` | Clear draft. |

Social handles are normalized — `/launch twitter clawnchbot` becomes `https://x.com/clawnchbot`. Full URLs pass through unchanged. The launchpad stores them as `tokenParams.metadata.socialMediaUrls` and may render them as badges on the launch detail page.

## Tool actions

### `clawnch_launch`

| Action | Returns |
|---|---|
| `deploy` | `{txHash, tokenAddress, ...}` on success. Requires `name`, `symbol`, and `burn_tx_hash` (the verified 1M+ $CLAWNCH burn — deploys without it are rejected with `burn_required`); accepts `description`, `image`, `twitter`, `website`, `telegram`, `farcaster`, `discord`, `bypass_tx_hash`. |
| `info` | Launch metadata for a deployed token. Requires `token` (address). |

Each social arg is normalized the same way the slash command does — bare handles become full platform URLs, full URLs pass through. Pass `discord` and `website` as complete URLs.

### `clawnch_fees`

| Action | Returns |
|---|---|
| `my_launches` | List of launches by the authenticated agent. |
| `launch_info` | Per-token launch detail (price, volume, fee accrual). |

## Recovery + edge cases

* **`burn_required`**: the deploy was attempted without a verified
  burn. Run `/burn 1000000` (or `/launch burn 1000000`), wait for the
  tx to confirm, then re-run `/launch confirm`. If the user already
  burned externally, attach the hash with `/launch burn <tx_hash>`.
* **`invalid_burn`**: the supplied hash failed verification — wrong
  sender, wrong recipient, amount below 1M, or older than 24h. The
  error message says which. A fresh burn from the launching wallet
  fixes it.
* **No wallet connected**: `/launch confirm` returns "No wallet
  connected. Run /connect first." Connect, then re-run confirm.
* **CLAWNCH_API_KEY missing**: deploy returns `no_credentials`. Run
  `/register_agent`, save the returned key to `~/.hermes/.env`,
  restart Hermes so the service picks it up.
* **Rate limited**: deploy returns `rate_limited` with bypass
  instructions. Send ETH to the bypass recipient, then
  `/launch bypass <tx> && /launch confirm`.
* **Challenge expired**: the captcha is 5-second windowed. If
  network latency caused the expiry, just re-run `/launch confirm`
  to start a fresh challenge.
* **Wallet signing failed**: WalletConnect bridge dropped or local
  keystore unlocked failed. Reconnect via `/connect` (WC) or
  `/connect_local` (local key).

## What this skill does NOT do

* It does not pay launchpad gas (Clawnch's deployer wallet does).
* It does not custody the launched token's mnemonic — Clanker
  emits the token contract directly; ownership is set per Clanker's
  rules (creator = the agent's registered wallet).
* It does not provide LP-fee claim transactions (Clanker handles
  fee accrual via FeeLocker; `clawnch_fees` reads metadata only).
* It does not waive the launch burn. Every deploy requires the
  verified 1M+ $CLAWNCH burn — there is no free-launch path. The
  API key itself is free to obtain via `/register_agent`.
