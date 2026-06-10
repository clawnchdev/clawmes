"""``/burn`` slash command — standalone $CLAWNCH burn.

Decoupled from the launch flow so users can burn at any time —
before drafting a launch, between launches, or ahead of a deploy
they're planning to do later from Base MCP / Claude Desktop /
another agent surface.

A verified 1,000,000+ $CLAWNCH burn is **required for every launch**
(the launchpad rejects no-burn deploys with ``burn_required``); the
same burn claims the Clanker vault allocation as a bonus.

Two input modes:

  * ``/burn <amount>`` — sign + submit a CLAWNCH transfer to the burn
    address from the active wallet. Amount in whole tokens (e.g.
    ``1000000`` for 1M CLAWNCH = 1% vault on next deploy).
  * ``/burn last`` — show the most recent burn tx hash submitted via
    this command, useful for piping into ``/launch burn <tx_hash>``
    or ``/api/prepare/deploy?burnTxHash=<hash>``.

Vault curve (verified server-side on every deploy that supplies the
hash):

  *   1,000,000 CLAWNCH → 1% vault
  *  10,000,000 CLAWNCH → 10% vault (Clanker maximum)
  *      between → linear (1k allocated tokens per 1 CLAWNCH burned)

The 7-day Clanker lockup applies to vault tokens.

State: per-sender, in-process. The most recent successful burn tx
hash is cached so the user can copy/reference it without scrolling.
Clears at process restart.
"""

from __future__ import annotations

import threading
from typing import Any

_burn_state: dict[str, dict[str, Any]] = {}
_state_lock = threading.RLock()

_MIN_BURN_TOKENS = 1_000_000
_MAX_BURN_TOKENS = 10_000_000


def _record(name: str, args: str, result: str) -> None:
    try:
        from clawmes.services.command_history import record_command_call

        record_command_call(name, args, result)
    except Exception:  # noqa: BLE001
        pass


def _resolve_sender(kwargs: dict[str, Any]) -> str:
    return str(kwargs.get("sender_id") or "default")


def _remember_burn(sender_id: str, tx_hash: str, amount: int) -> None:
    with _state_lock:
        _burn_state[sender_id] = {"tx_hash": tx_hash, "amount": amount}


def _recall_burn(sender_id: str) -> dict[str, Any] | None:
    with _state_lock:
        return _burn_state.get(sender_id)


def _parse_amount(raw: str) -> int | str:
    """Return the parsed amount or an error string."""
    try:
        amount = int(raw.replace("_", "").replace(",", ""))
    except ValueError:
        return f"Invalid amount {raw!r}. Expected a positive integer (e.g. 1000000)."
    if amount < _MIN_BURN_TOKENS:
        return (
            f"Burn amount too low: {amount:,} CLAWNCH "
            f"(minimum {_MIN_BURN_TOKENS:,} — required to launch; grants 1% vault)."
        )
    if amount > _MAX_BURN_TOKENS:
        return (
            f"Burn amount above cap: {amount:,} CLAWNCH "
            f"(max {_MAX_BURN_TOKENS:,} = 10% vault, the Clanker limit). "
            "Anything above this is wasted."
        )
    return amount


async def handle_burn(raw_args: str, **kwargs: Any) -> str:
    sender_id = _resolve_sender(kwargs)
    arg = (raw_args or "").strip()
    if not arg:
        out = _render_usage(_recall_burn(sender_id))
        _record("burn", raw_args, out)
        return out

    parts = arg.split()
    first = parts[0].lower()

    if first == "last":
        out = _render_last(_recall_burn(sender_id))
    else:
        parsed = _parse_amount(parts[0])
        if isinstance(parsed, str):
            out = parsed
        else:
            out = await _submit(sender_id, parsed)

    _record("burn", raw_args, out)
    return out


def _render_usage(last: dict[str, Any] | None) -> str:
    lines = [
        "Burn $CLAWNCH — required for every launch (min 1M); claims vault allocation.",
        "",
        "Usage:",
        "  /burn <amount>          — sign + submit a CLAWNCH burn from your wallet",
        "  /burn last              — show the most recent burn tx hash",
        "",
        "Vault curve: 1M CLAWNCH = 1% vault, 10M = 10% (Clanker max).",
        "             1k allocated tokens per 1 CLAWNCH burned.",
        "             7-day Clanker lockup applies.",
        "",
        "After burning, either:",
        "  /launch confirm                       (vault auto-applied if recent burn)",
        "  /launch burn <tx_hash>                (explicit attach to draft)",
        "  /api/prepare/deploy?burnTxHash=<hash> (Base MCP / external flow)",
    ]
    if last:
        lines.append("")
        lines.append("Most recent burn:")
        lines.append(f"  amount: {last['amount']:,} CLAWNCH")
        lines.append(f"  tx:     {last['tx_hash']}")
    return "\n".join(lines)


def _render_last(last: dict[str, Any] | None) -> str:
    if not last:
        return "No burn recorded this session. Run /burn <amount> first."
    return (
        f"Most recent burn: {last['amount']:,} CLAWNCH\n"
        f"  Tx: {last['tx_hash']}\n"
        f"  Basescan: https://basescan.org/tx/{last['tx_hash']}"
    )


async def _submit(sender_id: str, whole_tokens: int) -> str:
    """Sign + submit the CLAWNCH burn transfer via the active wallet."""
    from clawmes.lib.abi import encode_transfer
    from clawmes.services.clawnch import get_clawnch_service
    from clawmes.services.wallet import get_wallet_service, get_wallet_state

    state = get_wallet_state()
    if not state.connected:
        return "No wallet connected. Run /connect or /connect_local first."
    mode = get_wallet_service().active_mode
    if mode is None:
        return "No active wallet mode. Run /connect."

    cfg = get_clawnch_service().get_burn_config()
    token_addr = cfg["token_address"]
    burn_addr = cfg["burn_address"]
    amount_wei = whole_tokens * (10**18)
    calldata = encode_transfer(burn_addr, amount_wei)

    # Append Coinbase builder code suffix on Base mainnet.
    from clawmes.lib.base_builder import append_builder_code

    calldata = append_builder_code(calldata, 8453)

    try:
        tx_hash = mode.send_transaction(
            to=token_addr,
            value=0,
            data=calldata,
            chain_id=8453,
        )
    except Exception as exc:  # noqa: BLE001
        return f"Burn tx submission failed: {exc}"

    _remember_burn(sender_id, tx_hash, whole_tokens)
    return (
        f"Burn submitted: {whole_tokens:,} CLAWNCH → {burn_addr}\n"
        f"  Tx: {tx_hash}\n"
        f"  Basescan: https://basescan.org/tx/{tx_hash}\n"
        "\n"
        "Next: /launch burn <tx_hash> + /launch confirm  (claims vault on next deploy)."
    )


def register(ctx) -> None:
    ctx.register_command(
        name="burn",
        handler=handle_burn,
        description="Burn $CLAWNCH from the active wallet (required to launch; claims vault %)",
        args_hint="<amount> | last",
    )
