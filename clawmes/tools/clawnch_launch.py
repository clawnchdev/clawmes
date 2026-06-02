"""``clawnch_launch`` — deploy a token via the Clawnch launchpad.

LLM-callable surface for the Clawnch deploy flow. Wraps
:class:`clawmes.services.clawnch.ClawnchService` which talks to the
launchpad HTTP API. Two actions:

  * ``deploy`` — submit a deploy. Service handles the captcha
    challenge (sign message + read storage slot + compute keccak
    proof), then posts the solution. Clawnch's deployer wallet pays
    gas + submits the underlying Clanker tx server-side. Optional
    ``bypass_tx_hash`` skips the 24h cooldown by paying ETH to the
    bypass recipient (see ``CLAWNCH_BYPASS_RECIPIENT``).
  * ``info`` — read launch metadata for an existing token.

Metadata: ``image`` + per-platform social URLs (``twitter``,
``website``, ``telegram``, ``farcaster``, ``discord``) are passed
through to the launchpad's ``tokenParams.metadata.socialMediaUrls``.
Each platform value is normalized (``@handle`` -> full URL when the
platform has a stable user URL like x.com / t.me / warpcast).

What used to be ``pair`` and ``seed_lp`` collapsed into ``deploy``:
Clanker handles ERC-20 deploy + Uniswap V4 pool + initial liquidity
seeding atomically in one call, so the multi-step ``pair`` /
``seed_lp`` actions don't apply against the live launchpad. They're
left out rather than stubbed.

Requires ``CLAWNCH_API_KEY``. Register an agent + obtain the key via
``/register_agent`` or directly against ``POST /api/agents/register``
+ ``POST /api/agents/verify`` on clawn.ch.
"""

from __future__ import annotations

from typing import Any

from clawmes.lib.logger import logger_for
from clawmes.lib.params import read_str
from clawmes.lib.tool_result import error_result, json_result
from clawmes.tools.registry import register_with_ctx, write_tool

_log = logger_for("tools.clawnch_launch")


# Map of (schema arg name, clawnch platform name, base URL). Base URL
# is empty for platforms that use full invite / handle URLs (discord,
# website). Matches the same set the /launch command exposes.
_SOCIAL_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("twitter", "twitter", "https://x.com/"),
    ("website", "website", ""),
    ("telegram", "telegram", "https://t.me/"),
    ("farcaster", "farcaster", "https://warpcast.com/"),
    ("discord", "discord", ""),
)


def _normalize_social(value: str, base_url: str) -> str:
    """Normalize a social handle / URL. Mirrors commands.launch logic.

    ``@handle`` or ``handle`` becomes ``base_url + handle`` when a
    base URL is provided; full ``http(s)://`` URLs pass through.
    Empty base URL = treat as a raw URL (just strip whitespace + add
    ``https://`` for bare hostnames).
    """
    v = value.strip()
    if v.startswith(("http://", "https://")):
        return v
    if base_url:
        handle = v.removeprefix("@").strip()
        return f"{base_url}{handle}" if handle else v
    # No base URL — bare-hostname autocomplete or pass through.
    if "." in v and " " not in v:
        return f"https://{v}"
    return v


_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["deploy", "info"],
        },
        "name": {"type": "string", "description": "Token name (deploy)."},
        "symbol": {"type": "string", "description": "Token symbol (deploy)."},
        "description": {
            "type": "string",
            "description": "One-line description of the token (deploy).",
        },
        "image": {
            "type": "string",
            "description": "Image URL for token metadata (deploy, optional).",
        },
        "twitter": {
            "type": "string",
            "description": (
                "X / Twitter handle (with or without @) or full URL (deploy, optional)."
            ),
        },
        "website": {
            "type": "string",
            "description": "Website URL (deploy, optional).",
        },
        "telegram": {
            "type": "string",
            "description": ("Telegram handle or invite URL (deploy, optional)."),
        },
        "farcaster": {
            "type": "string",
            "description": ("Farcaster handle or full Warpcast URL (deploy, optional)."),
        },
        "discord": {
            "type": "string",
            "description": "Discord invite URL (deploy, optional).",
        },
        "bypass_tx_hash": {
            "type": "string",
            "description": (
                "Tx hash of >= 0.001 ETH paid to the Clawnch bypass "
                "recipient. Skips the 24h deploy cooldown (deploy, "
                "optional)."
            ),
        },
        "token": {
            "type": "string",
            "description": "Token address (info).",
        },
        "policyConfirmationNonce": {
            "type": "string",
            "description": "Set when retrying after POLICY HOLD.",
        },
    },
    "required": ["action"],
}


@write_tool(
    name="clawnch_launch",
    toolset="clawmes-defi",
    description=(
        "Deploy a token on Base via the Clawnch launchpad. Clawnch "
        "handles the deploy + initial liquidity atomically via the "
        "Clanker SDK; the user's wallet signs a captcha challenge to "
        "prove identity. Supports image + social metadata (twitter / "
        "website / telegram / farcaster / discord). Requires "
        "CLAWNCH_API_KEY (register an agent with /register_agent)."
    ),
    schema=_SCHEMA,
    emoji="\U0001f31f",
)
def clawnch_launch(args: dict[str, Any], **kwargs: Any) -> str:
    action = read_str(args, "action", required=True)

    if action == "info":
        return _handle_info(args)
    return _handle_deploy(args)


def _handle_deploy(args: dict[str, Any]) -> str:
    from clawmes.services.clawnch import ClawnchError, get_clawnch_service

    name = read_str(args, "name")
    symbol = read_str(args, "symbol")
    if not name or not symbol:
        return error_result(
            "deploy requires both 'name' and 'symbol'.",
            code="param_error",
        )

    token_params: dict[str, Any] = {
        "name": name,
        "symbol": symbol,
    }
    if description := read_str(args, "description"):
        token_params["description"] = description
    if image := read_str(args, "image"):
        token_params["image"] = image

    # Build socialMediaUrls from individual platform args.
    socials: list[dict[str, str]] = []
    for arg_key, platform_name, base_url in _SOCIAL_FIELDS:
        if value := read_str(args, arg_key):
            socials.append({"platform": platform_name, "url": _normalize_social(value, base_url)})
    if socials:
        token_params["metadata"] = {"socialMediaUrls": socials}

    bypass = read_str(args, "bypass_tx_hash") or None

    try:
        result = get_clawnch_service().deploy(
            token_params=token_params,
            bypass_tx_hash=bypass,
        )
    except ClawnchError as exc:
        return error_result(exc.message, code=exc.code)
    except Exception as exc:  # noqa: BLE001
        return error_result(f"Launch failed: {exc}", code="api_error")

    tx_hash = result.get("txHash") or result.get("tx_hash")
    token_address = result.get("tokenAddress") or result.get("token_address")
    # Desktop UI: Clawnch launches are Base-only, so surface the tx explorer
    # link plus Clanker / DexScreener / token-explorer links for the brand-new
    # token as clickable Link artifacts. The enrich helpers no-op on missing or
    # malformed values, so we call them unconditionally (passive descriptive
    # keys — no preview auto-open from the tool itself).
    from clawmes.lib.ui_artifacts import enrich_token_links, enrich_tx_links

    enrich_tx_links(result, tx_hash=tx_hash or "", chain_id=8453)
    enrich_token_links(result, token=token_address or "", chain_id=8453)

    # Desktop UI: render a launch-receipt card and surface its path at the
    # envelope top level (json_result ``preview=``) so the desktop opens it in
    # the preview pane. clawnch_launch is user/LLM-invoked (never
    # scheduler-driven). Best-effort: never fail the launch on UI errors.
    preview_path: str | None = None
    try:
        from clawmes.lib.ui_cards import receipt_card, write_card

        rows = [("Symbol", symbol)]
        if token_address:
            rows.append(("Token", token_address))
        if tx_hash:
            rows.append(("Tx", tx_hash))
        links = [
            ("Clanker", result.get("clanker_url", "")),
            ("DexScreener", result.get("dexscreener_url", "")),
            ("Explorer", result.get("explorer_url", "")),
        ]
        card_html = receipt_card(title=f"Launched {symbol}", rows=rows, links=links)
        preview_path = str(write_card(card_html, f"launch-{symbol}"))
    except Exception:  # noqa: BLE001 — UI is best-effort
        preview_path = None

    summary_parts = [f"Launched {symbol} via Clawnch."]
    if token_address:
        summary_parts.append(f"Token: {token_address}")
    if tx_hash:
        summary_parts.append(f"Tx: {tx_hash}")
    return json_result(result, summary=" ".join(summary_parts), preview=preview_path)


def _handle_info(args: dict[str, Any]) -> str:
    from clawmes.services.clawnch import ClawnchError, get_clawnch_service

    token = read_str(args, "token")
    if not token:
        return error_result(
            "info requires 'token' (the launched token's address).",
            code="param_error",
        )
    try:
        data = get_clawnch_service().get_launch(token)
    except ClawnchError as exc:
        return error_result(exc.message, code=exc.code)
    except Exception as exc:  # noqa: BLE001
        return error_result(f"Info read failed: {exc}", code="api_error")
    return json_result(data, summary=f"Launch info for {token}")


def register(ctx) -> None:
    register_with_ctx(ctx, clawnch_launch)
