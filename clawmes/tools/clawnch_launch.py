"""``clawnch_launch`` — deploy a token via the Clawnch launchpad.

LLM-callable surface for the Clawnch deploy flow. Wraps
:class:`clawmes.services.clawnch.ClawnchService` which talks to the
launchpad HTTP API. Two surfaces, four actions:

Base (Clanker) — ``deploy`` submits a deploy through the custodial
flow. The service handles the captcha challenge (sign message + read
storage slot + compute keccak proof), then posts the solution.
Clawnch's deployer wallet pays gas + submits the underlying Clanker
tx server-side. Requires ``burn_tx_hash`` — every Base launch needs a
verified 1,000,000+ $CLAWNCH burn (the launchpad rejects no-burn
deploys with ``burn_required``). Optional ``bypass_tx_hash`` skips the
24h cooldown by paying ETH to the bypass recipient.

Robinhood Chain (Bags.fm launch router) — the Clanker path does not
exist on RHC:

  * ``rh_ticket`` — POST ``/api/robinhood/ticket``: the unsigned
    ``launch()`` tx + EIP-712 ticket for the agent wallet to sign and
    pay for.
  * ``rh_confirm`` — POST ``/api/robinhood/launch`` ``mode="confirm"``:
    record the broadcast ticket-path launch.
  * ``rh_deposit`` — POST ``/api/robinhood/launch`` ``mode="deposit"``:
    launch from a plain ETH deposit to the router's deposit address.
  * ``rh_token`` — the RHC $CLAWNCH address + trade/explorer links.

``info`` reads launch metadata. ``chain`` on ``deploy`` is Base-only:
a robinhood request raises ``unsupported_chain`` with the RHC
alternatives rather than deploying through the wrong backend.

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
            "enum": ["deploy", "info", "rh_ticket", "rh_confirm", "rh_deposit", "rh_token"],
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
                "Tx hash of >= 0.005 ETH paid to the Clawnch bypass "
                "recipient. Skips the 24h deploy cooldown (deploy, "
                "optional)."
            ),
        },
        "burn_tx_hash": {
            "type": "string",
            "description": (
                "Tx hash of a verified 1,000,000+ $CLAWNCH burn from "
                "the agent's wallet to the dead address within 24h. "
                "Required for every Base deploy — the launchpad rejects "
                "no-burn launches with code 'burn_required'. Not used "
                "on Robinhood Chain (Bags does not burn)."
            ),
        },
        "chain": {
            "type": "string",
            "description": (
                "Launch surface for 'deploy'. Only 'base' (default, "
                "Clanker) is served by that action; Robinhood Chain "
                "launches use the rh_ticket / rh_deposit actions."
            ),
        },
        "from_address": {
            "type": "string",
            "description": (
                "The wallet that signs / pays for the launch (rh_ticket, "
                "rh_confirm, rh_deposit). On the RHC actions this must be "
                "the agent wallet registered with Clawnch."
            ),
        },
        "fee_recipient": {
            "type": "string",
            "description": (
                "Address receiving creator fees on RHC launches "
                "(rh_ticket; optional, defaults to from_address)."
            ),
        },
        "tx_hash": {
            "type": "string",
            "description": (
                "Ticket-path launch tx hash to record (rh_confirm), sent "
                "from the agent wallet to the launch router."
            ),
        },
        "deposit_tx_hash": {
            "type": "string",
            "description": (
                "Plain ETH transfer hash to the Clawnch RHC deposit "
                "address (rh_deposit) — see meta.depositAddress from "
                "rh_ticket."
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
        "Deploy a token via the Clawnch launchpad. 'deploy' launches on "
        "Base via Clanker (requires a verified 1,000,000+ $CLAWNCH burn; "
        "use /burn to submit one) — it is Base-only. Robinhood Chain "
        "launches use the Bags.fm router: 'rh_ticket' returns the unsigned "
        "launch() tx + EIP-712 ticket the agent's wallet signs and pays "
        "for, 'rh_confirm' records that tx, and 'rh_deposit' launches from "
        "a plain ETH deposit to the router deposit address. 'rh_token' "
        "returns the RHC $CLAWNCH address + links. 'info' reads launch "
        "metadata. Supports image + social metadata (twitter / website / "
        "telegram / farcaster / discord). The RHC actions need "
        "CLAWNCH_API_KEY + a registered agent wallet (/register_agent)."
    ),
    schema=_SCHEMA,
    emoji="\U0001f31f",
)
def clawnch_launch(args: dict[str, Any], **kwargs: Any) -> str:
    action = read_str(args, "action", required=True)

    if action == "info":
        return _handle_info(args)
    if action == "rh_ticket":
        return _handle_rh_ticket(args)
    if action == "rh_confirm":
        return _handle_rh_confirm(args)
    if action == "rh_deposit":
        return _handle_rh_deposit(args)
    if action == "rh_token":
        return _handle_rh_token(args)
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
    burn = read_str(args, "burn_tx_hash") or None

    # `chain` selects the launch surface. Only Base is served by this
    # action: Robinhood Chain runs through the launch-router actions
    # (rh_ticket / rh_confirm / rh_deposit) and the service raises
    # `unsupported_chain` for a robinhood request — we surface that
    # rather than silently deploying on Base.
    start_deploy_chain = read_str(args, "chain") or None

    try:
        if start_deploy_chain and start_deploy_chain.strip().lower() not in ("base", "8453"):
            result = get_clawnch_service().prepare_deploy(
                from_address=args.get("from_address") or "",
                name=name,
                symbol=symbol,
                description=token_params.get("description"),
                image=token_params.get("image"),
                twitter=read_str(args, "twitter"),
                website=read_str(args, "website"),
                telegram=read_str(args, "telegram"),
                farcaster=read_str(args, "farcaster"),
                discord=read_str(args, "discord"),
                burn_tx_hash=burn,
                chain=start_deploy_chain,
            )
        else:
            result = get_clawnch_service().deploy(
                token_params=token_params,
                bypass_tx_hash=bypass,
                burn_tx_hash=burn,
            )
    except ClawnchError as exc:
        if exc.code == "burn_required":
            meta = exc.meta or {}
            min_tokens = str(meta.get("minBurnTokens") or "1000000")
            burn_addr = str(meta.get("burnAddress") or "0x000000000000000000000000000000000000dEaD")
            return error_result(
                f"{exc.message} Burn {min_tokens}+ CLAWNCH to {burn_addr} "
                "from the agent's wallet (the /burn command signs + submits "
                "one), then retry with burn_tx_hash=<tx hash>.",
                code=exc.code,
            )
        return error_result(exc.message, code=exc.code)
    except Exception as exc:  # noqa: BLE001
        return error_result(f"Launch failed: {exc}", code="api_error")

    tx_hash = result.get("txHash") or result.get("tx_hash")
    token_address = result.get("tokenAddress") or result.get("token_address")

    # Surface the actual chain from the launch response. The clawn.ch API
    # returns chainId in `data.chainId` (prepare path) or the deploy
    # metadata; the Base custodial path echoes none, so only THAT path
    # defaults to Base (a Robinhood request must never fall back to Base).
    chain_id = _extract_chain_id(result)

    # Desktop UI: surface the tx explorer link + token links for the
    # brand-new token as clickable Link artifacts. Passive descriptive
    # keys right now — no preview auto-open. enrich helpers no-op on
    # missing values.
    from clawmes.lib.ui_artifacts import enrich_token_links, enrich_tx_links

    enrich_tx_links(result, tx_hash=tx_hash or "", chain_id=chain_id)
    enrich_token_links(result, token=token_address or "", chain_id=chain_id)

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
            ("Bags", result.get("bags_url", "")),
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


def _extract_chain_id(result: dict[str, Any]) -> int:
    """Best-effort chain id off a launch response; Base when truly absent.

    The clawn.ch deploy responses echo a chain id on the non-custodial
    path (``data.chainId``) and the RHC envelope (``data.chainId`` +
    ``meta.chain``). Only the Base custodial response carries none — and
    only then do we default to Base (8453) for link rendering.
    """
    data = result.get("data")
    data = data if isinstance(data, dict) else {}
    meta = result.get("meta")
    meta = meta if isinstance(meta, dict) else {}
    for candidate in (
        result.get("chainId"),
        result.get("chain_id"),
        data.get("chainId"),
        data.get("chain_id"),
    ):
        if candidate is not None:
            try:
                return int(candidate)
            except (TypeError, ValueError):
                continue
    if str(meta.get("chain") or "").strip().lower() == "robinhood":
        return 4663
    return 8453


def _handle_rh_ticket(args: dict[str, Any]) -> str:
    """RHC ticket path: unsigned launch tx + EIP-712 ticket."""
    from clawmes.services.clawnch import ClawnchError, get_clawnch_service

    name = read_str(args, "name")
    symbol = read_str(args, "symbol")
    from_address = read_str(args, "from_address")
    if not name or not symbol:
        return error_result(
            "rh_ticket requires 'name' and 'symbol'.",
            code="param_error",
        )
    if not from_address:
        return error_result(
            "rh_ticket requires 'from_address' — the registered agent wallet "
            "that will sign and pay for the launch.",
            code="param_error",
        )

    try:
        result = get_clawnch_service().rh_ticket(
            agent_wallet=from_address,
            name=name,
            symbol=symbol,
            description=read_str(args, "description") or None,
            image=read_str(args, "image") or None,
            fee_recipient=read_str(args, "fee_recipient") or None,
        )
    except ClawnchError as exc:
        return error_result(exc.message, code=exc.code)
    except Exception as exc:  # noqa: BLE001
        return error_result(f"RHC ticket request failed: {exc}", code="api_error")

    data = _as_dict(result.get("data"))
    meta = _as_dict(result.get("meta"))
    deposit_address = meta.get("depositAddress") or ""
    creation_fee = meta.get("creationFeeWei") or data.get("value") or "0"
    parts = [
        f"Robinhood Chain launch ticket issued for {symbol}.",
        "Sign and send the unsigned launch() tx from the agent wallet, "
        "then record it with clawnch_launch action=rh_confirm tx_hash=<hash>.",
    ]
    if creation_fee not in ("", "0", "0x0"):
        parts.append(f"Creation fee (wei): {creation_fee}.")
    if deposit_address:
        parts.append(
            "Deposit path: send >= max(0.02 ETH, creation fee) to "
            f"{deposit_address}, then call action=rh_deposit with "
            "deposit_tx_hash=<hash>."
        )
    return json_result(result, summary=" ".join(parts))


def _as_dict(value: Any) -> dict[str, Any]:
    """Narrow an untyped JSON value to a dict (empty when it isn't one)."""
    return value if isinstance(value, dict) else {}


def _handle_rh_confirm(args: dict[str, Any]) -> str:
    """RHC ticket path: record the broadcast launch tx."""
    from clawmes.services.clawnch import (
        ClawnchError,
        get_clawnch_service,
        rh_explorer_token_url,
        rh_explorer_tx_url,
        rh_trade_url,
    )

    tx_hash = read_str(args, "tx_hash")
    if not tx_hash:
        return error_result(
            "rh_confirm requires 'tx_hash' (the launch tx sent from the agent wallet).",
            code="param_error",
        )
    try:
        result = get_clawnch_service().rh_confirm_launch(tx_hash=tx_hash)
    except ClawnchError as exc:
        return error_result(exc.message, code=exc.code)
    except Exception as exc:  # noqa: BLE001
        return error_result(f"RHC confirm failed: {exc}", code="api_error")

    launch = _as_dict(result.get("launch"))
    token = launch.get("token") or ""
    tx_url = rh_explorer_tx_url(tx_hash)
    if tx_url:
        result["explorer_url"] = tx_url
    if token:
        result["token_explorer_url"] = rh_explorer_token_url(token)
        result["trade_url"] = rh_trade_url(token)
    summary = f"Recorded Robinhood Chain launch {token or tx_hash}."
    return json_result(result, summary=summary)


def _handle_rh_deposit(args: dict[str, Any]) -> str:
    """RHC deposit path: launch from an already-sent ETH deposit."""
    from clawmes.services.clawnch import (
        ClawnchError,
        get_clawnch_service,
        rh_explorer_token_url,
        rh_trade_url,
    )

    deposit_tx_hash = read_str(args, "deposit_tx_hash")
    from_address = read_str(args, "from_address")
    name = read_str(args, "name")
    symbol = read_str(args, "symbol")
    if not deposit_tx_hash or not from_address or not name or not symbol:
        return error_result(
            "rh_deposit requires 'deposit_tx_hash', 'from_address', 'name' and 'symbol'.",
            code="param_error",
        )
    try:
        result = get_clawnch_service().rh_deposit_launch(
            deposit_tx_hash=deposit_tx_hash,
            agent_wallet=from_address,
            name=name,
            symbol=symbol,
            description=read_str(args, "description") or None,
            image=read_str(args, "image") or None,
        )
    except ClawnchError as exc:
        return error_result(exc.message, code=exc.code)
    except Exception as exc:  # noqa: BLE001
        return error_result(f"RHC deposit launch failed: {exc}", code="api_error")

    launch = _as_dict(result.get("launch"))
    token = launch.get("token") or ""
    if token:
        result["token_explorer_url"] = rh_explorer_token_url(token)
        result["trade_url"] = rh_trade_url(token)
    return json_result(
        result,
        summary=f"Launched {symbol} on Robinhood Chain via deposit.",
    )


def _handle_rh_token(args: dict[str, Any]) -> str:
    """$CLAWNCH on Robinhood Chain: address + links."""
    from clawmes.services.clawnch import get_clawnch_service

    info = get_clawnch_service().rh_token_info()
    return json_result(
        info,
        summary=(
            f"$CLAWNCH on Robinhood Chain: {info['token_address']} (trade: {info.get('trade_url')})"
        ),
    )


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
