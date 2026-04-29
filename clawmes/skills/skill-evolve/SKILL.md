---
name: skill-evolve
description: Propose and apply edits to clawmes' own skill bundles based on observed user patterns
metadata:
  hermes:
    tags: [meta, skills, agentic, self-improvement]
    category: clawmes
    requires_tools: [skill_evolve]
---

# Skill Evolve

Self-improvement workflow for clawmes' bundled skills. The user describes a recurring task pattern, you propose a skill update, and `skill_evolve` writes the proposal to disk for the user to review + apply.

## When to use

- "you keep forgetting that I always want X for Y. Save it." → `propose` an update to the relevant skill.
- "what skills could you improve based on the last few sessions?" → `list` proposals.
- "apply that change you suggested" → `update` with the proposal ID.
- "undo the last change to the swap skill" → `revert` to a prior version.

## Required parameters

- **`propose`**: `skill_name`, `change_description`. Optionally `before` / `after` for diff context.
- **`list`**: optional `skill_name` to scope. Returns pending proposals.
- **`update`**: `proposal_id`. Applies the proposal — creates a new skill version.
- **`revert`**: `skill_name`, optional `version` (default = previous).

## Common flows

### Capture a recurring user preference

1. User says: "I always want gas estimates in USD, not gwei. Stop showing me gwei."
2. Propose an edit to the `transfer` skill:
   ```
   skill_evolve(
     action="propose",
     skill_name="transfer",
     change_description="When showing gas estimates, default to USD (gas_estimate_usd) and omit raw wei/gwei unless explicitly requested. Update the 'Verification' section."
   )
   ```
3. Tell the user: "saved as proposal `prop-abc-123`. Run `/skills update prop-abc-123` when you want it applied."
4. Don't apply automatically — proposals are explicit user actions.

### Review pending proposals

1. `skill_evolve(action="list")` — show all pending changes.
2. Walk through them with the user one by one; let them approve / reject each.
3. For approved ones: `skill_evolve(action="update", proposal_id=<id>)`.

### Roll back a bad change

1. User says: "that skill change made things worse."
2. `skill_evolve(action="revert", skill_name=<name>)` — restores the prior version.
3. Confirm and explain what was reverted.

## Pitfalls

- **Never apply automatically**: proposals are explicit. Don't call `update` without the user's confirmation in the current turn. Self-modifying behavior without user buy-in is exactly the kind of thing that breaks user trust.
- **Don't propose for one-off requests**: if the user says "this once, do X", that's not a skill update — that's a one-time instruction. Only propose when you've seen the pattern across multiple turns or sessions.
- **Versioning**: each `update` creates a new version. The bundled skill (in the wheel) is read-only; user-applied edits live in `${HERMES_HOME}/clawmes/skills/<name>/`.
- **Conflict resolution**: if the user upgrades clawmes and the bundled skill changed, the user's local override wins for that skill until they explicitly reset. Surface the divergence at upgrade time so they can review.
- **Diff size**: keep proposed changes small — one section or paragraph per proposal. Large rewrites are hard to review and easy to revert wholesale even if 80% was good.

## Related tools

- `agent_memory` — the lightweight alternative for capturing preferences without a skill change.
- `session_recall` — useful as evidence when proposing a skill update ("we discussed this in session X").
