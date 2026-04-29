"""``governance`` — DAO voting via Snapshot + Tally.

Four actions:

  * ``proposals`` — list active proposals from a Snapshot space or
    Tally DAO. Defaults to Snapshot since most DAOs use it.
  * ``info``      — single-proposal detail.
  * ``vote``      — submit a signed Snapshot vote. Requires the wallet
    to sign an EIP-712 message; this tool relays the pre-built
    payload to /api/msg.
  * ``delegate``  — placeholder. On-chain delegation needs the
    governance-token contract's delegate() call which varies per DAO.
    Returns ``not_implemented`` with a hint to use the DAO's UI.

Snapshot is off-chain (free, signature-only); Tally proposals are
on-chain and require gas to vote. Both are widely used; the tool
defaults to Snapshot when ``backend`` isn't specified.
"""

from __future__ import annotations

from typing import Any

from clawmes.lib.logger import logger_for
from clawmes.lib.params import read_int, read_str
from clawmes.lib.tool_result import error_result, json_result
from clawmes.services.governance import (
    GovernanceError,
    snapshot_query,
    submit_snapshot_vote,
    tally_query,
)
from clawmes.tools.registry import register_with_ctx, write_tool

_log = logger_for("tools.governance")

_PROPOSALS_QUERY = """
query Proposals($space: String!, $state: String, $first: Int) {
  proposals(
    first: $first
    where: { space: $space, state: $state }
    orderBy: "created"
    orderDirection: desc
  ) {
    id
    title
    body
    state
    start
    end
    snapshot
    choices
    scores
    scores_total
    author
    space { id name }
  }
}
"""

_PROPOSAL_QUERY = """
query Proposal($id: String!) {
  proposal(id: $id) {
    id
    title
    body
    state
    start
    end
    choices
    scores
    scores_total
    author
    space { id name }
  }
}
"""

_TALLY_DAOS_QUERY = """
query Daos($chainId: ChainID!) {
  organizations(input: { filters: { chainIds: [$chainId] } }) {
    nodes {
      ... on Organization {
        id
        name
        slug
        chainIds
      }
    }
  }
}
"""

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["proposals", "info", "vote", "delegate"],
        },
        "backend": {
            "type": "string",
            "enum": ["snapshot", "tally"],
            "description": "Default snapshot.",
        },
        "space": {
            "type": "string",
            "description": "Snapshot space ID (e.g. 'aave.eth').",
        },
        "proposal_id": {
            "type": "string",
            "description": "Required for action=info or vote.",
        },
        "state": {
            "type": "string",
            "enum": ["active", "closed", "pending"],
            "description": "Filter for proposals action (default active).",
        },
        "limit": {
            "type": "integer",
            "description": "Max proposals (default 10).",
        },
        "chain_id": {
            "type": "integer",
            "description": "Chain id for Tally DAO listing.",
        },
        "payload": {
            "type": "object",
            "description": ("Pre-built signed Snapshot vote payload. Required for action=vote."),
        },
        "policyConfirmationNonce": {
            "type": "string",
            "description": "Set when retrying after POLICY HOLD.",
        },
    },
    "required": ["action"],
}


@write_tool(
    name="governance",
    toolset="clawmes-defi",
    description=(
        "DAO voting via Snapshot (off-chain) or Tally (on-chain). "
        "proposals lists active proposals; info returns detail; vote "
        "submits a signed Snapshot vote (the LLM/caller pre-builds the "
        "EIP-712 payload). delegate is a stub — varies per DAO."
    ),
    schema=_SCHEMA,
    emoji="\U0001f5f3\ufe0f",
)
def governance(args: dict[str, Any], **kwargs: Any) -> str:
    action = read_str(args, "action", required=True)
    backend = read_str(args, "backend") or "snapshot"

    if action == "proposals":
        return _handle_proposals(args, backend)
    if action == "info":
        return _handle_info(args, backend)
    if action == "vote":
        return _handle_vote(args, backend)
    return error_result(
        "Delegate is DAO-specific and not yet implemented. Most DAOs "
        "expose a delegate(address) call on their governance token "
        "contract — call it via /transfer with the encoded calldata.",
        code="not_implemented",
    )


def _handle_proposals(args, backend: str) -> str:
    if backend == "snapshot":
        space = read_str(args, "space", required=True)
        state = read_str(args, "state") or "active"
        limit = read_int(args, "limit") or 10
        try:
            data = snapshot_query(
                _PROPOSALS_QUERY,
                {"space": space, "state": state, "first": limit},
            )
        except GovernanceError as exc:
            return error_result(exc.message, code=exc.code)
        proposals = data.get("proposals") or []
        return json_result(
            {
                "backend": "snapshot",
                "space": space,
                "state": state,
                "count": len(proposals),
                "proposals": proposals,
            },
            summary=(
                f"{len(proposals)} {state} proposal(s) in {space}\n"
                + "\n".join(f"  • {p.get('title', '')[:80]}" for p in proposals[:5])
            ),
        )

    # Tally backend — DAO listing rather than per-DAO proposals
    chain_id = read_int(args, "chain_id") or 1
    try:
        data = tally_query(_TALLY_DAOS_QUERY, {"chainId": f"eip155:{chain_id}"})
    except GovernanceError as exc:
        return error_result(exc.message, code=exc.code)
    orgs = (data.get("organizations") or {}).get("nodes") or []
    return json_result(
        {
            "backend": "tally",
            "chain_id": chain_id,
            "count": len(orgs),
            "daos": orgs,
        },
        summary=f"{len(orgs)} DAO(s) on chain {chain_id} via Tally",
    )


def _handle_info(args, backend: str) -> str:
    if backend != "snapshot":
        return error_result(
            "Tally proposal info not yet wired; use action=proposals "
            "for now or supply a Snapshot proposal_id.",
            code="not_implemented",
        )
    proposal_id = read_str(args, "proposal_id", required=True)
    try:
        data = snapshot_query(_PROPOSAL_QUERY, {"id": proposal_id})
    except GovernanceError as exc:
        return error_result(exc.message, code=exc.code)
    proposal = data.get("proposal")
    if not proposal:
        return error_result(f"Proposal {proposal_id!r} not found", code="not_found")
    return json_result(
        {"proposal": proposal},
        summary=(
            f"{proposal.get('title', '')}\n"
            f"  Space:  {(proposal.get('space') or {}).get('name', '?')}\n"
            f"  State:  {proposal.get('state')}\n"
            f"  Author: {proposal.get('author')}"
        ),
    )


def _handle_vote(args, backend: str) -> str:
    if backend != "snapshot":
        return error_result(
            "Tally on-chain voting requires building governor.castVote "
            "calldata — use /transfer with the encoded calldata.",
            code="not_implemented",
        )
    payload = args.get("payload")
    if not isinstance(payload, dict):
        return error_result(
            "vote requires a 'payload' dict with the signed Snapshot "
            "message. The wallet's sign_typed_data_v4 builds it; see "
            "https://docs.snapshot.org for the EIP-712 schema.",
            code="param_error",
        )
    try:
        result = submit_snapshot_vote(payload)
    except GovernanceError as exc:
        return error_result(exc.message, code=exc.code)
    return json_result(
        {"backend": "snapshot", "result": result},
        summary="Snapshot vote submitted",
    )


def register(ctx) -> None:
    """Wire ``governance`` into Hermes."""
    register_with_ctx(ctx, governance)
