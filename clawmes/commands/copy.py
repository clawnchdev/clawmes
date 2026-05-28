"""``/copy`` — copy-trade a wallet's buys with bounded execution.

Watch another wallet's ERC-20 receipts. When the target receives a
new token (i.e. they bought it on a DEX), submit our own buy of that
token at a configurable fixed ETH amount. Safeguards mirror the ``/dca``
v2 surface so the same auto-pause / cap discipline applies.

Surface:

  * ``/copy add <wallet> <eth_per_copy> [--slippage bps]
    [--daily-cap eth] [--max-total eth] [--max-failures n]
    [--blocklist 0x…,0x…]``
                                              follow a wallet
  * ``/copy list``                            show your follows
  * ``/copy pause <id>``                      suspend without removing
  * ``/copy resume <id>``                     re-arm
  * ``/copy cancel <id>``                     remove entirely
  * ``/copy edit <id> <field> <value>``       change one field
  * ``/copy tick``                            manually poll + execute (testing)
  * ``/copy status``                          global summary
  * ``/copy history <id>``                    past copies

Detection: each tick walks ``account.tokentx`` on Basescan for every
followed wallet since the last seen block. Any incoming token transfer
(``to == watched_wallet``) for a token NOT in the per-follow blocklist
triggers a copy buy. We pay our own gas + slippage from the active
wallet at tick time.

False positives (airdrops, random sends) are minimized by:
  * Per-follow blocklist (set via ``--blocklist`` or ``/copy edit``).
  * Per-follow ETH cap (a runaway airdrop streak hits the daily cap
    and the schedule pauses itself).
  * Max-failures auto-pause when N consecutive copies fail.

Storage: ``${HERMES_HOME}/clawmes/copy/follows.json``.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from clawmes.lib.http import http_get

_BASESCAN_BASE = "https://api.basescan.org/api"
_DEFAULT_SLIPPAGE_BPS = 100
_DEFAULT_MAX_FAILURES = 3
_DEFAULT_LOOKBACK_BLOCKS = 10  # how far back to seed last_seen on first add
_MAX_TX_PER_TICK = 20  # cap copies per tick to avoid runaway


# ── helpers ─────────────────────────────────────────────────────────


def _follows_path() -> Path:
    from clawmes.lib.paths import state_dir

    return state_dir("copy") / "follows.json"


def _load_state() -> dict[str, Any]:
    path = _follows_path()
    if not path.exists():
        return {"follows": []}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {"follows": []}
    if not isinstance(data, dict) or not isinstance(data.get("follows"), list):
        return {"follows": []}
    return data


def _save_state(state: dict[str, Any]) -> None:
    path = _follows_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now_epoch() -> int:
    return int(time.time())


def _new_id() -> str:
    return f"copy_{uuid.uuid4().hex[:10]}"


def _short(value: str) -> str:
    if not isinstance(value, str) or len(value) <= 12:
        return str(value)
    return f"{value[:6]}…{value[-4:]}"


def _split_flags(parts: list[str]) -> tuple[list[str], dict[str, str]]:
    """Mirror of ``dca._split_flags`` — positional + ``--flag value`` pairs.

    Bare flags (no value) are supported: if the next token is another
    ``--flag`` or there is no next token, the current flag captures an
    empty string. This lets ``--invert`` and other booleans coexist with
    value flags like ``--blocklist <list>`` without the boolean swallowing
    the next flag's name.
    """
    positional: list[str] = []
    flags: dict[str, str] = {}
    i = 0
    while i < len(parts):
        tok = parts[i]
        if tok.startswith("--"):
            name = tok[2:]
            next_tok = parts[i + 1] if i + 1 < len(parts) else None
            if next_tok is not None and not next_tok.startswith("--"):
                flags[name] = next_tok
                i += 2
            else:
                flags[name] = ""
                i += 1
        else:
            positional.append(tok)
            i += 1
    return positional, flags


def _parse_blocklist(raw: str) -> list[str]:
    """Comma-separated token addresses → lowercased + validated list."""
    if not raw:
        return []
    out = []
    for tok in raw.split(","):
        tok = tok.strip()
        if tok.startswith("0x") and len(tok) == 42:
            out.append(tok.lower())
    return out


def _record(name: str, args: str, result: str) -> None:
    try:
        from clawmes.services.command_history import record_command_call

        record_command_call(name, args, result)
    except Exception:  # noqa: BLE001
        pass


# ── command dispatch ───────────────────────────────────────────────


async def handle_copy(raw_args: str, *, sender_id: str = "default", **_kwargs: Any) -> str:
    raw = (raw_args or "").strip()
    if not raw:
        out = _render_usage()
    else:
        parts = raw.split()
        sub = parts[0].lower()
        rest = parts[1:]
        if sub == "add":
            out = _cmd_add(sender_id, rest)
        elif sub in ("list", "ls"):
            out = _cmd_list(sender_id)
        elif sub == "pause":
            out = _cmd_mutate(sender_id, rest, status="paused", verb="paused")
        elif sub == "resume":
            out = _cmd_mutate(sender_id, rest, status="active", verb="resumed")
        elif sub in ("cancel", "rm", "remove"):
            out = _cmd_cancel(sender_id, rest)
        elif sub == "edit":
            out = _cmd_edit(sender_id, rest)
        elif sub == "tick":
            out = await _cmd_tick()
        elif sub == "status":
            out = _cmd_status(sender_id)
        elif sub == "history":
            out = _cmd_history(sender_id, rest)
        else:
            out = f"Unknown subcommand: {sub!r}\n\n" + _render_usage()
    _record("copy", raw_args, out)
    return out


def _render_usage() -> str:
    return (
        "Copy-trade — mirror a wallet's buys at a fixed ETH amount per copy.\n"
        "\n"
        "  /copy add <wallet> <eth_per_copy>\n"
        "      [--slippage <bps>] [--daily-cap <eth>]\n"
        "      [--max-total <eth>] [--max-failures <n>]\n"
        "      [--blocklist <0xa,0xb,…>] [--pct <N>]\n"
        "      [--invert] [--multi <0xa,0xb,…>]\n"
        "                          Follow a wallet's buys. --pct scales each\n"
        "                          copy to N%% of target's outgoing ETH (HOLDER).\n"
        "                          --invert also mirrors sells (HOLDER).\n"
        "                          --multi follows multiple wallets (UNLIMITED).\n"
        "  /copy list              Show your follows + last activity\n"
        "  /copy pause <id>        Suspend without losing state\n"
        "  /copy resume <id>       Re-arm a paused follow\n"
        "  /copy cancel <id>       Remove a follow entirely\n"
        "  /copy edit <id> <field> <value>\n"
        "                          Change one field on a follow\n"
        "  /copy tick              Manually poll + execute (testing)\n"
        "  /copy status            Global summary + service health\n"
        "  /copy history <id>      Past copies for a follow\n"
        "\n"
        "Example:\n"
        "  /copy add 0xWhale… 0.001 --slippage 200 --daily-cap 0.05\n"
        "    Watch the whale; copy each buy at 0.001 ETH with 2% slippage,\n"
        "    cap 24h spend at 0.05 ETH. Service auto-pauses after 3 failures."
    )


# ── /copy add ───────────────────────────────────────────────────────


def _cmd_add(sender_id: str, parts: list[str]) -> str:
    pos, flags = _split_flags(parts)
    if len(pos) < 2:
        return (
            "Usage: /copy add <wallet> <eth_per_copy>\n"
            "  [--slippage bps] [--daily-cap eth]\n"
            "  [--max-total eth] [--max-failures n]\n"
            "  [--blocklist 0xa,0xb,…]"
        )

    # Free-tier cap on active follows. Holders bypass.
    from clawmes.services.token_gate import check_cap_or_error

    state_check = _load_state()
    active_mine = sum(
        1
        for f in state_check["follows"]
        if f.get("sender_id") == sender_id and f.get("status") == "active"
    )
    cap_err = check_cap_or_error("copy", active_count=active_mine, feature="copy follow")
    if cap_err:
        return cap_err

    wallet, eth_raw = pos[0], pos[1]
    if not (wallet.startswith("0x") and len(wallet) == 42):
        return f"wallet must be a 0x… address (got {wallet!r})."
    try:
        eth_per_copy = float(eth_raw)
    except ValueError:
        return f"eth_per_copy must be a number (got {eth_raw!r})."
    if eth_per_copy <= 0:
        return f"eth_per_copy must be positive (got {eth_per_copy})."

    # Flag parsing — same shape as /dca add.
    slippage_bps = _DEFAULT_SLIPPAGE_BPS
    if "slippage" in flags:
        try:
            slippage_bps = int(flags["slippage"])
        except ValueError:
            return f"--slippage must be an integer bps value (got {flags['slippage']!r})."
        if slippage_bps < 0 or slippage_bps > 10_000:
            return f"--slippage must be 0–10000 bps (got {slippage_bps})."

    daily_cap_eth: float | None = None
    if "daily-cap" in flags:
        try:
            daily_cap_eth = float(flags["daily-cap"])
        except ValueError:
            return f"--daily-cap must be a number (got {flags['daily-cap']!r})."
        if daily_cap_eth <= 0:
            return f"--daily-cap must be positive (got {daily_cap_eth})."

    max_eth_total: float | None = None
    if "max-total" in flags:
        try:
            max_eth_total = float(flags["max-total"])
        except ValueError:
            return f"--max-total must be a number (got {flags['max-total']!r})."
        if max_eth_total <= 0:
            return f"--max-total must be positive (got {max_eth_total})."

    max_failures = _DEFAULT_MAX_FAILURES
    if "max-failures" in flags:
        try:
            max_failures = int(flags["max-failures"])
        except ValueError:
            return f"--max-failures must be an integer (got {flags['max-failures']!r})."
        if max_failures < 1:
            return f"--max-failures must be >= 1 (got {max_failures})."

    # Percentage-based sizing: when set, the per-copy ETH amount is
    # scaled to ``pct%`` of the target wallet's outgoing ETH on each
    # detected buy. ``eth_per_copy`` becomes the FLOOR (and used as
    # fallback when we can't read the target tx's ETH value). A holder-
    # tier feature; free tier sticks to the fixed amount.
    pct: float | None = None
    if "pct" in flags:
        from clawmes.services.token_gate import Tier, check_tier_or_error

        gate_err = check_tier_or_error(Tier.HOLDER, feature="/copy --pct percentage sizing")
        if gate_err:
            return gate_err
        try:
            pct = float(flags["pct"])
        except ValueError:
            return f"--pct must be a number (got {flags['pct']!r})."
        if pct <= 0 or pct > 1000:
            return f"--pct must be between 0 and 1000 (got {pct})."

    # Invert: also mirror SELLS from the watched wallet. When the wallet
    # sends a token OUT (to a DEX router, presumably to sell), we check
    # our balance for that token and submit a corresponding sell on our
    # side. HOLDER tier — captures "whale exiting" signal.
    invert = False
    if "invert" in flags:
        from clawmes.services.token_gate import Tier, check_tier_or_error

        gate_err = check_tier_or_error(Tier.HOLDER, feature="/copy --invert (mirror sells)")
        if gate_err:
            return gate_err
        invert = True

    # Multi-wallet: one follow tracks multiple wallets at once. The
    # `wallet` field stays the primary; `extra_wallets` carries the
    # rest. Polling iterates over all of them. UNLIMITED tier.
    extra_wallets: list[str] = []
    if "multi" in flags:
        from clawmes.services.token_gate import Tier, check_tier_or_error

        gate_err = check_tier_or_error(Tier.UNLIMITED, feature="/copy --multi (multiple wallets)")
        if gate_err:
            return gate_err
        raw_multi = flags["multi"]
        for w in raw_multi.split(","):
            w = w.strip()
            if not w:
                continue
            if not (w.startswith("0x") and len(w) == 42):
                return f"--multi wallet must be a 0x… address (got {w!r})."
            extra_wallets.append(w.lower())

    blocklist = _parse_blocklist(flags.get("blocklist", ""))

    # Seed last_seen_block from the current chain head. We default to a
    # small lookback so the first tick doesn't replay 100 days of history
    # but still catches anything from the last few blocks the user might
    # have intended.
    last_block = _current_block_height() - _DEFAULT_LOOKBACK_BLOCKS
    if last_block < 0:
        last_block = 0

    state = _load_state()
    follow_id = _new_id()
    follow = {
        "id": follow_id,
        "sender_id": sender_id,
        "wallet": wallet.lower(),
        "extra_wallets": extra_wallets,
        "eth_per_copy": eth_per_copy,
        "pct": pct,
        "invert": invert,
        "slippage_bps": slippage_bps,
        "daily_cap_eth": daily_cap_eth,
        "max_eth_total": max_eth_total,
        "max_consecutive_failures": max_failures,
        "blocklist": blocklist,
        "status": "active",
        "created_at": _now_iso(),
        "last_seen_block": last_block,
        "total_eth_spent": 0.0,
        "executions": [],
    }
    state["follows"].append(follow)
    _save_state(state)

    pct_line = f"  Pct sizing:  {pct}% of target tx\n" if pct is not None else ""
    multi_line = f"  Multi:       {len(extra_wallets)} extra wallet(s)\n" if extra_wallets else ""
    invert_line = "  Invert:      mirror sells too\n" if invert else ""
    return (
        f"Follow added: {follow_id}\n"
        f"  Wallet:      {wallet}\n"
        + multi_line
        + f"  Per copy:    {eth_per_copy} ETH"
        + (" (floor)" if pct is not None else "")
        + "\n"
        + pct_line
        + invert_line
        + f"  Slippage:    {slippage_bps} bps\n"
        f"  Daily cap:   {daily_cap_eth if daily_cap_eth is not None else 'none'}\n"
        f"  Total cap:   {max_eth_total if max_eth_total is not None else 'none'}\n"
        f"  Max fails:   {max_failures}\n"
        f"  Blocklist:   {len(blocklist)} token(s)\n"
        f"  Last block:  {last_block}\n"
        "\n"
        "The copy-trader service polls Basescan every ~60s and submits a buy\n"
        "whenever a watched wallet receives a non-blocklisted token. Use\n"
        "/copy list to see all your follows."
    )


# ── /copy list / mutate / cancel ────────────────────────────────────


def _cmd_list(sender_id: str) -> str:
    state = _load_state()
    mine = [f for f in state["follows"] if f.get("sender_id") == sender_id]
    if not mine:
        return "No /copy follows. Add one with /copy add <wallet> <eth_per_copy>."

    lines = [f"Copy follows for {sender_id} ({len(mine)}):", ""]
    for f in mine:
        runs = len(f.get("executions", []))
        lines.append(
            f"  {f['id']}  {f.get('status'):<8s}"
            f"  {f['eth_per_copy']} ETH/copy  → {_short(f['wallet'])}"
            f"  ({runs} copies, last_block={f.get('last_seen_block')})"
        )
    return "\n".join(lines)


def _cmd_mutate(sender_id: str, parts: list[str], *, status: str, verb: str) -> str:
    if not parts:
        return f"Usage: /copy {verb.removesuffix('d')} <id>"
    fid = parts[0]
    state = _load_state()
    for f in state["follows"]:
        if f.get("id") == fid and f.get("sender_id") == sender_id:
            f["status"] = status
            _save_state(state)
            return f"Follow {fid} {verb}."
    return f"No follow found with id {fid!r}."


def _cmd_cancel(sender_id: str, parts: list[str]) -> str:
    if not parts:
        return "Usage: /copy cancel <id>"
    fid = parts[0]
    state = _load_state()
    before = len(state["follows"])
    state["follows"] = [
        f for f in state["follows"] if not (f.get("id") == fid and f.get("sender_id") == sender_id)
    ]
    if len(state["follows"]) == before:
        return f"No follow found with id {fid!r}."
    _save_state(state)
    return f"Cancelled follow {fid}."


# ── /copy edit ──────────────────────────────────────────────────────


_EDITABLE = {
    "eth_per_copy",
    "slippage_bps",
    "daily_cap_eth",
    "max_eth_total",
    "max_consecutive_failures",
    "blocklist",
    "invert",
    "extra_wallets",
}


def _cmd_edit(sender_id: str, parts: list[str]) -> str:
    if len(parts) < 3:
        return f"Usage: /copy edit <id> <field> <value>\nFields: {', '.join(sorted(_EDITABLE))}"
    fid, field, value = parts[0], parts[1], parts[2]
    if field not in _EDITABLE:
        return f"Unknown field {field!r}. Editable: {', '.join(sorted(_EDITABLE))}"

    state = _load_state()
    follow = _find(state, fid, sender_id)
    if follow is None:
        return f"No follow found with id {fid!r}."

    if field == "eth_per_copy":
        try:
            v = float(value)
        except ValueError:
            return f"eth_per_copy must be a number (got {value!r})."
        if v <= 0:
            return f"eth_per_copy must be positive (got {v})."
        follow["eth_per_copy"] = v
    elif field == "slippage_bps":
        try:
            v = int(value)
        except ValueError:
            return f"slippage_bps must be an integer (got {value!r})."
        if v < 0 or v > 10_000:
            return f"slippage_bps must be 0–10000 (got {v})."
        follow["slippage_bps"] = v
    elif field == "daily_cap_eth":
        if value.lower() == "none":
            follow["daily_cap_eth"] = None
        else:
            try:
                v = float(value)
            except ValueError:
                return f"daily_cap_eth must be a number or 'none' (got {value!r})."
            if v <= 0:
                return f"daily_cap_eth must be positive (got {v})."
            follow["daily_cap_eth"] = v
    elif field == "max_eth_total":
        if value.lower() == "none":
            follow["max_eth_total"] = None
        else:
            try:
                v = float(value)
            except ValueError:
                return f"max_eth_total must be a number or 'none' (got {value!r})."
            if v <= 0:
                return f"max_eth_total must be positive (got {v})."
            follow["max_eth_total"] = v
    elif field == "max_consecutive_failures":
        try:
            v = int(value)
        except ValueError:
            return f"max_consecutive_failures must be an integer (got {value!r})."
        if v < 1:
            return f"max_consecutive_failures must be >= 1 (got {v})."
        follow["max_consecutive_failures"] = v
    elif field == "invert":
        # Accept true/false/yes/no/on/off. HOLDER tier required to flip ON.
        truthy = {"true", "yes", "on", "1"}
        falsy = {"false", "no", "off", "0"}
        v_lower = value.lower()
        if v_lower in truthy:
            from clawmes.services.token_gate import Tier, check_tier_or_error

            gate_err = check_tier_or_error(Tier.HOLDER, feature="/copy --invert (mirror sells)")
            if gate_err:
                return gate_err
            follow["invert"] = True
        elif v_lower in falsy:
            follow["invert"] = False
        else:
            return f"invert must be true|false (got {value!r})."
    elif field == "extra_wallets":
        # Comma-separated list of additional wallet addresses; "none"
        # clears. UNLIMITED tier required to add any extras.
        from clawmes.services.token_gate import Tier, check_tier_or_error

        if value.lower() == "none":
            follow["extra_wallets"] = []
        else:
            gate_err = check_tier_or_error(
                Tier.UNLIMITED, feature="/copy --multi (multiple wallets)"
            )
            if gate_err:
                return gate_err
            extras: list[str] = []
            for w in value.split(","):
                w = w.strip()
                if not w:
                    continue
                if not (w.startswith("0x") and len(w) == 42):
                    return f"extra_wallets entry must be 0x… address (got {w!r})."
                extras.append(w.lower())
            follow["extra_wallets"] = extras
    else:  # blocklist
        follow["blocklist"] = _parse_blocklist(value)

    _save_state(state)
    return f"Follow {fid}: {field} = {value}."


def _find(state: dict[str, Any], fid: str, sender_id: str) -> dict[str, Any] | None:
    for f in state["follows"]:
        if f.get("id") == fid and f.get("sender_id") == sender_id:
            return f
    return None


# ── /copy tick — the actual watcher ─────────────────────────────────


async def _cmd_tick() -> str:
    """Manual ``/copy tick`` — delegates to the sync runner."""
    n, lines = _run_due_with_lines()
    if n == 0:
        return "No copy follows had new activity."
    return "\n".join([f"Processed {n} new tx(s)..."] + lines)


def _run_due_sync() -> int:
    """Service entrypoint — returns count of copies submitted across all follows."""
    n, _ = _run_due_with_lines()
    return n


def _run_due_with_lines() -> tuple[int, list[str]]:
    state = _load_state()
    lines: list[str] = []
    total = 0
    for follow in state["follows"]:
        if follow.get("status") != "active":
            continue
        try:
            count = _process_follow(follow, lines)
            total += count
        except Exception as exc:  # noqa: BLE001
            lines.append(f"  {follow['id']}  error fetching: {exc}")
    if total > 0 or any(lines):
        _save_state(state)
    return total, lines


def _process_follow(follow: dict[str, Any], lines: list[str]) -> int:
    """Poll Basescan for new transfers on every watched wallet.

    Iterates over the primary ``wallet`` plus any ``extra_wallets``
    (multi-wallet follow). For each watched address:

    * INCOMING transfers (``to == wallet``) → trigger a buy on our
      side. Default copy behavior.
    * OUTGOING transfers (``from == wallet``) → only processed when
      ``invert`` is set on the follow. Treated as sell signals;
      we sell our own balance of the token via ``_execute_sell``.
    """
    wallets = _all_watched_wallets(follow)
    start_block = int(follow.get("last_seen_block", 0)) + 1
    invert = bool(follow.get("invert"))
    blocklist = set(follow.get("blocklist", []))
    count = 0
    max_block = int(follow.get("last_seen_block", 0))

    for wallet in wallets:
        if invert:
            txs = _basescan_token_transfers_all(wallet, start_block=start_block)
        else:
            txs = _basescan_token_receipts(wallet, start_block=start_block)
        if not txs:
            continue
        # Cap per-tick processing per wallet so a single wallet's airdrop
        # burst can't crowd out the rest.
        txs = txs[:_MAX_TX_PER_TICK]
        wallet_lower = wallet.lower()

        for tx in txs:
            token = (tx.get("contractAddress") or "").lower()
            if not token or len(token) != 42:
                continue
            block_no = int(tx.get("blockNumber", 0))
            if block_no > max_block:
                max_block = block_no

            tx_to = (tx.get("to") or "").lower()
            tx_from = (tx.get("from") or "").lower()
            is_incoming = tx_to == wallet_lower
            is_outgoing = tx_from == wallet_lower and tx_to != wallet_lower

            if token in blocklist:
                lines.append(f"  {follow['id']}  skipped {_short(token)} (blocklisted)")
                follow["executions"].append(
                    {
                        "at": _now_iso(),
                        "tx_seen": tx.get("hash", ""),
                        "watched_wallet": wallet_lower,
                        "token": token,
                        "result": {
                            "status": "blocklisted",
                            "detail": "in follow blocklist",
                        },
                    }
                )
                continue

            if is_incoming:
                eth_for_copy = _compute_copy_amount(follow, tx)
                result = _execute_copy(follow, token, eth_amount=eth_for_copy)
                follow["executions"].append(
                    {
                        "at": _now_iso(),
                        "tx_seen": tx.get("hash", ""),
                        "watched_wallet": wallet_lower,
                        "direction": "buy",
                        "token": token,
                        "eth_amount": eth_for_copy,
                        "result": result,
                    }
                )
                if result.get("status") == "ok":
                    follow["total_eth_spent"] = (
                        float(follow.get("total_eth_spent", 0.0)) + eth_for_copy
                    )
                    count += 1
                lines.append(
                    f"  {follow['id']}  buy  {result.get('status')}  "
                    f"{_short(token)}  {result.get('detail', '')}"
                )
            elif is_outgoing and invert:
                result = _execute_sell(follow, token)
                follow["executions"].append(
                    {
                        "at": _now_iso(),
                        "tx_seen": tx.get("hash", ""),
                        "watched_wallet": wallet_lower,
                        "direction": "sell",
                        "token": token,
                        "result": result,
                    }
                )
                if result.get("status") == "ok":
                    count += 1
                lines.append(
                    f"  {follow['id']}  sell {result.get('status')}  "
                    f"{_short(token)}  {result.get('detail', '')}"
                )
            # else: tx neither incoming nor outgoing to/from this
            # watched wallet (e.g. internal transfers we shouldn't act
            # on) — silently skip.

    follow["last_seen_block"] = max_block
    _maybe_auto_pause(follow)
    return count


def _all_watched_wallets(follow: dict[str, Any]) -> list[str]:
    """Primary + extras, normalized + deduplicated."""
    seen: set[str] = set()
    out: list[str] = []
    primary = (follow.get("wallet") or "").lower()
    if primary:
        seen.add(primary)
        out.append(primary)
    for w in follow.get("extra_wallets", []) or []:
        if not isinstance(w, str):
            continue
        wl = w.lower()
        if wl in seen:
            continue
        seen.add(wl)
        out.append(wl)
    return out


def _maybe_auto_pause(follow: dict[str, Any]) -> None:
    max_fails = int(follow.get("max_consecutive_failures") or _DEFAULT_MAX_FAILURES)
    runs = follow.get("executions", [])
    if len(runs) < max_fails:
        return
    tail = runs[-max_fails:]
    fail_states = {"error", "no_wallet", "daily_capped", "total_capped"}
    if all((r.get("result") or {}).get("status") in fail_states for r in tail):
        follow["status"] = "paused"


def _spend_in_last_24h(follow: dict[str, Any]) -> float:
    runs = follow.get("executions", [])
    cutoff = _now_epoch() - 86400
    eth_per_copy = float(follow.get("eth_per_copy", 0.0))
    total = 0.0
    for run in runs:
        if (run.get("result") or {}).get("status") != "ok":
            continue
        at = run.get("at", "")
        try:
            dt = datetime.strptime(at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
            ts = int(dt.timestamp())
        except (ValueError, TypeError):
            continue
        if ts >= cutoff:
            total += eth_per_copy
    return total


def _compute_copy_amount(follow: dict[str, Any], tx: dict[str, Any]) -> float:
    """Resolve the actual ETH amount for one copy.

    Default: ``follow["eth_per_copy"]`` — fixed sizing.

    With ``pct`` set: scale to the target wallet's outgoing ETH on the
    seen tx (looked up via ``eth_getTransactionByHash``), capped at
    ``eth_per_copy`` so a whale's huge buy can't drain the follower's
    wallet. Falls back to fixed sizing when the target tx had no ETH
    value (e.g., token-token swap) or when the lookup fails.
    """
    base = float(follow.get("eth_per_copy", 0.0))
    pct = follow.get("pct")
    if pct is None:
        return base
    tx_hash = tx.get("hash", "")
    if not tx_hash:
        return base
    target_eth_wei = _get_tx_eth_value(tx_hash)
    if target_eth_wei <= 0:
        return base
    target_eth = target_eth_wei / 1e18
    scaled = target_eth * (float(pct) / 100.0)
    # Cap at eth_per_copy so a whale's huge buy is bounded.
    return min(scaled, base)


def _get_tx_eth_value(tx_hash: str) -> int:
    """Read the ``value`` field (wei) of a tx via Basescan's proxy endpoint.

    Returns ``0`` on any error — the caller treats 0 as "fall back to
    fixed sizing." Cached implicitly by Basescan's CDN; we don't add
    a clawmes-side cache because the per-tick cap of 20 copies already
    bounds the call rate.
    """
    params = {
        "module": "proxy",
        "action": "eth_getTransactionByHash",
        "txhash": tx_hash,
    }
    api_key = os.environ.get("BASESCAN_API_KEY")
    if api_key:
        params["apikey"] = api_key
    try:
        body = http_get(_BASESCAN_BASE, params=params, timeout=10.0)
    except Exception:  # noqa: BLE001
        return 0
    if not isinstance(body, dict):
        return 0
    result = body.get("result")
    if not isinstance(result, dict):
        return 0
    value = result.get("value")
    if not isinstance(value, str) or not value.startswith("0x"):
        return 0
    try:
        return int(value, 16)
    except (ValueError, TypeError):
        return 0


def _execute_copy(
    follow: dict[str, Any], token: str, *, eth_amount: float | None = None
) -> dict[str, Any]:
    """Submit one copy buy. Same safeguard order as /dca v2.

    ``eth_amount`` defaults to ``follow["eth_per_copy"]`` when omitted —
    the manual ``/copy tick`` path passes nothing. Callers using
    percentage-based sizing (``--pct``) precompute the amount and pass
    it explicitly via ``_compute_copy_amount``.
    """
    if eth_amount is None:
        eth_amount = float(follow.get("eth_per_copy", 0.0))
    else:
        eth_amount = float(eth_amount)

    max_total = follow.get("max_eth_total")
    if max_total is not None:
        spent = float(follow.get("total_eth_spent", 0.0))
        if spent + eth_amount > float(max_total):
            return {
                "status": "total_capped",
                "detail": f"would exceed max-total {max_total} ETH (spent {spent})",
                "tx_hash": "",
            }

    daily_cap = follow.get("daily_cap_eth")
    if daily_cap is not None:
        spent_24h = _spend_in_last_24h(follow)
        if spent_24h + eth_amount > float(daily_cap):
            return {
                "status": "daily_capped",
                "detail": f"would exceed daily-cap {daily_cap} ETH (spent {spent_24h:.6f} in 24h)",
                "tx_hash": "",
            }

    from clawmes.services.wallet import get_wallet_state

    wstate = get_wallet_state()
    if not wstate.connected:
        return {"status": "no_wallet", "detail": "no wallet connected", "tx_hash": ""}

    try:
        from clawmes.tools.defi_swap import defi_swap

        raw = defi_swap(
            {
                "action": "swap",
                "sell_token": "ETH",
                "buy_token": token,
                "sell_amount": str(eth_amount),
                "slippage_bps": int(follow.get("slippage_bps") or _DEFAULT_SLIPPAGE_BPS),
            }
        )
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "detail": str(exc), "tx_hash": ""}

    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return {"status": "error", "detail": f"bad swap response: {raw}", "tx_hash": ""}
    if payload.get("isError"):
        msg = payload.get("content", [{}])[0].get("text", "swap failed")
        return {"status": "error", "detail": msg, "tx_hash": ""}

    details = payload.get("details") or {}
    tx_hash = details.get("tx_hash") or details.get("txHash") or ""
    return {"status": "ok", "detail": f"tx {tx_hash[:14]}…", "tx_hash": tx_hash}


def _execute_sell(follow: dict[str, Any], token: str) -> dict[str, Any]:
    """Sell our balance of ``token`` back to ETH (invert mode).

    Behavior:
    * If we hold zero of the token, return ``no_balance`` — silent no-op.
    * Otherwise submit ``defi_swap`` with sell_token=token, buy_token=ETH,
      sell_amount=our_balance.

    The sell amount uses 100% of our current token balance. We don't
    try to scale via ``--pct`` because the typical "whale exit"
    signal is "the wallet is done with this position"; a partial sell
    doesn't match the signal.
    """
    from clawmes.services.wallet import get_wallet_state

    wstate = get_wallet_state()
    if not wstate.connected:
        return {"status": "no_wallet", "detail": "no wallet connected", "tx_hash": ""}

    balance_wei = _read_our_token_balance(token, wstate.address)
    if balance_wei <= 0:
        return {
            "status": "no_balance",
            "detail": "we don't hold this token",
            "tx_hash": "",
        }

    try:
        from clawmes.tools.defi_swap import defi_swap

        raw = defi_swap(
            {
                "action": "swap",
                "sell_token": token,
                "buy_token": "ETH",
                "sell_amount_wei": str(balance_wei),
                "slippage_bps": int(follow.get("slippage_bps") or _DEFAULT_SLIPPAGE_BPS),
            }
        )
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "detail": str(exc), "tx_hash": ""}

    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return {"status": "error", "detail": f"bad swap response: {raw}", "tx_hash": ""}
    if payload.get("isError"):
        msg = payload.get("content", [{}])[0].get("text", "swap failed")
        return {"status": "error", "detail": msg, "tx_hash": ""}

    details = payload.get("details") or {}
    tx_hash = details.get("tx_hash") or details.get("txHash") or ""
    return {"status": "ok", "detail": f"sold via tx {tx_hash[:14]}…", "tx_hash": tx_hash}


def _read_our_token_balance(token: str, address: str | None) -> int:
    """Read our balance of ``token`` via ``balanceOf`` eth_call.

    Returns 0 on any error — invert mode treats unknown balances as
    "don't sell" rather than risking an erroneous tx.
    """
    if not address:
        return 0
    try:
        from clawmes.lib.abi import decode_uint, encode_balance_of
        from clawmes.services.rpc import get_rpc_service

        rpc = get_rpc_service()
        raw = rpc.eth_call(
            to=token,
            data=encode_balance_of(address),
            chain_id=8453,
        )
        return decode_uint(raw)
    except Exception:  # noqa: BLE001
        return 0


# ── Basescan polling ────────────────────────────────────────────────


def _basescan_token_receipts(wallet: str, *, start_block: int) -> list[dict[str, Any]]:
    """Return ERC-20 token transfers IN to ``wallet`` from ``start_block``+.

    Uses Basescan's ``account.tokentx`` endpoint, filtered to incoming
    transfers (``to == wallet``). Returns the raw entries; callers map
    ``contractAddress`` → token to copy.
    """
    params = {
        "module": "account",
        "action": "tokentx",
        "address": wallet,
        "startblock": str(start_block),
        "endblock": "99999999",
        "sort": "asc",
    }
    api_key = os.environ.get("BASESCAN_API_KEY")
    if api_key:
        params["apikey"] = api_key

    body = http_get(_BASESCAN_BASE, params=params, timeout=20.0)
    if not isinstance(body, dict):
        return []
    if str(body.get("status")) != "1":
        # status="0" usually means "No transactions found" — return [].
        return []
    result = body.get("result")
    if not isinstance(result, list):
        return []
    # Keep only incoming transfers (``to`` matches the watched wallet).
    incoming = [
        x for x in result if isinstance(x, dict) and (x.get("to") or "").lower() == wallet.lower()
    ]
    return incoming


def _basescan_token_transfers_all(wallet: str, *, start_block: int) -> list[dict[str, Any]]:
    """Return ERC-20 transfers BOTH directions for ``wallet``.

    Used by invert-mode follows: incoming transfers map to "buy on our
    side" and outgoing transfers map to "sell on our side". Caller
    classifies each entry by comparing ``from`` / ``to`` against the
    watched wallet.
    """
    params = {
        "module": "account",
        "action": "tokentx",
        "address": wallet,
        "startblock": str(start_block),
        "endblock": "99999999",
        "sort": "asc",
    }
    api_key = os.environ.get("BASESCAN_API_KEY")
    if api_key:
        params["apikey"] = api_key

    body = http_get(_BASESCAN_BASE, params=params, timeout=20.0)
    if not isinstance(body, dict):
        return []
    if str(body.get("status")) != "1":
        return []
    result = body.get("result")
    if not isinstance(result, list):
        return []
    wallet_lower = wallet.lower()
    # Both directions: from == wallet OR to == wallet. Drop everything else.
    relevant = [
        x
        for x in result
        if isinstance(x, dict)
        and (
            (x.get("to") or "").lower() == wallet_lower
            or (x.get("from") or "").lower() == wallet_lower
        )
    ]
    return relevant


def _current_block_height() -> int:
    """Best-effort head-block fetch for seeding last_seen on new follows."""
    params = {
        "module": "proxy",
        "action": "eth_blockNumber",
    }
    api_key = os.environ.get("BASESCAN_API_KEY")
    if api_key:
        params["apikey"] = api_key

    try:
        body = http_get(_BASESCAN_BASE, params=params, timeout=10.0)
    except Exception:  # noqa: BLE001
        return 0
    if not isinstance(body, dict):
        return 0
    result = body.get("result")
    if not isinstance(result, str) or not result.startswith("0x"):
        return 0
    try:
        return int(result, 16)
    except (ValueError, TypeError):
        return 0


# ── /copy status ────────────────────────────────────────────────────


def _cmd_status(_sender_id: str) -> str:
    state = _load_state()
    follows = state.get("follows", [])
    if not follows:
        return "No /copy follows exist. The watcher service is idle."

    by_status: dict[str, int] = {}
    total_spent = 0.0
    total_runs = 0
    failures = 0
    for f in follows:
        s = f.get("status", "?")
        by_status[s] = by_status.get(s, 0) + 1
        total_spent += float(f.get("total_eth_spent", 0.0))
        runs = f.get("executions", [])
        total_runs += len(runs)
        for r in runs:
            status = (r.get("result") or {}).get("status", "")
            if status in ("error", "no_wallet", "daily_capped", "total_capped"):
                failures += 1

    lines = [
        f"/copy watcher status ({len(follows)} follow(s)):",
        "",
        f"  By status:    {', '.join(f'{k}={v}' for k, v in sorted(by_status.items()))}",
        f"  Total copies: {total_runs}",
        f"  Failures:     {failures}",
        f"  ETH spent:    {total_spent:.6f}",
    ]

    try:
        from clawmes.services.copy_trader import get_copy_trader_service

        svc = get_copy_trader_service()
        h = svc.health()
        lines.append(
            f"  Service:      {h.get('status')} "
            f"(ticks={h.get('ticks')}, total_copies={h.get('total_runs')})"
        )
    except Exception:  # noqa: BLE001
        pass

    return "\n".join(lines)


# ── /copy history ───────────────────────────────────────────────────


def _cmd_history(sender_id: str, parts: list[str]) -> str:
    if not parts:
        return "Usage: /copy history <id>"
    fid = parts[0]
    state = _load_state()
    follow = _find(state, fid, sender_id)
    if follow is None:
        return f"No follow found with id {fid!r}."
    runs = follow.get("executions", [])
    if not runs:
        return f"Follow {fid}: no copies yet. (Watcher will surface new buys on the next tick.)"
    lines = [f"Copies for {fid} ({len(runs)}):", ""]
    for run in runs[-25:]:  # most recent 25
        result = run.get("result") or {}
        status = result.get("status", "?")
        token = run.get("token", "")
        when = run.get("at", "")
        tx_seen = run.get("tx_seen", "")
        seen_str = f"  seen {_short(tx_seen)}" if tx_seen else ""
        lines.append(f"  {when}  {status:<13s}  {_short(token)}{seen_str}")
    # Quiet the timedelta import warning — it's used by tests.
    _ = timedelta
    return "\n".join(lines)


def register(ctx) -> None:
    ctx.register_command(
        name="copy",
        handler=handle_copy,
        description="Copy-trade a wallet's buys at a fixed ETH amount",
        args_hint="add <wallet> <eth> | list | pause | resume | cancel | edit | tick | status | history",
    )
