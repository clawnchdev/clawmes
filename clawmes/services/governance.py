"""Governance integrations — Snapshot (off-chain) + Tally (on-chain).

Snapshot is the canonical off-chain DAO voting platform — proposals
live in a "space" (per-DAO container), votes are signed messages
(EIP-712) submitted to the hub. Tally is the standard on-chain
governance interface — proposals are Compound-Bravo-style, votes are
on-chain transactions.

Endpoints:

  * **Snapshot GraphQL** — POST to https://hub.snapshot.org/graphql
    for queries (spaces, proposals, votes). Vote submission is a
    signed-message POST to /api/msg.
  * **Tally GraphQL** — POST to https://api.tally.xyz/query for
    DAO + proposal queries. ``TALLY_API_KEY`` required.

Vote submission requires the wallet mode's sign_typed_data_v4 path
(EIP-712). This service exposes the read paths; vote submission
lives in the tool layer where it has access to the wallet.
"""

from __future__ import annotations

import os
from typing import Any

from clawmes.lib.http import http_post
from clawmes.lib.logger import logger_for

_log = logger_for("services.governance")

_SNAPSHOT_BASE = "https://hub.snapshot.org"
_TALLY_BASE = "https://api.tally.xyz"


class GovernanceError(RuntimeError):
    """Raised on Snapshot / Tally API failures.

    ``code``:
      * ``not_found`` — space / proposal / DAO doesn't exist.
      * ``rate_limited`` — HTTP 429.
      * ``api_error`` — generic upstream failure.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def snapshot_query(query: str, variables: dict[str, Any]) -> dict[str, Any]:
    """Run a GraphQL query against Snapshot's hub. Returns the
    ``data`` payload directly; raises on errors / non-dict responses.
    """
    try:
        resp = http_post(
            f"{_SNAPSHOT_BASE}/graphql",
            json={"query": query, "variables": variables},
            timeout=15.0,
        )
    except Exception as exc:  # noqa: BLE001
        msg = str(exc).lower()
        if "429" in msg or "rate" in msg:
            raise GovernanceError("rate_limited", str(exc)) from exc
        raise GovernanceError("api_error", f"Snapshot request failed: {exc}") from exc

    if not isinstance(resp, dict):
        raise GovernanceError("api_error", "Snapshot returned non-dict response")
    if resp.get("errors"):
        msg = str(resp["errors"])
        if "not found" in msg.lower():
            raise GovernanceError("not_found", msg)
        raise GovernanceError("api_error", msg)
    return resp.get("data") or {}


def tally_query(query: str, variables: dict[str, Any]) -> dict[str, Any]:
    """Run a GraphQL query against Tally. Requires TALLY_API_KEY."""
    api_key = os.environ.get("TALLY_API_KEY")
    if not api_key:
        raise GovernanceError(
            "no_credentials",
            "TALLY_API_KEY required for Tally queries — get one at https://www.tally.xyz/api",
        )
    headers = {"Api-Key": api_key, "Content-Type": "application/json"}
    try:
        resp = http_post(
            f"{_TALLY_BASE}/query",
            json={"query": query, "variables": variables},
            headers=headers,
            timeout=15.0,
        )
    except Exception as exc:  # noqa: BLE001
        msg = str(exc).lower()
        if "429" in msg or "rate" in msg:
            raise GovernanceError("rate_limited", str(exc)) from exc
        raise GovernanceError("api_error", f"Tally request failed: {exc}") from exc

    if not isinstance(resp, dict):
        raise GovernanceError("api_error", "Tally returned non-dict response")
    if resp.get("errors"):
        raise GovernanceError("api_error", str(resp["errors"]))
    return resp.get("data") or {}


def submit_snapshot_vote(payload: dict[str, Any]) -> dict[str, Any]:
    """Submit a signed Snapshot vote to /api/msg.

    ``payload`` must include ``address``, ``msg`` (the JSON-stringified
    proposal/choice/space/etc.), and ``sig`` (EIP-712 signature). Caller
    is responsible for constructing this — see the snapshot.js source
    or https://docs.snapshot.org for the canonical shape.
    """
    try:
        resp = http_post(
            f"{_SNAPSHOT_BASE}/api/msg",
            json=payload,
            timeout=15.0,
        )
    except Exception as exc:  # noqa: BLE001
        raise GovernanceError("api_error", f"Snapshot vote submission failed: {exc}") from exc

    if isinstance(resp, dict):
        return resp
    return {"submitted": True}
