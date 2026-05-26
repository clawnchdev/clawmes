"""``/launch`` slash command — guided token deploy on Clawnch.

Multi-turn flow that walks the user through deploying a token via the
Clawnch launchpad from chat. State is held per ``sender_id`` so a
single channel can have multiple deploys in progress concurrently.

Steps:

  Core:
    ``/launch name <token name>``
    ``/launch symbol <ticker>``
    ``/launch description <text>``        (optional)

  Metadata:
    ``/launch image <url>``               (optional — token image)
    ``/launch twitter <handle | url>``    (optional — X / Twitter)
    ``/launch website <url>``             (optional)
    ``/launch telegram <handle | url>``   (optional)
    ``/launch farcaster <handle | url>``  (optional)
    ``/launch discord <url>``             (optional)

  Flow:
    ``/launch bypass <tx_hash>``          (optional — skip 24h cooldown)
    ``/launch status``                    (show draft)
    ``/launch confirm``                   (deploy)
    ``/launch cancel``                    (clear)

Auth: requires ``CLAWNCH_API_KEY`` in env (Clawnch refuses unauth'd
deploys). The user-facing error from the underlying call tells the
user how to get a key (``/register_agent``).

Wallet: the active wallet mode must be connected — the captcha
challenge is signed via ``personal_sign``.
"""

from __future__ import annotations

import threading
from typing import Any

_log_state: dict[str, dict[str, Any]] = {}
_state_lock = threading.RLock()


# ── social link normalization ───────────────────────────────────────


def _normalize_handle_or_url(value: str, base_url: str) -> str:
    """Turn ``@handle`` / ``handle`` / full URL into a full URL.

    Falls back to the raw value when nothing else fits — Clawnch's
    socialMediaUrls is a free-form ``{platform, url}`` list, so we
    don't strictly need URLs; this is just for ergonomics so a user
    can type ``/launch twitter clawnchbot`` and get something sane.
    """
    v = value.strip()
    if v.startswith(("http://", "https://")):
        return v
    handle = v.removeprefix("@").strip()
    return f"{base_url}{handle}" if handle else v


def _normalize_url(value: str) -> str:
    """Pass-through normalizer for URLs (website, telegram, discord).

    Strips whitespace. Adds ``https://`` if missing for bare hostnames
    like ``example.com``. Returns the raw value if the input doesn't
    look hostname-shaped.
    """
    v = value.strip()
    if v.startswith(("http://", "https://")):
        return v
    if "." in v and " " not in v:
        return f"https://{v}"
    return v


# Map of `/launch <platform>` arg to (clawnch platform name, normalizer).
# Keeping the clawnch platform names stable since the launchpad UI may
# render badges keyed on these strings (twitter, telegram, etc.).
_SOCIAL_PLATFORMS: dict[str, tuple[str, str]] = {
    "twitter": ("twitter", "https://x.com/"),
    "x": ("twitter", "https://x.com/"),  # alias
    "telegram": ("telegram", "https://t.me/"),
    "farcaster": ("farcaster", "https://warpcast.com/"),
    "discord": ("discord", ""),  # discord uses full invite URLs
    "website": ("website", ""),  # plain URL
}


# ── per-sender state helpers ────────────────────────────────────────


def _record(name: str, args: str, result: str) -> None:
    """Best-effort recording into command_history. Matches the existing
    pattern from other clawmes commands."""
    try:
        from clawmes.services.command_history import record_command_call

        record_command_call(name, args, result)
    except Exception:  # noqa: BLE001
        pass


def _get_draft(sender_id: str) -> dict[str, Any]:
    with _state_lock:
        draft = _log_state.get(sender_id)
        if draft is None:
            draft = {}
            _log_state[sender_id] = draft
        return draft


def _clear_draft(sender_id: str) -> None:
    with _state_lock:
        _log_state.pop(sender_id, None)


def _resolve_sender(kwargs: dict[str, Any]) -> str:
    return str(kwargs.get("sender_id") or "default")


def _set_social(draft: dict[str, Any], platform_arg: str, value: str) -> str:
    """Record a social link on the draft. Returns user-facing message."""
    cfg = _SOCIAL_PLATFORMS[platform_arg]
    platform_name, base_url = cfg
    if base_url:
        url = _normalize_handle_or_url(value, base_url)
    else:
        url = _normalize_url(value)
    socials = draft.setdefault("socials", {})
    socials[platform_name] = url
    return f"{platform_name.capitalize()} set: {url}"


# ── main handler ────────────────────────────────────────────────────


async def handle_launch(raw_args: str, **kwargs: Any) -> str:
    sender_id = _resolve_sender(kwargs)
    arg = (raw_args or "").strip()
    parts = arg.split(maxsplit=1)

    if not arg:
        out = _render_usage(_get_draft(sender_id))
        _record("launch", raw_args, out)
        return out

    action = parts[0].lower()
    rest = parts[1].strip() if len(parts) > 1 else ""

    if action == "status":
        out = _render_status(_get_draft(sender_id))
    elif action == "cancel":
        _clear_draft(sender_id)
        out = "Launch draft cleared."
    elif action == "name":
        draft = _get_draft(sender_id)
        if not rest:
            out = "Usage: /launch name <token name>"
        else:
            draft["name"] = rest
            out = f"Name set: {rest}. Next: /launch symbol <TICKER>"
    elif action == "symbol":
        draft = _get_draft(sender_id)
        if not rest:
            out = "Usage: /launch symbol <TICKER>"
        else:
            draft["symbol"] = rest.upper()
            out = (
                f"Symbol set: {draft['symbol']}. "
                "Add metadata (/launch image, /launch twitter, /launch website…) "
                "or /launch confirm."
            )
    elif action == "description":
        draft = _get_draft(sender_id)
        if not rest:
            out = "Usage: /launch description <text>"
        else:
            draft["description"] = rest
            out = "Description set."
    elif action == "image":
        draft = _get_draft(sender_id)
        if not rest:
            out = "Usage: /launch image <url>"
        else:
            draft["image"] = _normalize_url(rest)
            out = f"Image set: {draft['image']}"
    elif action in _SOCIAL_PLATFORMS:
        draft = _get_draft(sender_id)
        if not rest:
            out = f"Usage: /launch {action} <handle or url>"
        else:
            out = _set_social(draft, action, rest)
    elif action == "bypass":
        draft = _get_draft(sender_id)
        if not rest:
            out = "Usage: /launch bypass <tx_hash>"
        else:
            draft["bypass_tx_hash"] = rest
            out = "Bypass tx recorded. Next: /launch confirm."
    elif action == "burn":
        out = await _handle_burn(sender_id, rest)
    elif action == "confirm":
        out = await _confirm(sender_id, rest)
    elif action == "export":
        out = await _export(sender_id)
    elif action == "alerts":
        out = _render_alerts(rest)
    else:
        out = (
            f"Unknown /launch arg {action!r}. Use:\n"
            "  /launch                       — show this help + draft\n"
            "  /launch name <name>           — set token name\n"
            "  /launch symbol <TICKER>       — set ticker\n"
            "  /launch description <text>    — optional description\n"
            "  /launch image <url>           — token image URL\n"
            "  /launch twitter <handle|url>  — X / Twitter (alias: /launch x)\n"
            "  /launch website <url>         — website\n"
            "  /launch telegram <handle|url> — Telegram\n"
            "  /launch farcaster <handle>    — Farcaster\n"
            "  /launch discord <url>         — Discord\n"
            "  /launch bypass <tx_hash>      — skip 24h cooldown (custodial mode only)\n"
            "  /launch burn <amount|tx_hash> — burn $CLAWNCH for vault allocation\n"
            "  /launch status                — show current draft\n"
            "  /launch confirm               — deploy (non-custodial by default)\n"
            "  /launch confirm --custodial   — deploy via Clawnch's custodial deployer\n"
            "  /launch export                — emit unsigned calldata for Base MCP / external signing\n"
            "  /launch alerts [source]       — subscribe to launch alerts (Telegram channel + filter docs)\n"
            "  /launch cancel                — clear draft"
        )
    _record("launch", raw_args, out)
    return out


def _render_usage(draft: dict[str, Any]) -> str:
    lines = [
        "Launch a token on Clawnch (guided flow).",
        "",
        "Required:",
        "  /launch name <token name>",
        "  /launch symbol <TICKER>",
        "",
        "Optional metadata:",
        "  /launch description <text>",
        "  /launch image <url>",
        "  /launch twitter <handle|url>     (or /launch x)",
        "  /launch website <url>",
        "  /launch telegram <handle|url>",
        "  /launch farcaster <handle|url>",
        "  /launch discord <url>",
        "",
        "Flow:",
        "  /launch bypass <tx_hash>         (skip 24h cooldown — custodial only)",
        "  /launch burn <amount|tx_hash>    (burn $CLAWNCH for vault allocation)",
        "  /launch status                   (show draft)",
        "  /launch confirm                  (deploy via wallet — non-custodial default)",
        "  /launch confirm --custodial      (deploy via Clawnch deployer wallet)",
        "  /launch export                   (emit unsigned calldata for Base MCP)",
        "  /launch cancel                   (clear draft)",
        "",
        "Requires CLAWNCH_API_KEY. Use /register_agent if you need a key.",
        "Active wallet must be connected — the deploy signs a captcha.",
    ]
    if draft:
        lines.append("")
        lines.append("Current draft:")
        lines.extend(_format_draft_lines(draft))
    return "\n".join(lines)


def _render_status(draft: dict[str, Any]) -> str:
    if not draft:
        return "No launch draft. Start with /launch name <token name>."
    lines = ["Launch draft:"]
    lines.extend(_format_draft_lines(draft))
    return "\n".join(lines)


def _format_draft_lines(draft: dict[str, Any]) -> list[str]:
    """Format draft for display. Socials get nested rendering for clarity."""
    out: list[str] = []
    for key, value in draft.items():
        if key == "socials" and isinstance(value, dict):
            out.append("  socials:")
            for platform, url in value.items():
                out.append(f"    {platform}: {url}")
        else:
            out.append(f"  {key}: {value}")
    return out


async def _handle_burn(sender_id: str, rest: str) -> str:
    """Handle ``/launch burn <amount|tx_hash>``.

    Two input modes:

      * ``tx_hash`` (``0x`` + 64 hex chars) — record verbatim. The user
        has already done the burn off-band and just wants to provide
        the hash for verification.

      * ``amount`` (positive integer >= 1,000,000) — sign + submit a
        $CLAWNCH ``transfer(burn_address, amount * 1e18)`` from the
        active wallet, wait for the receipt, and store the resulting
        tx hash on the draft. Saves the user from having to manually
        construct the burn tx in their wallet UI.

    Either way the draft ends up with ``burn_tx_hash`` set, which is
    forwarded to the Clawnch ``/api/deploy`` endpoint on confirm.
    Clawnch's backend verifies the burn (sender, recipient, amount,
    timing) and applies the corresponding vault allocation.
    """
    draft = _get_draft(sender_id)
    if not rest:
        return (
            "Usage: /launch burn <amount> | /launch burn <tx_hash>\n"
            "  Amount: whole CLAWNCH (>= 1,000,000 for 1% vault, max 10,000,000 for 10%).\n"
            "  Tx hash: 0x... if you already burned externally."
        )

    if _looks_like_tx_hash(rest):
        draft["burn_tx_hash"] = rest
        return "Burn tx recorded. Next: /launch confirm."

    try:
        amount = int(rest.replace("_", "").replace(",", ""))
    except ValueError:
        return f"Invalid burn input {rest!r}. Expected an amount (e.g. 1000000) or 0x tx hash."
    if amount < 1_000_000:
        return (
            f"Burn amount too low: {amount:,} CLAWNCH (minimum 1,000,000 for 1% vault).\n"
            "See https://clawn.ch/docs/burn for the vault curve."
        )

    return await _submit_burn(draft, amount)


async def _submit_burn(draft: dict[str, Any], whole_tokens: int) -> str:
    """Sign + submit the CLAWNCH burn tx via the active wallet."""
    from clawmes.lib.abi import encode_transfer
    from clawmes.services.clawnch import get_clawnch_service
    from clawmes.services.wallet import get_wallet_service, get_wallet_state

    state = get_wallet_state()
    if not state.connected:
        return "No wallet connected. Run /connect or /connect_local first."
    mode = get_wallet_service().active_mode()
    if mode is None:
        return "No active wallet mode. Run /connect."

    cfg = get_clawnch_service().get_burn_config()
    token_addr = cfg["token_address"]
    burn_addr = cfg["burn_address"]
    amount_wei = whole_tokens * (10**18)
    calldata = encode_transfer(burn_addr, amount_wei)

    try:
        tx_hash = mode.send_transaction(
            to=token_addr,
            value=0,
            data=calldata,
            chain_id=8453,
        )
    except Exception as exc:  # noqa: BLE001
        return f"Burn tx submission failed: {exc}"

    draft["burn_tx_hash"] = tx_hash
    draft["burn_amount"] = whole_tokens
    return (
        f"Burn submitted: {whole_tokens:,} CLAWNCH → {burn_addr}\n"
        f"  Tx: {tx_hash}\n"
        f"  Basescan: https://basescan.org/tx/{tx_hash}\n"
        "Wait for confirmation, then /launch confirm to deploy with vault allocation."
    )


def _looks_like_tx_hash(value: str) -> bool:
    """Heuristic: ``0x`` + 64 hex chars."""
    if not value.startswith("0x"):
        return False
    body = value[2:]
    if len(body) != 64:
        return False
    return all(c in "0123456789abcdefABCDEF" for c in body)


def _resolve_confirm_mode(flag_str: str, wallet_connected: bool) -> str:
    """Pick the deploy path from the confirm-arg flag + wallet state.

    Returns one of ``"noncustodial"`` or ``"custodial"``.

    Rules:
      * ``--custodial`` → custodial (regardless of wallet state).
      * ``--noncustodial`` → non-custodial (errors later if no wallet).
      * Default (no flag) → non-custodial when a wallet is connected,
        else custodial. The new non-custodial path is the better
        default — no API key required, no 24h cooldown, user pays gas.
        But we fall back to custodial when no wallet is connected so
        chat-only users on a stale install don't get a confusing
        "no wallet" error.
    """
    flags = flag_str.split()
    if "--custodial" in flags:
        return "custodial"
    if "--noncustodial" in flags:
        return "noncustodial"
    return "noncustodial" if wallet_connected else "custodial"


def _build_token_params(draft: dict[str, Any], *, name: str, symbol: str) -> dict[str, Any]:
    """Build the ``tokenParams`` dict that gets passed to ``ClawnchService.deploy``.

    Shared between custodial and non-custodial flows so social-media URLs +
    description + image render identically on the launchpad regardless of
    which deploy path the user picked.
    """
    params: dict[str, Any] = {"name": name, "symbol": symbol}
    if description := draft.get("description"):
        params["description"] = description
    if image := draft.get("image"):
        params["image"] = image
    socials = draft.get("socials") or {}
    if socials:
        params["metadata"] = {
            "socialMediaUrls": [
                {"platform": platform, "url": url} for platform, url in socials.items()
            ]
        }
    return params


async def _confirm(sender_id: str, flag_str: str) -> str:
    draft = _get_draft(sender_id)
    name = draft.get("name")
    symbol = draft.get("symbol")
    if not name or not symbol:
        return (
            "Launch needs at minimum a name and a symbol. "
            "Run /launch name <…> and /launch symbol <TICKER> first."
        )

    from clawmes.services.wallet import get_wallet_state

    wallet_state = get_wallet_state()
    mode = _resolve_confirm_mode(flag_str, wallet_connected=wallet_state.connected)

    if mode == "noncustodial":
        if not wallet_state.connected or not wallet_state.address:
            return (
                "Non-custodial /launch needs a connected wallet to sign the deploy.\n"
                "Run /connect or /connect_local first, or use /launch confirm --custodial "
                "for the API-key path."
            )
        return await _confirm_noncustodial(sender_id, draft, wallet_state)
    return await _confirm_custodial(sender_id, draft, name=name, symbol=symbol)


async def _confirm_noncustodial(
    sender_id: str,
    draft: dict[str, Any],
    wallet_state: Any,
) -> str:
    """Non-custodial path: prepare unsigned calldata, sign + submit via wallet.

    The Clawnch backend's ``/api/prepare/deploy`` returns a
    ``deployToken`` calldata for the Clanker factory, with the 20%
    platform fee already embedded in the rewards array. We sign it via
    the active wallet mode's ``send_transaction`` — the user (or their
    phone, in WalletConnect mode) approves the tx and pays gas.

    No API key required. No captcha. No 24h cooldown. Just one signed
    transaction.
    """
    from clawmes.services.clawnch import ClawnchError, get_clawnch_service
    from clawmes.services.rpc import RpcError, get_rpc_service
    from clawmes.services.wallet import get_wallet_service

    name = draft["name"]
    symbol = draft["symbol"]
    description = draft.get("description") or None
    image = draft.get("image") or None
    socials = draft.get("socials") or {}
    burn = draft.get("burn_tx_hash")

    try:
        prepared = get_clawnch_service().prepare_deploy(
            from_address=wallet_state.address,
            name=name,
            symbol=symbol,
            description=description,
            image=image,
            twitter=socials.get("twitter"),
            website=socials.get("website"),
            telegram=socials.get("telegram"),
            farcaster=socials.get("farcaster"),
            discord=socials.get("discord"),
            burn_tx_hash=burn,
        )
    except ClawnchError as exc:
        msg = f"Prepare failed ({exc.code}): {exc.message}"
        if exc.code == "rate_limited":
            msg += "\n\nPer-wallet prepare limit reached (10/day). Try again after 00:00 UTC."
        elif exc.code == "bad_request":
            msg += "\n\nCheck /launch status — name, symbol, and any burn tx hash all need to be valid."
        return msg
    except Exception as exc:  # noqa: BLE001
        return f"Prepare failed: {exc}"

    data = prepared.get("data") or {}
    meta = prepared.get("meta") or {}
    to_addr = data.get("to")
    calldata = data.get("data")
    chain_id = data.get("chainId") or 8453
    if not to_addr or not calldata:
        return f"Prepare returned an unexpected shape: {prepared!r}"

    mode = get_wallet_service().active_mode()
    if mode is None:
        return "No active wallet mode. Run /connect."
    try:
        tx_hash = mode.send_transaction(
            to=to_addr,
            value=0,
            data=calldata,
            chain_id=chain_id,
        )
    except Exception as exc:  # noqa: BLE001
        return f"Deploy submission failed: {exc}"

    # Wait for receipt so we can surface the new token address. Best
    # effort — if the receipt poll fails we still show the tx hash.
    token_address = ""
    try:
        receipt = get_rpc_service().wait_for_receipt(tx_hash, chain_id, timeout=180.0)
        # Clanker factory emits TokenCreated(address indexed token, ...)
        # as its first log; the token address is in topics[1].
        logs = receipt.get("logs") or []
        if logs:
            topics = (logs[0] or {}).get("topics") or []
            if len(topics) >= 2:
                topic = topics[1]
                if isinstance(topic, str) and len(topic) >= 42:
                    token_address = "0x" + topic[-40:]
    except (RpcError, Exception):  # noqa: BLE001 — receipt is best-effort
        pass

    _clear_draft(sender_id)
    lines = [
        f"Launched (non-custodial). {meta.get('platformFeeBps', 2000) / 100:.0f}% platform fee preserved.",
        f"  Tx: {tx_hash}",
        f"  Basescan: https://basescan.org/tx/{tx_hash}",
    ]
    if token_address:
        lines.append(f"  Token: {token_address}")
        lines.append(f"  Chart: https://dexscreener.com/base/{token_address}")
    vault_pct = meta.get("vaultPercentage") or 0
    if vault_pct:
        lines.append(f"  Vault: {vault_pct}% (Clanker lockup applies)")
    return "\n".join(lines)


async def _confirm_custodial(
    sender_id: str,
    draft: dict[str, Any],
    *,
    name: str,
    symbol: str,
) -> str:
    """Custodial path: original Clawnch API flow (captcha + server deploys).

    Kept for backwards-compat and for users who can't / don't want to
    pay deploy gas themselves. Requires ``CLAWNCH_API_KEY``.
    """
    token_params = _build_token_params(draft, name=name, symbol=symbol)
    bypass = draft.get("bypass_tx_hash")
    burn = draft.get("burn_tx_hash")

    try:
        from clawmes.services.clawnch import ClawnchError, get_clawnch_service

        result = get_clawnch_service().deploy(
            token_params=token_params,
            bypass_tx_hash=bypass,
            burn_tx_hash=burn,
        )
    except ClawnchError as exc:
        msg = f"Launch failed ({exc.code}): {exc.message}"
        if exc.code == "no_credentials":
            msg += "\n\nRun /register_agent <name> <description> to get a key, or drop --custodial to use the wallet-signed path."
        elif exc.code == "rate_limited":
            from clawmes.services.clawnch import get_clawnch_service as _svc

            bypass_info = _svc().get_bypass_recipient()
            msg += (
                f"\n\nBypass: send {bypass_info['fee_eth']} ETH to "
                f"{bypass_info['recipient']} on Base, then "
                f"/launch bypass <tx_hash> and /launch confirm --custodial.\n"
                f"Or drop --custodial to use the non-custodial path (no cooldown)."
            )
        return msg
    except Exception as exc:  # noqa: BLE001
        return f"Launch failed: {exc}"

    _clear_draft(sender_id)
    tx_hash = result.get("txHash") or result.get("tx_hash")
    token_address = result.get("tokenAddress") or result.get("token_address")
    lines = ["Launched (custodial)."]
    if token_address:
        lines.append(f"  Token: {token_address}")
    if tx_hash:
        lines.append(f"  Tx: {tx_hash}")
        lines.append(f"  Chart: https://dexscreener.com/base/{token_address or tx_hash}")
    return "\n".join(lines)


def _render_alerts(rest: str) -> str:
    """``/launch alerts [source]`` — point users at the launch-alerts feed.

    The Clawnch backend posts every successful deploy to the public
    Telegram channel ``@ClawnchAlerts``. This command surfaces the
    subscription link + documents how to filter the feed client-side
    (Telegram's built-in keyword filters / muted words).

    Optional ``source`` argument shows the exact keyword to filter on
    so the user can mute everything except (e.g.) ``base-mcp`` deploys
    in their own client.
    """
    valid_sources = {
        "clawmes",
        "moltbook",
        "4claw",
        "clawtomaton",
        "moltx",
        "base-mcp",
        "clawncher",
    }
    arg = (rest or "").strip().lower()
    if arg and arg not in valid_sources:
        return (
            f"Unknown source {arg!r}. Known sources: "
            f"{', '.join(sorted(valid_sources))}. Drop the arg to see general subscribe info."
        )

    lines = [
        "Launch alerts feed:",
        "",
        "  Channel:  https://t.me/ClawnchAlerts",
        "  Web:      https://www.clawn.ch/api/launches (read-only JSON feed)",
        "",
        "Every successful Clawnch deploy lands in the channel within seconds. "
        "Custodial deploys post from the server. Non-custodial (base-mcp) "
        "deploys post via the on-chain indexer cron, with up to ~5min latency.",
    ]
    if arg:
        lines.extend(
            [
                "",
                f"Filtering for source = {arg!r}:",
                f"  In Telegram, mute the channel and add {arg!r} to your client's keyword highlights. "
                f"Each post tags the source as `via {arg.capitalize() if arg != 'base-mcp' else 'Base MCP'}`, so a substring "
                f"match catches the lot.",
            ]
        )
    else:
        lines.extend(
            [
                "",
                "To filter to a specific source (clawmes, moltbook, 4claw, base-mcp, etc.), "
                "run /launch alerts <source> for client-side filter docs.",
            ]
        )
    return "\n".join(lines)


async def _export(sender_id: str) -> str:
    """``/launch export`` — emit unsigned Clanker calldata for external signing.

    Useful when the user is on Base MCP, Claude Desktop, Cursor, or any
    other agent surface that has its own wallet-signing UX. clawmes
    prepares the calldata locally; the user copies the JSON and pastes
    it into the other surface's ``send_calls`` flow.

    Doesn't touch the user's clawmes wallet — purely a prepare-only
    path. ``from`` is the active wallet's address if connected, else
    a placeholder the user must replace.
    """
    draft = _get_draft(sender_id)
    name = draft.get("name")
    symbol = draft.get("symbol")
    if not name or not symbol:
        return (
            "Export needs at minimum a name and a symbol. "
            "Run /launch name <…> and /launch symbol <TICKER> first."
        )

    from clawmes.services.clawnch import ClawnchError, get_clawnch_service
    from clawmes.services.wallet import get_wallet_state

    state = get_wallet_state()
    from_addr = state.address or "0x" + "0" * 40
    socials = draft.get("socials") or {}

    try:
        prepared = get_clawnch_service().prepare_deploy(
            from_address=from_addr,
            name=name,
            symbol=symbol,
            description=draft.get("description") or None,
            image=draft.get("image") or None,
            twitter=socials.get("twitter"),
            website=socials.get("website"),
            telegram=socials.get("telegram"),
            farcaster=socials.get("farcaster"),
            discord=socials.get("discord"),
            burn_tx_hash=draft.get("burn_tx_hash"),
        )
    except ClawnchError as exc:
        return f"Export failed ({exc.code}): {exc.message}"
    except Exception as exc:  # noqa: BLE001
        return f"Export failed: {exc}"

    import json

    data = prepared.get("data") or {}
    meta = prepared.get("meta") or {}
    block = json.dumps({"chain": "base", "calls": [data]}, indent=2)
    lines = [
        "Unsigned calldata ready to paste into Base MCP / Claude Desktop / Cursor:",
        "",
        block,
        "",
        f"Platform fee preserved: {meta.get('platformFeeBps', 2000)} bps (20% to Clawnch).",
        f"User fee share: {meta.get('userFeeBps', 8000)} bps.",
        f"Vault: {meta.get('vaultPercentage', 0)}%.",
        "",
        "On the receiving surface, pass `calls` to `send_calls`. The user signs in their wallet — no clawmes action needed.",
    ]
    if not state.address:
        lines.append("")
        lines.append(
            "⚠ No wallet connected — `from` is set to the zero address. "
            "Edit the calldata's `from` before signing, or /connect first and re-run."
        )
    return "\n".join(lines)


def register(ctx) -> None:
    ctx.register_command(
        name="launch",
        handler=handle_launch,
        description="Deploy a token on Clawnch via guided chat flow",
        args_hint="[name | symbol | description | image | twitter | website | telegram | farcaster | discord | bypass | status | confirm | cancel] <value>",
    )
