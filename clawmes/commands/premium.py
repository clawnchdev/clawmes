"""``/premium``, ``/verify``, ``/burn_and_call`` slash commands.

Surface for the Clawnch premium system:

  * ``/premium`` — show the active wallet's tier, with upgrade hints.
    Argument variations:
      - ``/premium``                          — show tier
      - ``/premium features``                 — list every premium feature
      - ``/premium quote <feature_id>``       — quote burn cost + calldata
  * ``/verify <signature>`` — submit a wallet-signed challenge to the
    clawn.ch verifier; cache the returned JWT.
  * ``/burn_and_call <feature_id> <tx_hash>`` — after signing a burn
    tx, redeem the hash for a one-shot grant of the named feature.

All three are non-LLM commands — they run synchronously, don't pay
inference cost, and persist nothing on disk in v1. The JWT cache and
one-shot grants live in :mod:`clawmes.services.clawnch_premium`.
"""

from __future__ import annotations


def _record(name: str, args: str, result: str) -> None:
    """Best-effort recording into command_history. Identical pattern to
    ``commands.identity`` — keeps the surfaces consistent."""
    try:
        from clawmes.services.command_history import record_command_call

        record_command_call(name, args, result)
    except Exception:  # noqa: BLE001 — recording must never break a command
        pass


# ──────────────────────────────────────────────────────────────────────
#  /premium
# ──────────────────────────────────────────────────────────────────────


async def handle_premium(raw_args: str) -> str:
    from clawmes.lib import clawnch as clawnch_const
    from clawmes.services.clawnch_premium import get_clawnch_premium_service

    svc = get_clawnch_premium_service()
    arg = raw_args.strip()

    if not arg:
        out = _render_status(svc, clawnch_const)
        _record("premium", raw_args, out)
        return out

    parts = arg.split()
    action = parts[0].lower()

    if action == "features":
        out = _render_features(clawnch_const)
        _record("premium", raw_args, out)
        return out

    if action == "quote":
        if len(parts) < 2:
            return "Usage: /premium quote <feature_id>\nRun /premium features to see available IDs."
        feature_id = parts[1]
        quote = svc.request_burn_quote(feature_id)
        out = _render_quote(quote)
        _record("premium", raw_args, out)
        return out

    return (
        f"Unknown /premium arg {arg!r}. Use:\n"
        "  /premium                 — show tier\n"
        "  /premium features        — list premium features\n"
        "  /premium quote <id>      — quote a burn cost + calldata"
    )


def _render_status(svc, clawnch_const) -> str:
    tier = svc.get_tier()
    lines = ["Clawnch premium status:"]
    lines.append(f"  Active tier: {tier.upper()}")
    if tier == "free":
        lines.append("")
        lines.append("Upgrade paths:")
        lines.append(
            f"  • Stake CLAWNCH at https://clawn.ch/stake — "
            f"≥{clawnch_const.PRO_THRESHOLD:,} weighted unlocks Pro, "
            f"≥{clawnch_const.MAX_THRESHOLD:,} weighted unlocks Max."
        )
        lines.append(
            "  • One-shot: /premium quote <feature_id> to get a burn quote for a single call."
        )
    elif tier == "pro":
        lines.append("")
        lines.append("Pro unlocks: BV-7X premium oracle, OpenGateway high-tier,")
        lines.append("  premium RPC, priority bridge routing. /premium features for full list.")
        lines.append("")
        lines.append(f"  Stake ≥{clawnch_const.MAX_THRESHOLD:,} weighted to upgrade to Max.")
    elif tier == "max":
        lines.append("")
        lines.append("Max unlocks: everything in Pro + clawmes-issued EAS attestations,")
        lines.append("  sub-agent / A2A capability, early-access tools, private channels.")
    return "\n".join(lines)


def _render_features(clawnch_const) -> str:
    if not clawnch_const.FEATURES:
        return "No premium features registered."
    lines = ["Premium features:"]
    for feature_id, meta in clawnch_const.FEATURES.items():
        tier = meta["tier"]
        label = meta["label"]
        cost = clawnch_const.burn_price(feature_id)
        cost_str = f"{cost:,} CLAWNCH" if cost is not None else "(no burn quote — stake only)"
        lines.append(f"  • {feature_id} [{tier}] — {label}")
        lines.append(f"      one-shot burn: {cost_str}")
    return "\n".join(lines)


def _render_quote(quote) -> str:
    if "error" in quote:
        return (
            f"Quote error: {quote['error']} (feature_id={quote.get('feature_id')!r}).\n"
            "Run /premium features to see available IDs."
        )
    if quote.get("unsupported"):
        return (
            f"`{quote['feature_id']}` has no published burn price — "
            f"stake at https://clawn.ch/stake to unlock {quote['required_tier']}."
        )
    lines = [
        f"Burn quote for `{quote['feature_id']}`:",
        f"  {quote['label']}",
        f"  Cost: {quote['cost_clawnch']:,} CLAWNCH ({quote['cost_wei']} wei)",
        f"  Token: {quote['token']}",
        f"  Burn address: {quote['burn_address']}",
        "",
        "Sign a transfer with the calldata below, then:",
        f"  /burn_and_call {quote['feature_id']} <tx_hash>",
        "",
        f"  calldata: {quote['calldata']}",
    ]
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────
#  /verify
# ──────────────────────────────────────────────────────────────────────


async def handle_verify(raw_args: str) -> str:
    """Submit a signed challenge to the clawn.ch verifier.

    Two-phase like ``/recover``: no-args returns usage; with-args
    forwards to the verifier and caches the JWT on success.

    In v1 we accept ``<address>:<signature>`` as the inline payload
    and POST to ``${VERIFIER_URL}clawmes/verify``. The verifier owns
    the challenge / signature spec — we just shuttle bytes.
    """
    from clawmes.lib import clawnch as clawnch_const
    from clawmes.services.clawnch_premium import get_clawnch_premium_service

    arg = raw_args.strip()
    if not arg:
        return (
            "Usage: /verify <address>:<signature>\n\n"
            "Steps:\n"
            f"  1. Sign the verifier challenge at {clawnch_const.VERIFIER_URL}clawmes/challenge\n"
            "     (or use your wallet's `eth_sign` against the issued nonce)\n"
            "  2. Copy the resulting <address>:<signature> string\n"
            "  3. Paste it here as /verify <address>:<signature>\n\n"
            "The verifier returns a 24h JWT that lifts the tier gate."
        )

    if ":" not in arg:
        return "Expected /verify <address>:<signature> — got a value with no `:` separator."
    address, signature = arg.split(":", 1)
    address = address.strip()
    signature = signature.strip()
    if not address or not signature:
        return "Both <address> and <signature> are required (split by `:`)."

    result = _post_verifier(
        clawnch_const.VERIFIER_URL + "clawmes/verify",
        {"address": address, "signature": signature},
    )
    if "error" in result:
        out = f"Verifier rejected: {result['error']}"
        _record("verify", raw_args, out)
        return out

    jwt = result.get("jwt")
    tier = result.get("tier")
    ttl = result.get("ttl_seconds")
    if not jwt or tier not in clawnch_const.TIER_ORDER:
        out = "Verifier response missing jwt/tier — staying on the previous tier."
        _record("verify", raw_args, out)
        return out

    svc = get_clawnch_premium_service()
    svc.set_jwt(address, jwt, tier, ttl=ttl)
    out = f"Verified. Tier: {tier.upper()}. JWT cached (TTL: {ttl or 'default'} seconds)."
    _record("verify", raw_args, out)
    return out


# ──────────────────────────────────────────────────────────────────────
#  /burn_and_call
# ──────────────────────────────────────────────────────────────────────


async def handle_burn_and_call(raw_args: str) -> str:
    """Redeem a confirmed burn tx for one-shot access to a feature.

    Calldata for the burn comes from ``/premium quote <feature_id>``;
    the user signs in their wallet, then submits the resulting tx
    hash here. We forward to the verifier (which confirms the burn
    on-chain) and on success record a one-shot grant scoped to the
    feature.
    """
    from clawmes.lib import clawnch as clawnch_const
    from clawmes.services.clawnch_premium import get_clawnch_premium_service

    parts = raw_args.split()
    if len(parts) < 2:
        return (
            "Usage: /burn_and_call <feature_id> <tx_hash>\n\n"
            "Get a quote first with /premium quote <feature_id>, sign\n"
            "the burn tx, then redeem the hash here."
        )

    feature_id, tx_hash = parts[0], parts[1]
    if feature_id not in clawnch_const.FEATURES:
        return f"Unknown feature_id {feature_id!r}.\nRun /premium features to see available IDs."

    result = _post_verifier(
        clawnch_const.VERIFIER_URL + "clawmes/burn-verify",
        {"feature_id": feature_id, "tx_hash": tx_hash},
    )
    if "error" in result:
        out = f"Verifier rejected: {result['error']}"
        _record("burn_and_call", raw_args, out)
        return out

    svc = get_clawnch_premium_service()
    ok = svc.redeem_burn(feature_id, tx_hash)
    if not ok:
        out = "Could not record the one-shot grant — check the inputs and retry."
        _record("burn_and_call", raw_args, out)
        return out

    out = (
        f"One-shot grant for `{feature_id}` recorded. Your next call to that "
        f"feature is free; subsequent calls require either the tier or a "
        f"fresh burn."
    )
    _record("burn_and_call", raw_args, out)
    return out


# ──────────────────────────────────────────────────────────────────────
#  Verifier transport
# ──────────────────────────────────────────────────────────────────────


def _post_verifier(url: str, body: dict) -> dict:
    """POST a JSON payload to the verifier, returning the parsed response.

    Network failures, non-2xx responses, and unparseable bodies all
    surface as ``{"error": "..."}`` so the command handlers stay
    branch-free. The verifier endpoints live under ``clawn.ch/api/``
    and follow a simple JSON convention.
    """
    try:
        from clawmes.lib.http import http_post

        return http_post(url, json=body, timeout=15.0)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"verifier transport error: {exc}"}


# ──────────────────────────────────────────────────────────────────────
#  Registration
# ──────────────────────────────────────────────────────────────────────


def register(ctx) -> None:
    """Wire premium commands into Hermes."""
    ctx.register_command(
        name="premium",
        handler=handle_premium,
        description="Show Clawnch premium tier, list features, or quote a burn",
        args_hint="[features | quote <feature_id>]",
    )
    ctx.register_command(
        name="verify",
        handler=handle_verify,
        description="Verify wallet ownership with clawn.ch, unlock premium tier",
        args_hint="<address>:<signature>",
    )
    ctx.register_command(
        name="burn_and_call",
        handler=handle_burn_and_call,
        description="Redeem a CLAWNCH burn tx for one-shot premium access",
        args_hint="<feature_id> <tx_hash>",
    )
