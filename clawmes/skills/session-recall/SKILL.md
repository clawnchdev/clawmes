---
name: session-recall
description: Search past chat sessions for prior context, decisions, and discussion
metadata:
  hermes:
    tags: [memory, sessions, history, search]
    category: clawmes
    requires_tools: [session_recall]
---

# Session Recall

Search the user's past chat sessions for context the current session might be missing. Different from `agent_memory` — that's curated key-value storage; this is full-text search across raw transcripts.

## When to use

- "what did we decide about the bridge to Optimism?" → `search` with the topic.
- "summarize what I worked on last week" → `summarize` with a date range.
- "what was the last thing I asked about Aave?" → `recent` filtered by keyword.

## Required parameters

- **`search`**: `query` (text). Optional `limit` (default 10).
- **`summarize`**: optional `since` (ISO date or relative like "1 week ago").
- **`recent`**: optional `limit`, optional keyword filter.

## Common flows

### Recover context after a `/reset`

User just did `/reset` and wants to pick up where they left off:

1. `session_recall(action="recent", limit=3)` — show the last 3 sessions' headlines.
2. Ask the user which session they want to continue from.
3. If they pick one, `session_recall(action="summarize", session_id=<id>)` for a quick refresh.

### Find a prior decision

User asks "didn't we already talk about the GMX vs dYdX trade?":

1. `session_recall(action="search", query="GMX dYdX")`.
2. Surface matching excerpts with timestamps.
3. Pick up from where the prior session left off if the user wants.

### Weekly review

User wants to know what they spent time on:

1. `session_recall(action="summarize", since="1 week ago")`.
2. Surface a high-level breakdown — most-discussed tokens, most-used tools, recurring questions.

## Pitfalls

- **Sessions ≠ memory**: a session transcript isn't curated. The user said things they didn't mean, asked questions they later got answers to elsewhere. Don't treat past-session content as ground truth — confirm with the user before acting.
- **Search relevance**: text-search is keyword-based, not semantic. "ETH staking" won't find a session that talked about "Lido" without saying "ETH staking" explicitly. Try multiple queries.
- **Privacy considerations**: past sessions may contain addresses, balances, intents the user no longer wants surfaced. Confirm before quoting any address from a recall back into the current session.
- **Volume**: heavy users have many sessions. Default `limit` is 10; use a stricter limit if the chat surface is constrained (Slack threads, Telegram).

## Related tools

- `agent_memory` — for explicitly-saved facts that should be authoritative.
- `skill_evolve` — for capturing patterns across sessions into reusable skills.
