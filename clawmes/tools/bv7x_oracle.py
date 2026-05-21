"""``bv7x_oracle`` — BV-7X signal + on-chain attestation surface.

Companion to :mod:`bv7x` (agent / A2A / commerce) and
:mod:`bv7x_market` (raw BTC data). This tool covers the signal,
track-record, and on-chain attestation read paths plus the
token-gated premium endpoints (when ``BV7X_API_KEY`` is set).

Actions:

  Free / public:
    * ``scorecard``        — prediction history + accuracy + streak.
    * ``signal_metadata``  — current signal metadata (direction is
      GATED for non-holders; everything else is free).
    * ``onchain_latest``   — latest on-chain prediction attestation.
    * ``onchain_history``  — paginated attestation history.
    * ``onchain_stats``    — aggregate on-chain attestation stats.
    * ``verify_uid``       — verify a specific attestation by UID via
      BV-7X's verifier (complement to clawmes' generic
      :mod:`eas_attestation` tool which reads the EAS contract on
      Base directly).

  Token-gated (require ``BV7X_API_KEY`` set after wallet-verify):
    * ``oracle``           — basic oracle signal (500M $BV7X).
    * ``oracle_premium``   — full breakdown (1B $BV7X).
    * ``copy_trade_next``  — next trade intent (1B $BV7X).
    * ``copy_trade_history`` — recent trade intents + outcomes (1B $BV7X).

Read-only by design — ``@read_tool`` skips the policy gate. None of
these endpoints mutate on-chain or wallet state.
"""

from __future__ import annotations

from typing import Any

from clawmes.lib.params import ParamError, read_enum, read_int, read_str
from clawmes.lib.tool_result import error_result, json_result
from clawmes.services.bv7x import BV7XError, get_bv7x_service
from clawmes.tools.registry import read_tool, register_with_ctx

_VALID_ACTIONS = [
    "scorecard",
    "signal_metadata",
    "onchain_latest",
    "onchain_history",
    "onchain_stats",
    "verify_uid",
    "oracle",
    "oracle_premium",
    "copy_trade_next",
    "copy_trade_history",
]


_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": _VALID_ACTIONS,
        },
        "horizon": {
            "type": "integer",
            "description": (
                "Prediction horizon in days for action=scorecard "
                "(default 7). For signal_metadata, pass horizon_str "
                "with '2d'/'3d'/'7d'."
            ),
        },
        "horizon_str": {
            "type": "string",
            "description": (
                "Prediction horizon for action=signal_metadata ('2d' / '3d' / '7d', default '7d')."
            ),
        },
        "limit": {
            "type": "integer",
            "description": "Page size for action=onchain_history (default 10).",
        },
        "uid": {
            "type": "string",
            "description": ("32-byte EAS attestation UID for action=verify_uid. Hex, 0x-prefixed."),
        },
    },
    "required": ["action"],
}


@read_tool(
    name="bv7x_oracle",
    toolset="clawmes-intelligence",
    description=(
        "BV-7X signal + on-chain attestation surface. Free: scorecard, "
        "signal_metadata, onchain_latest, onchain_history, onchain_stats, "
        "verify_uid. Premium ($BV7X-gated, requires BV7X_API_KEY): "
        "oracle, oracle_premium, copy_trade_next, copy_trade_history. "
        "Token gating is enforced by BV-7X on-chain; clawmes only "
        "forwards the session token."
    ),
    schema=_SCHEMA,
    emoji="\U0001f52e",  # 🔮
)
def bv7x_oracle(args: dict[str, Any], **kwargs: Any) -> str:
    try:
        action = read_enum(args, "action", _VALID_ACTIONS, required=True)
    except ParamError as exc:
        return error_result(str(exc), code="param_error")

    svc = get_bv7x_service()
    try:
        if action == "scorecard":
            horizon = read_int(args, "horizon") or 7
            data = svc.get_scorecard(horizon=horizon)
            return json_result(data, summary=_format_scorecard(data))
        if action == "signal_metadata":
            horizon_str = read_str(args, "horizon_str") or "7d"
            data = svc.get_signal_metadata(horizon=horizon_str)
            return json_result(data, summary=_format_signal_metadata(data))
        if action == "onchain_latest":
            data = svc.get_onchain_latest()
            return json_result(data, summary=_format_onchain_latest(data))
        if action == "onchain_history":
            limit = read_int(args, "limit") or 10
            data = svc.get_onchain_history(limit=limit)
            count = len(data.get("attestations", []) or data.get("items", []) or [])
            return json_result(data, summary=f"{count} on-chain attestation(s)")
        if action == "onchain_stats":
            data = svc.get_onchain_stats()
            return json_result(data, summary=_format_onchain_stats(data))
        if action == "verify_uid":
            try:
                uid = read_str(args, "uid", required=True)
            except ParamError as exc:
                return error_result(str(exc), code="param_error")
            assert uid is not None
            data = svc.verify_onchain_attestation(uid.strip())
            valid = bool(data.get("valid"))
            return json_result(
                data,
                summary=("attestation VALID" if valid else "attestation INVALID"),
            )
        if action == "oracle":
            data = svc.get_oracle()
            return json_result(data, summary=_format_oracle(data))
        if action == "oracle_premium":
            data = svc.get_oracle_premium()
            return json_result(data, summary=_format_oracle(data, premium=True))
        if action == "copy_trade_next":
            data = svc.get_copy_trade_next()
            return json_result(data, summary=_format_copy_trade(data, kind="next"))
        # action == "copy_trade_history" — the only remaining option.
        data = svc.get_copy_trade_history()
        return json_result(data, summary=_format_copy_trade(data, kind="history"))
    except BV7XError as exc:
        return error_result(exc.message, code=exc.code)


# --- summary formatters -------------------------------------------------


def _format_scorecard(data: dict[str, Any]) -> str:
    summary = data.get("summary") or {}
    total = summary.get("totalPredictions") or summary.get("total") or "?"
    accuracy = summary.get("accuracy") or summary.get("dedupedAccuracy") or "?"
    streak = summary.get("streak") or {}
    streak_text = (
        f"{streak.get('count', '?')}{(streak.get('type') or '?')[0:1]}"
        if isinstance(streak, dict)
        else "?"
    )
    return f"BV-7X scorecard: {total} prediction(s), accuracy {accuracy}% (streak {streak_text})"


def _format_signal_metadata(data: dict[str, Any]) -> str:
    signal = data.get("signal") or "?"
    ctx = data.get("market_context") or {}
    price = ctx.get("btc_price")
    fg = ctx.get("fear_greed")
    parts = [f"BV-7X signal: {signal}"]
    if price is not None:
        parts.append(f"btc=${price}")
    if fg is not None:
        parts.append(f"F&G={fg}")
    return " · ".join(parts)


def _format_onchain_latest(data: dict[str, Any]) -> str:
    direction = data.get("direction") or data.get("signal") or "?"
    uid = data.get("uid") or data.get("attestation_uid") or "?"
    if isinstance(uid, str) and len(uid) > 18:
        uid_short = uid[:18] + "..."
    else:
        uid_short = uid
    return f"latest attestation: {direction} · uid={uid_short}"


def _format_onchain_stats(data: dict[str, Any]) -> str:
    total = data.get("total") or data.get("count") or "?"
    accuracy = data.get("accuracy") or "?"
    return f"on-chain attestations: {total} total · accuracy={accuracy}"


def _format_oracle(data: dict[str, Any], *, premium: bool = False) -> str:
    direction = data.get("direction") or data.get("signal") or "?"
    confidence = data.get("confidence") or "?"
    label = "premium oracle" if premium else "oracle"
    return f"BV-7X {label}: {direction} ({confidence})"


def _format_copy_trade(data: dict[str, Any], *, kind: str) -> str:
    if kind == "next":
        market = data.get("market") or data.get("question") or "?"
        side = data.get("side") or data.get("direction") or "?"
        return f"copy-trade next: {side} on {market}"
    # kind == "history"
    items = data.get("trades") or data.get("items") or data.get("history") or []
    return f"copy-trade history: {len(items)} trade(s)"


def register(ctx) -> None:
    register_with_ctx(ctx, bv7x_oracle)
