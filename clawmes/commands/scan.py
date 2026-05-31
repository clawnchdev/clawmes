"""``/scan <wallet>`` — comprehensive wallet analysis.

A Holder-tier feature. Pulls a multi-source snapshot of any Base
wallet so traders can do due diligence before copying or following:

  * Native ETH balance + recent activity volume
  * Top ERC-20 holdings (unique tokens recently received)
  * Recent activity summary: tx count, last seen
  * Risk flags: brand-new wallet, single-token concentration

Reading is unauthenticated — anyone can /scan any address. The HOLDER
gate is in place because the underlying Basescan API calls have a
rate limit we want to protect, and because scan is a power-trader
feature that pairs naturally with /copy follows.

Surface:

  * ``/scan <wallet>``         single-wallet summary
  * ``/scan <wallet> --json``  raw structured data

The render is intentionally compact — one screenful — and avoids
making any directional claims (good/bad token, buy/sell signal). The
output is a snapshot, not advice.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any

from clawmes.lib.http import http_get

_BASESCAN_BASE = "https://api.basescan.org/api"


def _record(name: str, args: str, result: str) -> None:
    try:
        from clawmes.services.command_history import record_command_call

        record_command_call(name, args, result)
    except Exception:  # noqa: BLE001
        pass


# ── dispatch ────────────────────────────────────────────────────────


async def handle_scan(raw_args: str, *, sender_id: str = "default", **_kwargs: Any) -> str:
    raw = (raw_args or "").strip()
    if not raw:
        out = (
            "Usage: /scan <wallet> [--json]\n"
            "\n"
            "Example: /scan 0xWhale… → snapshot of holdings + activity"
        )
        _record("scan", raw_args, out)
        return out

    from clawmes.services.token_gate import Tier, check_tier_or_error

    gate_err = check_tier_or_error(Tier.HOLDER, feature="/scan")
    if gate_err:
        return gate_err

    parts = raw.split()
    wallet = parts[0]
    as_json = "--json" in parts

    if not (wallet.startswith("0x") and len(wallet) == 42):
        out = f"wallet must be a 0x… address (got {wallet!r})."
        _record("scan", raw_args, out)
        return out

    snapshot = _build_snapshot(wallet.lower())
    out = json.dumps(snapshot, indent=2) if as_json else _render(snapshot)
    _record("scan", raw_args, out)
    return out


# ── data assembly ──────────────────────────────────────────────────


def _build_snapshot(wallet: str) -> dict[str, Any]:
    """Aggregate every available signal on ``wallet`` into one dict."""
    snapshot: dict[str, Any] = {"wallet": wallet}
    snapshot["balance_eth"] = _fetch_native_balance(wallet)
    txs = _fetch_recent_token_txs(wallet)
    snapshot["tx_count_30d"] = len(txs)
    snapshot["top_tokens"] = _aggregate_top_tokens(txs)
    snapshot["last_activity"] = _last_activity_timestamp(txs)
    snapshot["flags"] = _compute_flags(snapshot)
    return snapshot


def _fetch_native_balance(wallet: str) -> float:
    """Read native ETH balance via ``account.balance``. Returns ETH float."""
    params = {"module": "account", "action": "balance", "address": wallet}
    api_key = os.environ.get("BASESCAN_API_KEY")
    if api_key:
        params["apikey"] = api_key
    try:
        body = http_get(_BASESCAN_BASE, params=params, timeout=10.0)
    except Exception:  # noqa: BLE001
        return 0.0
    if not isinstance(body, dict):
        return 0.0
    if str(body.get("status")) != "1":
        return 0.0
    raw = body.get("result")
    try:
        return int(raw) / 1e18
    except (TypeError, ValueError):
        return 0.0


def _fetch_recent_token_txs(wallet: str) -> list[dict[str, Any]]:
    """Fetch recent ERC-20 token transfer events.

    Both directions. Capped at the most recent 100 transfers — Basescan
    returns them recency-sorted when we pass ``sort=desc``. We use
    ``startblock=0`` because we want lifetime activity, but cap the
    response so the call doesn't balloon for hyperactive wallets.
    """
    params = {
        "module": "account",
        "action": "tokentx",
        "address": wallet,
        "startblock": "0",
        "endblock": "99999999",
        "sort": "desc",
        "page": "1",
        "offset": "100",
    }
    api_key = os.environ.get("BASESCAN_API_KEY")
    if api_key:
        params["apikey"] = api_key
    try:
        body = http_get(_BASESCAN_BASE, params=params, timeout=15.0)
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(body, dict):
        return []
    if str(body.get("status")) != "1":
        return []
    result = body.get("result")
    if not isinstance(result, list):
        return []
    return [x for x in result if isinstance(x, dict)]


def _aggregate_top_tokens(txs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group ERC-20 transfers by token, summarize each one.

    Returns up to 10 entries sorted by total net activity (incoming +
    outgoing transfer count), which is a reasonable proxy for "tokens
    this wallet cares about."
    """
    by_token: dict[str, dict[str, Any]] = {}
    for tx in txs:
        addr = (tx.get("contractAddress") or "").lower()
        if not addr or len(addr) != 42:
            continue
        entry = by_token.setdefault(
            addr,
            {
                "address": addr,
                "symbol": tx.get("tokenSymbol") or "?",
                "name": tx.get("tokenName") or "?",
                "transfer_count": 0,
                "first_seen": tx.get("timeStamp"),
                "last_seen": tx.get("timeStamp"),
            },
        )
        entry["transfer_count"] += 1
        ts = tx.get("timeStamp")
        try:
            ts_int = int(ts)
            if int(entry["first_seen"] or ts_int) > ts_int:
                entry["first_seen"] = ts_int
            if int(entry["last_seen"] or ts_int) < ts_int:
                entry["last_seen"] = ts_int
        except (TypeError, ValueError):
            pass
    ranked = sorted(by_token.values(), key=lambda x: x["transfer_count"], reverse=True)
    return ranked[:10]


def _last_activity_timestamp(txs: list[dict[str, Any]]) -> int:
    """Most recent transfer's epoch. 0 if none."""
    best = 0
    for tx in txs:
        try:
            best = max(best, int(tx.get("timeStamp", 0)))
        except (TypeError, ValueError):
            continue
    return best


def _compute_flags(snapshot: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    tokens = snapshot.get("top_tokens") or []
    tx_count = int(snapshot.get("tx_count_30d") or 0)
    if tx_count == 0:
        flags.append("no_recent_activity")
    elif tx_count < 5:
        flags.append("very_low_activity")
    if len(tokens) == 1 and tx_count >= 5:
        flags.append("single_token_concentration")
    if snapshot.get("balance_eth", 0.0) == 0.0 and tx_count == 0:
        flags.append("empty_wallet")
    if tokens:
        top = tokens[0]
        ratio = top["transfer_count"] / max(1, tx_count)
        if ratio >= 0.8 and tx_count >= 10:
            flags.append("single_token_dominance")
    return flags


# ── render ──────────────────────────────────────────────────────────


def _render(snapshot: dict[str, Any]) -> str:
    wallet = snapshot.get("wallet", "?")
    eth = snapshot.get("balance_eth", 0.0)
    tx_count = snapshot.get("tx_count_30d", 0)
    last = snapshot.get("last_activity", 0)
    last_iso = (
        datetime.fromtimestamp(last, tz=UTC).strftime("%Y-%m-%d %H:%M UTC") if last else "never"
    )
    lines = [
        f"Wallet snapshot: {wallet}",
        "",
        "BALANCE + ACTIVITY",
        f"  Native ETH:       {eth:.6f}",
        f"  Recent transfers: {tx_count}",
        f"  Last activity:    {last_iso}",
        "",
        "TOP TOKENS (by transfer activity)",
    ]
    top_tokens = snapshot.get("top_tokens") or []
    if not top_tokens:
        lines.append("  (no token activity)")
    else:
        for tok in top_tokens:
            lines.append(
                f"  {tok.get('symbol', '?'):<10s}  {tok.get('name', '?'):<30s}"
                f"  {tok.get('transfer_count', 0):4d} transfers"
            )
    flags = snapshot.get("flags") or []
    if flags:
        lines.append("")
        lines.append("FLAGS")
        for f in flags:
            lines.append(f"  • {f}")
    return "\n".join(lines)


def register(ctx) -> None:
    ctx.register_command(
        name="scan",
        handler=handle_scan,
        description="Comprehensive wallet analysis (Holder tier)",
        args_hint="<wallet> [--json]",
    )
