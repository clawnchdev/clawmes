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
    elif action == "confirm":
        out = await _confirm(sender_id)
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
            "  /launch bypass <tx_hash>      — skip 24h cooldown\n"
            "  /launch status                — show current draft\n"
            "  /launch confirm               — deploy\n"
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
        "  /launch bypass <tx_hash>         (skip 24h cooldown)",
        "  /launch status                   (show draft)",
        "  /launch confirm                  (deploy)",
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


async def _confirm(sender_id: str) -> str:
    draft = _get_draft(sender_id)
    name = draft.get("name")
    symbol = draft.get("symbol")
    if not name or not symbol:
        return (
            "Launch needs at minimum a name and a symbol. "
            "Run /launch name <…> and /launch symbol <TICKER> first."
        )

    token_params: dict[str, Any] = {"name": name, "symbol": symbol}
    if description := draft.get("description"):
        token_params["description"] = description
    if image := draft.get("image"):
        token_params["image"] = image
    socials = draft.get("socials") or {}
    if socials:
        token_params["metadata"] = {
            "socialMediaUrls": [
                {"platform": platform, "url": url} for platform, url in socials.items()
            ]
        }

    bypass = draft.get("bypass_tx_hash")

    try:
        from clawmes.services.clawnch import ClawnchError, get_clawnch_service

        result = get_clawnch_service().deploy(
            token_params=token_params,
            bypass_tx_hash=bypass,
        )
    except ClawnchError as exc:
        msg = f"Launch failed ({exc.code}): {exc.message}"
        if exc.code == "no_credentials":
            msg += "\n\nRun /register_agent <name> <description> to get a key."
        elif exc.code == "rate_limited":
            from clawmes.services.clawnch import get_clawnch_service as _svc

            bypass_info = _svc().get_bypass_recipient()
            msg += (
                f"\n\nBypass: send {bypass_info['fee_eth']} ETH to "
                f"{bypass_info['recipient']} on Base, then "
                f"/launch bypass <tx_hash> and /launch confirm."
            )
        return msg
    except Exception as exc:  # noqa: BLE001
        return f"Launch failed: {exc}"

    _clear_draft(sender_id)
    tx_hash = result.get("txHash") or result.get("tx_hash")
    token_address = result.get("tokenAddress") or result.get("token_address")
    lines = ["Launched."]
    if token_address:
        lines.append(f"  Token: {token_address}")
    if tx_hash:
        lines.append(f"  Tx: {tx_hash}")
        lines.append(f"  Chart: https://dexscreener.com/base/{token_address or tx_hash}")
    return "\n".join(lines)


def register(ctx) -> None:
    ctx.register_command(
        name="launch",
        handler=handle_launch,
        description="Deploy a token on Clawnch via guided chat flow",
        args_hint="[name | symbol | description | image | twitter | website | telegram | farcaster | discord | bypass | status | confirm | cancel] <value>",
    )
