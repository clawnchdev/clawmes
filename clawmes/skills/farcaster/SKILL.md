---
name: farcaster
description: Post, search, read Farcaster — via Neynar API
metadata:
  hermes:
    tags: [crypto, social, farcaster]
    category: clawmes
    requires_tools: [farcaster, nookplot]
---

# Farcaster Social

The `farcaster` tool talks to Neynar (the canonical Farcaster API
provider). Two related tools live alongside:

- `nookplot` — Farcaster creator analytics (who's getting engagement).
- `governance` — many DAOs vote via casts; cross-reference if needed.

## When to use

- User says: "cast 'gm' to my channel"
- User says: "reply to that cast"
- User says: "search Farcaster for 'eigenlayer'"
- User says: "show me my mentions"
- User says: "what's vitalik (fid 5650) been posting"

## Required env

- `NEYNAR_API_KEY` — for all actions. Sign up at neynar.com.
- `NEYNAR_SIGNER_UUID` — additional requirement for `cast` and
  `reply`. The signer is a multi-step OAuth-like setup at neynar.com
  that authorizes the API key to post on the user's behalf.

## Actions

### `cast` — post a new cast

```json
{"action": "cast", "text": "gm builders ☀️", "channel": "crypto"}
```

320-char hard limit (validated locally before submitting). Channel is
optional — omit to post to home / non-channel.

### `reply` — reply to an existing cast

```json
{"action": "reply", "text": "wagmi", "parent_hash": "0xabc..."}
```

Same flow as cast but with `parent_hash` set.

### `search` — find casts by query

```json
{"action": "search", "query": "ethereum", "limit": 25}
```

Returns matching casts (text content, author, timestamp).

### `feed` — get a user's recent casts by FID

```json
{"action": "feed", "fid": 5650, "limit": 10}
```

FIDs are integer Farcaster IDs. Find a user's FID at
[warpcast.com/<username>](https://warpcast.com).

### `notifications` — mentions / replies for a user

```json
{"action": "notifications", "fid": 5650, "limit": 25}
```

## Errors

- `no_credentials` — `NEYNAR_API_KEY` missing (or
  `NEYNAR_SIGNER_UUID` for write actions).
- `param_error` — text > 320 chars, missing fid for feed/notifs,
  missing parent_hash for reply.
- `api_error` — Neynar request failed.

## Rate limits

Free tier ~100 req/min — enough for personal use. Production / bot
traffic should pay for the appropriate tier at neynar.com.
