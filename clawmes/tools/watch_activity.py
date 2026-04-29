"""``watch_activity`` — track on-chain activity for an address.

Four actions:

  * ``watch``     — register an address for ongoing monitoring. Stored
    in ``${HERMES_HOME}/clawmes/watch/list.json`` and surfaced to the
    caller for polling. The actual notification dispatch is handled
    by Hermes' cron daemon (out of scope for this tool).
  * ``unwatch``   — remove an address from the watch list.
  * ``list``      — show currently watched addresses.
  * ``recent``    — fetch recent transactions for any address (one-shot
    read; doesn't require watch registration).

The persistent watch list is just a JSON file — Hermes' cron picks it
up via the existing plan_scheduler. This tool is the management
interface for the list itself.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from clawmes.lib.logger import logger_for
from clawmes.lib.params import read_int, read_str
from clawmes.lib.paths import hermes_home
from clawmes.lib.tool_result import error_result, json_result
from clawmes.tools.registry import read_tool, register_with_ctx

_log = logger_for("tools.watch_activity")

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["watch", "unwatch", "list", "recent"],
        },
        "address": {
            "type": "string",
            "description": "Wallet address to watch / fetch.",
        },
        "label": {
            "type": "string",
            "description": "Optional label for the watched address.",
        },
        "chain_id": {
            "type": "integer",
            "description": "Chain id (default 1).",
        },
        "limit": {
            "type": "integer",
            "description": "Max recent txs (default 25, max 100).",
        },
    },
    "required": ["action"],
}


def _watch_path() -> Path:
    return hermes_home() / "clawmes" / "watch" / "list.json"


def _load_list() -> list[dict[str, Any]]:
    p = _watch_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
    except (json.JSONDecodeError, OSError):
        return []
    return []


def _save_list(items: list[dict[str, Any]]) -> None:
    p = _watch_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(items, indent=2, default=str), encoding="utf-8")


@read_tool(
    name="watch_activity",
    toolset="clawmes-defi",
    description=(
        "Watch addresses for on-chain activity. watch / unwatch / list "
        "manage a persistent watch list (Hermes cron polls + notifies). "
        "recent fetches recent txs for any address one-shot."
    ),
    schema=_SCHEMA,
    emoji="\U0001f441\ufe0f",
)
def watch_activity(args: dict[str, Any], **kwargs: Any) -> str:
    action = read_str(args, "action", required=True)

    if action == "list":
        return _handle_list()
    if action == "watch":
        return _handle_watch(args)
    if action == "unwatch":
        return _handle_unwatch(args)
    return _handle_recent(args)


def _handle_list() -> str:
    items = _load_list()
    return json_result(
        {"count": len(items), "watched": items},
        summary=(
            f"Watching {len(items)} address(es):\n"
            + "\n".join(
                f"  • {i.get('label') or '(unlabeled)'}: {i.get('address')} "
                f"on chain {i.get('chain_id')}"
                for i in items
            )
        ),
    )


def _handle_watch(args) -> str:
    address = read_str(args, "address", required=True)
    if not address.startswith(("0x", "0X")) or len(address) != 42:
        return error_result(f"Invalid address: {address!r}", code="param_error")
    chain_id = read_int(args, "chain_id") or 1
    label = read_str(args, "label")

    items = _load_list()
    # Dedupe by (address, chain_id)
    key = (address.lower(), chain_id)
    items = [i for i in items if (i.get("address", "").lower(), i.get("chain_id")) != key]
    items.append(
        {
            "address": address,
            "chain_id": chain_id,
            "label": label,
        }
    )
    _save_list(items)
    return json_result(
        {"watched": items},
        summary=(f"Now watching {address} on chain {chain_id}" + (f" ({label})" if label else "")),
    )


def _handle_unwatch(args) -> str:
    address = read_str(args, "address", required=True)
    chain_id = read_int(args, "chain_id") or 1
    items = _load_list()
    before = len(items)
    items = [
        i
        for i in items
        if (i.get("address", "").lower(), i.get("chain_id")) != (address.lower(), chain_id)
    ]
    if len(items) == before:
        return error_result(
            f"{address} on chain {chain_id} is not in the watch list.",
            code="not_found",
        )
    _save_list(items)
    return json_result(
        {"watched": items, "removed": address},
        summary=f"Unwatched {address} on chain {chain_id}",
    )


def _handle_recent(args) -> str:
    from clawmes.services.explorer import ExplorerError, get_explorer_service

    address = read_str(args, "address", required=True)
    if not address.startswith(("0x", "0X")) or len(address) != 42:
        return error_result(f"Invalid address: {address!r}", code="param_error")
    chain_id = read_int(args, "chain_id") or 1
    limit = read_int(args, "limit") or 25

    explorer = get_explorer_service()
    try:
        # Use logs endpoint with address filter; explorer's get_logs
        # gives us recent activity. For full tx history we'd need
        # the txlist endpoint — not exposed yet.
        logs = explorer.get_logs(chain_id, address=address, offset=min(limit, 100))
    except ExplorerError as exc:
        return error_result(str(exc), code="explorer_error")
    return json_result(
        {
            "address": address,
            "chain_id": chain_id,
            "count": len(logs),
            "logs": logs[:limit],
        },
        summary=(f"{len(logs)} recent log(s) involving {address} on chain {chain_id}"),
    )


def register(ctx) -> None:
    """Wire ``watch_activity`` into Hermes."""
    register_with_ctx(ctx, watch_activity)
