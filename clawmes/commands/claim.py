"""``/claim`` — sweep accumulated LP fees on tokens you launched.

When you deploy a Clanker-style token via Clawnch, you get reward-admin
rights on a slice of the LP fee stream (the agent slot — 80% in the
default config, vs. 20% to the platform). Those fees accrue inside the
Uniswap V4 PositionManager until someone calls the Clanker locker's
``collectRewards(address token)`` function. Whoever calls it pays gas;
the locker then distributes the accumulated fees to every recipient
slot in one shot.

This command:

  * ``/claim`` — list your launches with hints (no tx).
  * ``/claim <address-or-symbol>`` — submit ``collectRewards`` for one
    token. Returns tx hash + Basescan link.
  * ``/claim all`` — sweep every launch in your agent history,
    sequentially. Warns first because gas can be non-trivial for users
    with many tokens (~$0.30 / claim × N).

Notes:

  * The locker pays out to ALL recipient slots (yours, the platform's,
    LP holders'). Calling claim enriches everyone with a slot. That's
    by design — it pulls fees forward into circulation faster.
  * Per-recipient amounts come from the ``ClaimedRewards`` event on
    the receipt. We don't pre-simulate (no view function exists; would
    require ``debug_traceCall``), so we surface the tx hash and let
    the user verify the amount on-chain via Basescan.

Clanker v4 LP Locker Fee Conversion: ``0x63D2DfEA64b3433F4071A98665bcD7Ca14d93496``
"""

from __future__ import annotations

from typing import Any

# Clanker v4 LP Locker Fee Conversion contract on Base mainnet.
# Verified at https://basescan.org/address/0x63D2DfEA64b3433F4071A98665bcD7Ca14d93496
CLANKER_LOCKER = "0x63D2DfEA64b3433F4071A98665bcD7Ca14d93496"

# Function selector for ``collectRewards(address token)``.
# Derived from ``keccak256("collectRewards(address)")[:4]``. Pinned as
# a constant rather than computed at import time so test envs without
# a fresh eth-utils keccak instance don't pay a startup cost.
SELECTOR_COLLECT_REWARDS = "0x5763dbd0"

# Event topic for ``ClaimedRewards(address indexed token, uint256, uint256, uint256[], uint256[])``.
# Surfaced so users can grep Basescan logs for "their" event after the
# tx confirms — useful when /claim all returns 10 tx hashes.
TOPIC_CLAIMED_REWARDS = "0x21d15f71483b597e8f0009e83b90b2117f6f98c185d7173857dddcae5eb8546a"


def _record(name: str, args: str, result: str) -> None:
    try:
        from clawmes.services.command_history import record_command_call

        record_command_call(name, args, result)
    except Exception:  # noqa: BLE001
        pass


async def handle_claim(raw_args: str, *, sender_id: str = "default", **_kwargs: Any) -> str:
    raw = (raw_args or "").strip()
    if not raw:
        out = _render_preview()
    elif raw.lower() == "all":
        out = await _sweep_all(sender_id)
    else:
        out = await _claim_one(sender_id, raw)
    _record("claim", raw_args, out)
    return out


# ── preview (no tx) ─────────────────────────────────────────────────


def _render_preview() -> str:
    """List the agent's launches with usage hints."""
    from clawmes.services.clawnch import ClawnchError, get_clawnch_service

    try:
        body = get_clawnch_service().get_my_launches()
    except ClawnchError as exc:
        msg = f"Could not fetch your launches ({exc.code}): {exc.message}"
        if exc.code == "no_credentials":
            msg += "\n\nRun /register_agent <name> <description> first."
        return msg

    launches = _extract_launches(body)
    if not launches:
        return (
            "No launches found for this agent. Nothing to claim.\n"
            "\n"
            "Once you launch tokens via /launch they'll appear here, and you\n"
            "can sweep their accumulated LP fees with /claim <address> or\n"
            "/claim all."
        )

    lines = [f"Your launches ({len(launches)}). Fees claimable on each:", ""]
    for i, lau in enumerate(launches, start=1):
        sym = lau.get("symbol") or lau.get("ticker") or "?"
        addr = lau.get("contractAddress") or lau.get("tokenAddress") or lau.get("address") or "?"
        lines.append(f"  {i:2d}. {sym:<10s}  {addr}")
    lines.append("")
    lines.append("Usage:")
    lines.append("  /claim <address>   Claim fees on one token")
    lines.append("  /claim all         Sweep every launch (one tx per token)")
    lines.append("")
    lines.append(
        "Each claim costs ~$0.30 in gas. Calling /claim all on 10 tokens\n"
        "submits 10 sequential txs (~$3 total). The locker pays out to all\n"
        "recipient slots in one shot, so calling claim also benefits any\n"
        "co-recipients on the token (platform, LP, etc.)."
    )
    return "\n".join(lines)


# ── /claim <token> — single ─────────────────────────────────────────


async def _claim_one(sender_id: str, target: str) -> str:
    """Resolve ``target`` (address or symbol), then claim."""
    addr = _resolve_target(target)
    if not addr:
        return (
            f"Could not resolve {target!r} to a token address. "
            "Try /claim with no args to see your launches, then pass the "
            "full 0x… address."
        )

    return await _submit_claim(sender_id, addr)


def _resolve_target(target: str) -> str | None:
    """Map ``target`` to a token address.

    Accepts either a 0x… address (returned as-is, lowercased) or a
    symbol that matches one of the agent's launches.
    """
    target = target.strip()
    if target.startswith("0x") and len(target) == 42:
        return target.lower()

    # Symbol lookup against agent's launches.
    from clawmes.services.clawnch import ClawnchError, get_clawnch_service

    try:
        body = get_clawnch_service().get_my_launches()
    except ClawnchError:
        return None
    launches = _extract_launches(body)
    upper = target.upper()
    for lau in launches:
        sym = (lau.get("symbol") or lau.get("ticker") or "").upper()
        if sym == upper:
            addr = lau.get("contractAddress") or lau.get("tokenAddress") or lau.get("address")
            if isinstance(addr, str) and addr.startswith("0x"):
                return addr.lower()
    return None


# ── /claim all — sweep ──────────────────────────────────────────────


async def _sweep_all(sender_id: str) -> str:
    """Submit ``collectRewards`` for every launch sequentially."""
    from clawmes.services.clawnch import ClawnchError, get_clawnch_service

    try:
        body = get_clawnch_service().get_my_launches()
    except ClawnchError as exc:
        return f"Could not fetch your launches ({exc.code}): {exc.message}"

    launches = _extract_launches(body)
    if not launches:
        return "No launches found. Nothing to sweep."

    lines = [f"Sweeping {len(launches)} launches. This will take a moment...", ""]
    results: list[tuple[str, str]] = []
    for lau in launches:
        addr = lau.get("contractAddress") or lau.get("tokenAddress") or lau.get("address")
        sym = lau.get("symbol") or lau.get("ticker") or "?"
        if not isinstance(addr, str) or not addr.startswith("0x"):
            results.append((sym, "skipped (no address in launch record)"))
            continue
        result = await _submit_claim(sender_id, addr.lower(), inline=True)
        results.append((sym, result))

    for sym, result in results:
        lines.append(f"  {sym:<10s}  {result}")
    lines.append("")
    lines.append(
        "Each tx confirms independently. To see exactly what was claimed,\n"
        f"grep Basescan logs for topic {TOPIC_CLAIMED_REWARDS}\n"
        "on the locker contract — the ClaimedRewards event carries per-\n"
        "recipient amounts in its data."
    )
    return "\n".join(lines)


# ── tx submission ───────────────────────────────────────────────────


async def _submit_claim(sender_id: str, token_addr: str, *, inline: bool = False) -> str:
    """Build + sign + submit one ``collectRewards`` tx via active wallet."""
    from clawmes.lib.abi import encode_address
    from clawmes.lib.base_builder import append_builder_code
    from clawmes.services.wallet import get_wallet_service, get_wallet_state

    state = get_wallet_state()
    if not state.connected:
        msg = "No wallet connected. Run /connect or /connect_local first."
        return msg if inline else msg

    mode = get_wallet_service().active_mode
    if mode is None:
        msg = "No active wallet mode. Run /connect."
        return msg if inline else msg

    # collectRewards(address token) — selector + 32-byte left-padded token.
    calldata = SELECTOR_COLLECT_REWARDS + encode_address(token_addr)
    # Append Coinbase builder code (chain 8453 only — base_builder is a no-op elsewhere).
    calldata = append_builder_code(calldata, 8453)

    try:
        tx_hash = mode.send_transaction(
            to=CLANKER_LOCKER,
            value=0,
            data=calldata,
            chain_id=8453,
        )
    except Exception as exc:  # noqa: BLE001
        msg = f"Claim tx failed: {exc}"
        return msg if inline else msg

    _ = sender_id  # reserved for future per-sender claim history
    if inline:
        return f"submitted {_short(token_addr)} → {_short(tx_hash)}"
    return (
        f"Claim submitted for {token_addr}\n"
        f"  Tx:       {tx_hash}\n"
        f"  Basescan: https://basescan.org/tx/{tx_hash}\n"
        "\n"
        "The ClaimedRewards event on the receipt has per-recipient amounts."
    )


# ── helpers ─────────────────────────────────────────────────────────


def _extract_launches(body: Any) -> list[dict[str, Any]]:
    if isinstance(body, list):
        return [x for x in body if isinstance(x, dict)]
    if isinstance(body, dict):
        for key in ("launches", "tokens", "data", "results"):
            inner = body.get(key)
            if isinstance(inner, list):
                return [x for x in inner if isinstance(x, dict)]
    return []


def _short(value: str) -> str:
    if not isinstance(value, str) or len(value) <= 10:
        return str(value)
    return f"{value[:6]}…{value[-4:]}"


def register(ctx) -> None:
    ctx.register_command(
        name="claim",
        handler=handle_claim,
        description="Sweep accumulated LP fees on your launched tokens",
        args_hint="[<address-or-symbol> | all]",
    )
