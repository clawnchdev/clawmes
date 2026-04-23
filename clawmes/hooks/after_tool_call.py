"""``post_tool_call`` hook — observer for completed tool calls.

Side effects layered here, in order:

  1. **Tx ledger append** — every write tool's tx hash + action context
     logged. Reads land here too if their result is structurally
     write-shaped, but the WRITE_TOOL_NAMES filter keeps reads out of
     the ledger.
  2. **Cost-basis ingest** — swap results auto-record to FIFO ledger.
     (TODO v0.2.0)
  3. **Budget tracking** — per-tool + per-session cost roll-up.
     (TODO v0.2.0)
  4. **Onboarding step advance** — the onboarding state machine reads
     ``post_tool_call`` events to know when a setup tool ran.
     (TODO v0.2.0)
  5. **Skill evolution nudges** — if a session has used the same tool
     5+ times with similar args, prompt the user about creating a
     macro / saved query. (TODO v0.2.0)
"""

from __future__ import annotations

import json
from typing import Any

from clawmes.ledger.tx_ledger import record_tx
from clawmes.lib.logger import logger_for
from clawmes.tools.registry import WRITE_TOOL_NAMES

_log = logger_for("hooks.post_tool_call")


def callback(
    *,
    tool_name: str,
    args: dict[str, Any] | None,
    result: str | None,
    duration_ms: float | None = None,
    error: BaseException | None = None,
    **kwargs: Any,
) -> None:
    """Pure observer — Hermes ignores the return value.

    Parses the result envelope and, for write tools that succeeded,
    appends a record to the tx ledger. Failures and read-only tools
    are skipped.
    """
    if error is not None:
        _log.warning(
            "tool %s failed in %.0fms: %s",
            tool_name,
            duration_ms or 0,
            error,
        )
        return

    _log.debug("tool %s completed in %.0fms", tool_name, duration_ms or 0)

    # 1. Tx ledger append — only writes, only successes
    if tool_name in WRITE_TOOL_NAMES:
        _record_to_ledger(tool_name, args or {}, result, kwargs)


def _record_to_ledger(
    tool_name: str,
    args: dict[str, Any],
    result: str | None,
    kwargs: dict[str, Any],
) -> None:
    """Parse a tool result envelope and append a ledger record.

    Quietly skips records the result envelope marks as ``isError``, so
    a write tool that returned an error envelope (e.g. policy_block)
    does NOT pollute the ledger.

    The result envelope shape is documented in
    :mod:`clawmes.lib.tool_result`.
    """
    parsed = _parse_result(result)
    if parsed is None:
        # Couldn't parse — log and skip rather than crash the hook.
        _log.debug("post_tool_call: result for %s was not parseable JSON", tool_name)
        return
    if parsed.get("isError"):
        _log.debug("post_tool_call: skipping %s (isError envelope)", tool_name)
        return

    details = parsed.get("details") or {}
    if not isinstance(details, dict):
        details = {}

    try:
        record_tx(
            session_id=str(kwargs.get("session_id") or kwargs.get("session_key") or ""),
            user_id=str(kwargs.get("user_id") or "default"),
            tool_name=tool_name,
            action_args=_redact_nonce(args),
            tx_hash=details.get("tx_hash"),
            chain_id=details.get("chain_id"),
            from_addr=details.get("from_addr"),
            to_addr=details.get("to_addr") or details.get("resolved_address"),
            value_wei=details.get("value_wei"),
            status=details.get("status", "submitted"),
        )
    except Exception:
        # Ledger writes should never break the agent loop. A disk-full
        # condition logs but the LLM still gets its tool result.
        _log.exception("post_tool_call: ledger append failed for %s", tool_name)


def _parse_result(result: str | None) -> dict[str, Any] | None:
    if not result:
        return None
    try:
        out = json.loads(result)
    except json.JSONDecodeError:
        return None
    if not isinstance(out, dict):
        return None
    return out


def _redact_nonce(args: dict[str, Any]) -> dict[str, Any]:
    """Strip ``policyConfirmationNonce`` from logged args.

    The nonce is a single-use credential — preserving it in the ledger
    is both unnecessary (it's already consumed) and a minor information-
    leak risk.
    """
    if "policyConfirmationNonce" not in args:
        return args
    return {k: v for k, v in args.items() if k != "policyConfirmationNonce"}
