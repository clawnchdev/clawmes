"""``farcaster`` — Farcaster social via Neynar API.

Five actions:

  * ``cast``           — post a new cast.
  * ``reply``          — reply to an existing cast.
  * ``search``         — search casts by query.
  * ``feed``           — get a user's recent casts.
  * ``notifications``  — list mentions / replies for the user.

Neynar (neynar.com) is the canonical Farcaster API — wraps the Hub
protocol with a clean REST interface. ``NEYNAR_API_KEY`` required;
free tier ~100 req/min.

Cast/reply submission requires a Farcaster signer UUID. Setting one
up is a multi-step OAuth-like flow at neynar.com; clawmes assumes
the user has already provisioned ``NEYNAR_SIGNER_UUID`` and stored
it in their env.
"""

from __future__ import annotations

import os
from typing import Any

from clawmes.lib.http import http_get, http_post
from clawmes.lib.logger import logger_for
from clawmes.lib.params import read_int, read_str
from clawmes.lib.tool_result import error_result, json_result
from clawmes.tools.registry import register_with_ctx, write_tool

_log = logger_for("tools.farcaster")

_NEYNAR_BASE = "https://api.neynar.com"

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["cast", "reply", "search", "feed", "notifications"],
        },
        "text": {
            "type": "string",
            "description": "Cast/reply body (max 320 chars).",
        },
        "parent_hash": {
            "type": "string",
            "description": "Parent cast hash for replies.",
        },
        "query": {
            "type": "string",
            "description": "Search query string.",
        },
        "fid": {
            "type": "integer",
            "description": "Farcaster ID for feed/notifications. Required for those.",
        },
        "limit": {
            "type": "integer",
            "description": "Max results (default 25, max 100).",
        },
        "channel": {
            "type": "string",
            "description": "Channel ID to post into (e.g. 'crypto').",
        },
        "policyConfirmationNonce": {
            "type": "string",
            "description": "Set when retrying after POLICY HOLD.",
        },
    },
    "required": ["action"],
}


@write_tool(
    name="farcaster",
    toolset="clawmes-defi",
    description=(
        "Farcaster social via Neynar API. cast/reply post new content "
        "(requires NEYNAR_SIGNER_UUID + NEYNAR_API_KEY); search / feed "
        "/ notifications are read-only. 320-char limit on cast text."
    ),
    schema=_SCHEMA,
    emoji="\U0001f7e3",
)
def farcaster(args: dict[str, Any], **kwargs: Any) -> str:
    action = read_str(args, "action", required=True)
    api_key = os.environ.get("NEYNAR_API_KEY")
    if not api_key:
        return error_result(
            "NEYNAR_API_KEY required. Get one at https://neynar.com.",
            code="no_credentials",
        )

    if action == "cast":
        return _handle_cast(args, api_key, parent=None)
    if action == "reply":
        parent = read_str(args, "parent_hash", required=True)
        return _handle_cast(args, api_key, parent=parent)
    if action == "search":
        return _handle_search(args, api_key)
    if action == "feed":
        return _handle_feed(args, api_key)
    return _handle_notifications(args, api_key)


def _handle_cast(args, api_key: str, parent: str | None) -> str:
    signer_uuid = os.environ.get("NEYNAR_SIGNER_UUID")
    if not signer_uuid:
        return error_result(
            "NEYNAR_SIGNER_UUID required to post casts. Set up a signer "
            "at https://docs.neynar.com.",
            code="no_credentials",
        )
    text = read_str(args, "text", required=True)
    if len(text) > 320:
        return error_result(f"Cast text exceeds 320-char limit ({len(text)})", code="param_error")

    payload: dict[str, Any] = {"signer_uuid": signer_uuid, "text": text}
    if parent is not None:
        payload["parent"] = parent
    channel = read_str(args, "channel")
    if channel:
        payload["channel_id"] = channel

    try:
        resp = http_post(
            f"{_NEYNAR_BASE}/v2/farcaster/cast",
            json=payload,
            headers={"api_key": api_key, "Content-Type": "application/json"},
            timeout=15.0,
        )
    except Exception as exc:  # noqa: BLE001
        return error_result(f"Neynar request failed: {exc}", code="api_error")

    if not isinstance(resp, dict):
        return error_result("Neynar non-dict response", code="api_error")
    cast = resp.get("cast") or {}
    return json_result(
        {"hash": cast.get("hash"), "thread": cast.get("thread_hash"), "raw": resp},
        summary=(f"{'Reply' if parent else 'Cast'} posted: {cast.get('hash', '?')}"),
    )


def _handle_search(args, api_key: str) -> str:
    query = read_str(args, "query", required=True)
    limit = read_int(args, "limit") or 25
    try:
        resp = http_get(
            f"{_NEYNAR_BASE}/v2/farcaster/cast/search",
            params={"q": query, "limit": str(min(limit, 100))},
            headers={"api_key": api_key},
            timeout=15.0,
        )
    except Exception as exc:  # noqa: BLE001
        return error_result(f"Neynar request failed: {exc}", code="api_error")
    if not isinstance(resp, dict):
        return error_result("Neynar non-dict response", code="api_error")
    casts = (resp.get("result") or {}).get("casts") or []
    return json_result(
        {"query": query, "count": len(casts), "casts": casts},
        summary=f"{len(casts)} cast(s) matching {query!r}",
    )


def _handle_feed(args, api_key: str) -> str:
    fid = read_int(args, "fid")
    if fid is None:
        return error_result("fid required for feed action", code="param_error")
    limit = read_int(args, "limit") or 25
    try:
        resp = http_get(
            f"{_NEYNAR_BASE}/v2/farcaster/feed/user/casts",
            params={"fid": str(fid), "limit": str(min(limit, 100))},
            headers={"api_key": api_key},
            timeout=15.0,
        )
    except Exception as exc:  # noqa: BLE001
        return error_result(f"Neynar request failed: {exc}", code="api_error")
    if not isinstance(resp, dict):
        return error_result("Neynar non-dict response", code="api_error")
    casts = resp.get("casts") or []
    return json_result(
        {"fid": fid, "count": len(casts), "casts": casts},
        summary=f"{len(casts)} recent cast(s) from FID {fid}",
    )


def _handle_notifications(args, api_key: str) -> str:
    fid = read_int(args, "fid")
    if fid is None:
        return error_result("fid required for notifications action", code="param_error")
    limit = read_int(args, "limit") or 25
    try:
        resp = http_get(
            f"{_NEYNAR_BASE}/v2/farcaster/notifications",
            params={"fid": str(fid), "limit": str(min(limit, 100))},
            headers={"api_key": api_key},
            timeout=15.0,
        )
    except Exception as exc:  # noqa: BLE001
        return error_result(f"Neynar request failed: {exc}", code="api_error")
    if not isinstance(resp, dict):
        return error_result("Neynar non-dict response", code="api_error")
    notifs = resp.get("notifications") or []
    return json_result(
        {"fid": fid, "count": len(notifs), "notifications": notifs},
        summary=f"{len(notifs)} notification(s) for FID {fid}",
    )


def register(ctx) -> None:
    """Wire ``farcaster`` into Hermes."""
    register_with_ctx(ctx, farcaster)
