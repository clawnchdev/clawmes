"""``/research <token>`` — structured autonomous token research.

A Clawmes Unlimited feature. Synthesizes data from multiple sources
into a structured report on a single token:

  * Current USD price + 24h change (defi_price)
  * Market cap + 24h volume + liquidity (DexScreener)
  * Recent launch context (Clawnch /api/launches if it's a launchpad
    token; otherwise skipped)
  * Risk flags (low liquidity, high age, etc.)

Optionally synthesizes a narrative summary via OpenGateway when the
key is configured. Without it, returns the raw structured report.

Surface:

  * ``/research <token>``               — single-token research
  * ``/research <token> --no-narrative`` — skip the LLM synthesis even
    when OpenGateway is available

Output is intentionally chat-friendly multi-line text. Use the JSON
view (``--json``) for machine consumption.
"""

from __future__ import annotations

import json
from typing import Any

from clawmes.lib.http import http_get

_DEXSCREENER_BASE = "https://api.dexscreener.com"
_CLAWNCH_API_BASE = "https://www.clawn.ch"


def _record(name: str, args: str, result: str) -> None:
    try:
        from clawmes.services.command_history import record_command_call

        record_command_call(name, args, result)
    except Exception:  # noqa: BLE001
        pass


# ── dispatch ────────────────────────────────────────────────────────


async def handle_research(raw_args: str, *, sender_id: str = "default", **_kwargs: Any) -> str:
    raw = (raw_args or "").strip()
    if not raw:
        out = (
            "Usage: /research <token> [--no-narrative] [--json]\n"
            "\n"
            "Examples:\n"
            "  /research CLAWNCH\n"
            "  /research 0xa1F72459dfA10BAD200Ac160eCd78C6b77a747be"
        )
        _record("research", raw_args, out)
        return out

    # UNLIMITED-tier gate.
    from clawmes.services.token_gate import Tier, check_tier_or_error

    gate_err = check_tier_or_error(Tier.UNLIMITED, feature="/research")
    if gate_err:
        return gate_err

    parts = raw.split()
    token = parts[0]
    use_narrative = "--no-narrative" not in parts
    as_json = "--json" in parts

    report = _build_report(token)
    if as_json:
        out = json.dumps(report, indent=2)
    elif use_narrative:
        narrative = _llm_synthesize(report)
        out = (
            _render_report(report)
            + ("\n\n" + narrative if narrative else "")
            + _card_suffix(report, token)
        )
    else:
        out = _render_report(report) + _card_suffix(report, token)

    _record("research", raw_args, out)
    return out


def _card_suffix(report: dict[str, Any], token: str) -> str:
    """Build a Desktop research card and return a line pointing at it.

    Returns ``""`` when there's nothing to show or anything fails — the card
    is a best-effort UI nicety layered on top of the text report. The card
    path surfaces as a clickable File artifact in the Hermes Desktop app.
    """
    try:
        from clawmes.lib.ui_artifacts import clanker_url, dexscreener_url, explorer_token_url
        from clawmes.lib.ui_cards import research_card, write_card

        dex = report.get("dex") or {}
        symbol = dex.get("symbol") or token
        specs = [
            ("Price", "price_usd"),
            ("Liquidity", "liquidity_usd"),
            ("Volume 24h", "volume_24h"),
            ("Market cap", "market_cap"),
            ("Change 24h", "price_change_24h"),
        ]
        rows: list[tuple[str, str]] = []
        for label, key in specs:
            value = dex.get(key)
            if value is not None:
                rows.append((label, str(value)))
        flags = report.get("flags") or []
        if flags:
            rows.append(("Risk flags", ", ".join(str(f) for f in flags)))

        addr = report.get("resolved_address") or ""
        links = [
            ("DexScreener", dexscreener_url(addr, 8453) or ""),
            ("Clanker", clanker_url(addr, 8453) or ""),
            ("Explorer", explorer_token_url(addr, 8453) or ""),
        ]
        if not rows:
            return ""
        card_path = write_card(
            research_card(symbol=symbol, rows=rows, links=links), f"research-{symbol}"
        )
        return f"\n\nResearch card: {card_path}\n"
    except Exception:  # noqa: BLE001 — UI is best-effort
        return ""


# ── data sources ───────────────────────────────────────────────────


def _build_report(token: str) -> dict[str, Any]:
    """Pull every available signal on the token into one structured dict."""
    report: dict[str, Any] = {"token_input": token}

    # 1. DexScreener: pair-level data. Works for symbols OR addresses.
    dex = _fetch_dexscreener(token)
    if dex:
        report["dex"] = dex
        # Promote a canonical address if we found one.
        report["resolved_address"] = dex.get("address")

    # 2. defi_price for a normalized USD quote (in case dex didn't yield one).
    if "dex" not in report or not report["dex"].get("price_usd"):
        price = _fetch_defi_price(token)
        if price is not None:
            report["price_usd"] = price

    # 3. Clawnch launch metadata — only valid for launchpad tokens.
    addr_for_clawnch = report.get("resolved_address") or (
        token if token.startswith("0x") and len(token) == 42 else None
    )
    if addr_for_clawnch:
        launch = _fetch_clawnch_launch(addr_for_clawnch)
        if launch:
            report["clawnch_launch"] = launch

    # 4. Compute derived signals + risk flags.
    report["flags"] = _compute_flags(report)
    return report


def _fetch_dexscreener(token: str) -> dict[str, Any] | None:
    """Fetch top pair for ``token`` from DexScreener.

    Accepts a symbol or an address. For addresses, hits the
    ``/tokens/v1/<chain>/<address>`` endpoint. For symbols, uses the
    search endpoint and takes the top Base pair.
    """
    if token.startswith("0x") and len(token) == 42:
        url = f"{_DEXSCREENER_BASE}/tokens/v1/base/{token.lower()}"
    else:
        url = f"{_DEXSCREENER_BASE}/latest/dex/search"
    try:
        if token.startswith("0x"):
            body = http_get(url, timeout=15.0)
        else:
            body = http_get(url, params={"q": token}, timeout=15.0)
    except Exception:  # noqa: BLE001
        return None

    pairs = _extract_dex_pairs(body)
    if not pairs:
        return None

    # Filter to Base, then pick highest 24h volume.
    base_pairs = [p for p in pairs if (p.get("chainId") or "").lower() == "base"]
    if not base_pairs:
        return None
    top = max(base_pairs, key=lambda p: _safe_float((p.get("volume") or {}).get("h24")))
    base_token = top.get("baseToken") or {}
    return {
        "address": (base_token.get("address") or "").lower() or None,
        "symbol": base_token.get("symbol"),
        "name": base_token.get("name"),
        "price_usd": _safe_float(top.get("priceUsd")),
        "volume_24h": _safe_float((top.get("volume") or {}).get("h24")),
        "liquidity_usd": _safe_float((top.get("liquidity") or {}).get("usd")),
        "market_cap": _safe_float(top.get("marketCap") or top.get("fdv")),
        "price_change_24h": _safe_float((top.get("priceChange") or {}).get("h24")),
        "dex_id": top.get("dexId"),
        "pair_address": top.get("pairAddress"),
    }


def _extract_dex_pairs(body: Any) -> list[dict[str, Any]]:
    """DexScreener returns ``{pairs: [...]}`` on search, raw list on tokens/v1."""
    if isinstance(body, list):
        return [x for x in body if isinstance(x, dict)]
    if isinstance(body, dict):
        pairs = body.get("pairs")
        if isinstance(pairs, list):
            return [x for x in pairs if isinstance(x, dict)]
    return []


def _fetch_defi_price(token: str) -> float | None:
    """Fall back to the existing defi_price tool for a USD quote."""
    try:
        from clawmes.tools.defi_price import defi_price

        raw = defi_price({"action": "quote", "symbol": token, "quote_currency": "USD"})
    except Exception:  # noqa: BLE001
        return None
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("isError"):
        return None
    details = payload.get("details") or {}
    return _safe_float(details.get("price_usd") or details.get("price"))


def _fetch_clawnch_launch(address: str) -> dict[str, Any] | None:
    """Fetch Clawnch /api/launches metadata for a known token address."""
    try:
        body = http_get(
            f"{_CLAWNCH_API_BASE}/api/launches",
            params={"address": address},
            timeout=10.0,
        )
    except Exception:  # noqa: BLE001
        return None
    launches = _extract_launches(body)
    if not launches:
        return None
    launch = launches[0]
    return {
        "agent": launch.get("agentName") or launch.get("agent"),
        "source": launch.get("source"),
        "symbol": launch.get("symbol") or launch.get("ticker"),
        "name": launch.get("name"),
        "deployed_at": launch.get("createdAt")
        or launch.get("deployedAt")
        or launch.get("timestamp"),
    }


def _extract_launches(body: Any) -> list[dict[str, Any]]:
    if isinstance(body, list):
        return [x for x in body if isinstance(x, dict)]
    if isinstance(body, dict):
        for key in ("launches", "data", "results"):
            inner = body.get(key)
            if isinstance(inner, list):
                return [x for x in inner if isinstance(x, dict)]
    return []


def _compute_flags(report: dict[str, Any]) -> list[str]:
    """Derive risk flags from the assembled data."""
    flags: list[str] = []
    dex = report.get("dex") or {}
    liquidity = dex.get("liquidity_usd")
    if isinstance(liquidity, (int, float)) and liquidity < 5_000:
        flags.append("low_liquidity")
    volume = dex.get("volume_24h")
    if isinstance(volume, (int, float)) and volume < 1_000:
        flags.append("thin_volume_24h")
    price_change = dex.get("price_change_24h")
    if isinstance(price_change, (int, float)):
        if price_change <= -50.0:
            flags.append("major_drawdown_24h")
        elif price_change >= 500.0:
            flags.append("blow_off_top_candidate")
    return flags


def _safe_float(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


# ── narrative synthesis ────────────────────────────────────────────


def _llm_synthesize(report: dict[str, Any]) -> str:
    """Optional narrative summary via OpenGateway. Empty on any failure.

    Strictly capped to 4 sentences in the prompt — we want a synthesis,
    not a sales pitch. Failures fall back to the structured report
    above without any visible error.
    """
    try:
        from clawmes.services.opengateway import get_opengateway_service
    except Exception:  # noqa: BLE001
        return ""

    system = (
        "You are a research analyst summarizing a single token for a trader. "
        "Given the structured JSON, output a 3-4 sentence neutral summary. "
        "Highlight liquidity, recent price action, and any obvious risks. "
        "Do not give buy / sell advice. Do not invent data."
    )
    user = json.dumps(report)
    try:
        svc = get_opengateway_service()
        resp = svc.chat_completion(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.2,
            max_tokens=200,
        )
    except Exception:  # noqa: BLE001
        return ""
    choices = resp.get("choices") or []
    if not choices:
        return ""
    msg = choices[0].get("message") or {}
    content = msg.get("content")
    if not isinstance(content, str):
        return ""
    content = content.strip()
    if not content:
        return ""
    return "SUMMARY\n" + content


# ── render ──────────────────────────────────────────────────────────


def _render_report(report: dict[str, Any]) -> str:
    lines = [f"Research: {report.get('token_input', '?')}"]
    dex = report.get("dex") or {}
    if dex:
        lines.extend(
            [
                "",
                "DEX (from DexScreener)",
                f"  Symbol:        {dex.get('symbol') or '?'}",
                f"  Name:          {dex.get('name') or '?'}",
                f"  Address:       {dex.get('address') or '?'}",
                f"  Price (USD):   {_fmt(dex.get('price_usd'))}",
                f"  24h change:    {_fmt_pct(dex.get('price_change_24h'))}",
                f"  24h volume:    {_fmt_usd(dex.get('volume_24h'))}",
                f"  Liquidity:     {_fmt_usd(dex.get('liquidity_usd'))}",
                f"  Market cap:    {_fmt_usd(dex.get('market_cap'))}",
                f"  DEX:           {dex.get('dex_id') or '?'}",
                f"  Pair address:  {dex.get('pair_address') or '?'}",
            ]
        )
    elif report.get("price_usd") is not None:
        lines.extend(
            [
                "",
                "PRICE",
                f"  USD:           {_fmt(report['price_usd'])}",
                "  (DexScreener returned no pair data; price from defi_price quote)",
            ]
        )
    else:
        lines.extend(
            [
                "",
                "No price or pair data found for this token.",
                "Try passing the full 0x… address, or a more specific symbol.",
            ]
        )

    launch = report.get("clawnch_launch")
    if launch:
        lines.extend(
            [
                "",
                "CLAWNCH LAUNCH METADATA",
                f"  Agent:         {launch.get('agent') or '?'}",
                f"  Source:        {launch.get('source') or '?'}",
                f"  Symbol:        {launch.get('symbol') or '?'}",
                f"  Name:          {launch.get('name') or '?'}",
                f"  Deployed at:   {launch.get('deployed_at') or '?'}",
            ]
        )

    flags = report.get("flags") or []
    if flags:
        lines.extend(["", "FLAGS"])
        for f in flags:
            lines.append(f"  • {f}")
    return "\n".join(lines)


def _fmt(v: Any) -> str:
    if v is None:
        return "?"
    if isinstance(v, (int, float)):
        return f"${v:,.8f}".rstrip("0").rstrip(".") if v < 1 else f"${v:,.2f}"
    return str(v)


def _fmt_pct(v: Any) -> str:
    if v is None:
        return "?"
    if isinstance(v, (int, float)):
        sign = "+" if v >= 0 else ""
        return f"{sign}{v:.1f}%"
    return str(v)


def _fmt_usd(v: Any) -> str:
    if v is None:
        return "?"
    if not isinstance(v, (int, float)):
        return str(v)
    if v >= 1_000_000_000:
        return f"${v / 1_000_000_000:.2f}B"
    if v >= 1_000_000:
        return f"${v / 1_000_000:.2f}M"
    if v >= 1_000:
        return f"${v / 1_000:.1f}k"
    return f"${v:.2f}"


def register(ctx) -> None:
    ctx.register_command(
        name="research",
        handler=handle_research,
        description="Structured autonomous token research (Clawmes Unlimited)",
        args_hint="<token> [--no-narrative] [--json]",
    )
