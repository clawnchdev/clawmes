"""``/bv7x`` and ``/btc`` slash commands.

Two convenience reads that don't require asking the LLM to call a
tool:

  * ``/bv7x`` — show BV-7X's most recent scorecard (track record +
    streak) and current market regime.
  * ``/btc``  — show BTC price + Fear & Greed in one line. Quick
    glance at where the market is.

Both routes hit :class:`BV7XService` (60-second cache shared with
the tool surface).
"""

from __future__ import annotations


def _record(name: str, args: str, result: str) -> None:
    """Best-effort record into command_history if available."""
    try:
        from clawmes.services.command_history import record_command_call

        record_command_call(name, args, result)
    except Exception:  # noqa: BLE001
        pass


async def handle_bv7x(raw_args: str) -> str:
    from clawmes.services.bv7x import BV7XError, get_bv7x_service

    svc = get_bv7x_service()
    lines = ["BV-7X status:"]
    try:
        scorecard = svc.get_scorecard()
        summary = scorecard.get("summary") or {}
        total = summary.get("totalPredictions") or summary.get("total")
        accuracy = summary.get("accuracy") or summary.get("dedupedAccuracy")
        streak = summary.get("streak") or {}
        streak_text = ""
        if isinstance(streak, dict):
            streak_text = f"{streak.get('count', '?')} {streak.get('type', '?')}"
        lines.append(
            f"  Track record: {total} predictions, {accuracy}% accuracy"
            + (f" (streak: {streak_text})" if streak_text else "")
        )
    except BV7XError as exc:
        lines.append(f"  Scorecard error: {exc.message}")

    try:
        regime = svc.get_regime()
        kind = regime.get("regime") or regime.get("classification") or "?"
        risk = regime.get("risk_level") or regime.get("risk")
        lines.append(f"  Regime: {kind}" + (f" (risk={risk})" if risk else ""))
    except BV7XError as exc:
        lines.append(f"  Regime error: {exc.message}")

    try:
        ident = svc.get_agent_identity()
        agent_id = ident.get("agent_id") or ident.get("id") or "?"
        lines.append(f"  ERC-8004 agent: #{agent_id}")
    except BV7XError:
        # Identity is non-critical; quietly skip.
        pass

    if not svc.has_api_key():
        lines.append("")
        lines.append(
            "  (Premium signal + copy-trade endpoints require BV7X_API_KEY. "
            "Hold 500M+ $BV7X and complete the wallet-verify at "
            "https://bv7x.ai/terminal#developer.)"
        )

    out = "\n".join(lines)
    _record("bv7x", raw_args, out)
    return out


async def handle_btc(raw_args: str) -> str:
    from clawmes.services.bv7x import BV7XError, get_bv7x_service

    svc = get_bv7x_service()
    lines = ["BTC market:"]
    try:
        price = svc.get_btc_price()
        p = price.get("price") or price.get("btc_price") or "?"
        change = price.get("change_24h") or price.get("price_change_24h") or 0
        sign = "+" if isinstance(change, (int, float)) and change >= 0 else ""
        lines.append(f"  Price:        ${p} ({sign}{change}% 24h)")
    except BV7XError as exc:
        lines.append(f"  Price error:  {exc.message}")

    try:
        fg = svc.get_fear_greed()
        value = fg.get("value") or fg.get("score") or "?"
        classification = fg.get("classification") or fg.get("label") or ""
        suffix = f" ({classification})" if classification else ""
        lines.append(f"  Fear & Greed: {value}{suffix}")
    except BV7XError as exc:
        lines.append(f"  F&G error:    {exc.message}")

    try:
        etf = svc.get_etf_flows()
        flow_7d = etf.get("flow_7d") or etf.get("seven_day_flow") or "?"
        lines.append(f"  ETF 7d flow:  {flow_7d}")
    except BV7XError:
        # ETF is non-critical; quietly skip.
        pass

    out = "\n".join(lines)
    _record("btc", raw_args, out)
    return out


def register(ctx) -> None:
    """Wire BV-7X slash commands into Hermes."""
    ctx.register_command(
        name="bv7x",
        handler=handle_bv7x,
        description="BV-7X status: track record, regime, agent identity",
    )
    ctx.register_command(
        name="btc",
        handler=handle_btc,
        description="Quick BTC market read: price, Fear & Greed, ETF flow",
    )
