"""``analytics`` — technical analysis on price time series.

Five actions:

  * ``rsi``        — Relative Strength Index (0-100). >70 overbought,
    <30 oversold by traditional reading.
  * ``macd``       — Moving Average Convergence Divergence. Crossovers
    of MACD vs. signal are the standard buy/sell trigger.
  * ``bollinger``  — Bollinger Bands. Price near upper / lower band
    indicates extension.
  * ``volume``     — recent volume statistics + relative-to-average.
  * ``funding``    — perp funding rates. Not yet implemented (needs
    a derivatives-exchange integration); returns ``not_implemented``.

Inputs:

  * ``token``  — CoinGecko token ID (``ethereum``, ``bitcoin``, etc.)
    or a full lower-case slug. Symbol-only inputs (``ETH``) are
    coerced to common slugs for the ~20 most popular tokens.
  * ``days``   — lookback window. Sets the data granularity:
    1 → 5-min ticks, 2-90 → hourly, 91+ → daily. Default 30.
  * ``period`` — indicator period (RSI: 14, MACD: 12/26/9 nested,
    Bollinger: 20). Default uses the conventional value per indicator.

All calculations run inline with no third-party TA library. The math
is the same Wilder / standard formulations used in TradingView /
Bloomberg.
"""

from __future__ import annotations

import statistics
from typing import Any

from clawmes.lib.logger import logger_for
from clawmes.lib.params import read_int, read_str
from clawmes.lib.tool_result import error_result, json_result
from clawmes.tools.registry import read_tool, register_with_ctx

_log = logger_for("tools.analytics")

# Common-symbol → CoinGecko-slug map for the most-asked tokens. The
# tool also accepts raw slugs (``ethereum``, ``solana``, ``aave``)
# for users who know the canonical IDs.
_SYMBOL_ALIASES = {
    "eth": "ethereum",
    "btc": "bitcoin",
    "sol": "solana",
    "matic": "polygon-pos",
    "pol": "polygon-pos",
    "arb": "arbitrum",
    "op": "optimism",
    "base": "base-protocol",
    "link": "chainlink",
    "uni": "uniswap",
    "aave": "aave",
    "ldo": "lido-dao",
    "rpl": "rocket-pool",
    "mkr": "maker",
    "comp": "compound-governance-token",
    "snx": "havven",
    "crv": "curve-dao-token",
    "1inch": "1inch",
    "usdc": "usd-coin",
    "usdt": "tether",
    "dai": "dai",
}

_DEFAULT_DAYS = 30

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["rsi", "macd", "bollinger", "volume", "funding"],
        },
        "token": {
            "type": "string",
            "description": (
                "Token symbol ('ETH') or CoinGecko slug ('ethereum'). "
                "Common symbols are aliased; unknown inputs pass through "
                "as slugs."
            ),
        },
        "days": {
            "type": "integer",
            "description": (
                "Lookback in days (default 30). 1 = 5-min ticks, 2-90 = hourly, 91+ = daily."
            ),
        },
        "period": {
            "type": "integer",
            "description": (
                "Indicator period override (default: RSI=14, "
                "Bollinger=20, MACD uses 12/26/9 fixed)."
            ),
        },
    },
    "required": ["action", "token"],
}


@read_tool(
    name="analytics",
    toolset="clawmes-defi",
    description=(
        "Technical-analysis indicators on token price history. "
        "RSI, MACD, Bollinger Bands, and volume statistics computed "
        "from CoinGecko data. Funding rate is not yet implemented "
        "(requires a derivatives-exchange integration)."
    ),
    schema=_SCHEMA,
    emoji="\U0001f4ca",
)
def analytics(args: dict[str, Any], **kwargs: Any) -> str:
    action = read_str(args, "action", required=True)
    if action == "funding":
        return error_result(
            "Funding rate is not yet implemented (requires a perp DEX "
            "integration). Use a derivatives exchange directly for now.",
            code="not_implemented",
        )

    token_input = read_str(args, "token", required=True)
    token = _resolve_token(token_input)
    days = read_int(args, "days") or _DEFAULT_DAYS
    period = read_int(args, "period")

    prices = _fetch_prices(token, days)
    if isinstance(prices, str):
        return prices

    if action == "rsi":
        return _handle_rsi(token, prices, period or 14, days)
    if action == "macd":
        return _handle_macd(token, prices, days)
    if action == "bollinger":
        return _handle_bollinger(token, prices, period or 20, days)
    return _handle_volume(token, prices, days, args)


def _fetch_prices(token: str, days: int):
    """Returns price-only array on success or an error_result string."""
    from clawmes.services.coingecko import get_coingecko_service

    try:
        chart = get_coingecko_service().get_market_chart(token, vs_currency="usd", days=days)
    except Exception as exc:  # noqa: BLE001
        return error_result(
            f"Could not fetch market data for {token!r}: {exc}",
            code="api_error",
        )
    raw_prices = chart.get("prices") or []
    if not raw_prices:
        return error_result(
            f"No price history for {token!r}. Is the token ID correct?",
            code="not_found",
        )
    # Each entry is [timestamp_ms, price]; we only need the price series
    return [float(p[1]) for p in raw_prices if len(p) >= 2]


def _handle_rsi(token: str, prices: list[float], period: int, days: int) -> str:
    if len(prices) < period + 1:
        return error_result(
            f"Need at least {period + 1} data points for RSI; got {len(prices)}.",
            code="insufficient_data",
        )
    rsi = _compute_rsi(prices, period)
    last = rsi[-1] if rsi else 0.0
    signal = "overbought" if last > 70 else "oversold" if last < 30 else "neutral"
    return json_result(
        {
            "token": token,
            "indicator": "rsi",
            "period": period,
            "days": days,
            "latest": last,
            "signal": signal,
            "series_length": len(rsi),
        },
        summary=(f"RSI({period}) for {token} over {days}d\n  Latest: {last:.2f} ({signal})"),
    )


def _handle_macd(token: str, prices: list[float], days: int) -> str:
    if len(prices) < 35:  # need EMA(26) + signal smoothing
        return error_result(
            f"Need at least 35 data points for MACD; got {len(prices)}.",
            code="insufficient_data",
        )
    macd, signal, histogram = _compute_macd(prices)
    return json_result(
        {
            "token": token,
            "indicator": "macd",
            "days": days,
            "macd": macd[-1] if macd else 0.0,
            "signal": signal[-1] if signal else 0.0,
            "histogram": histogram[-1] if histogram else 0.0,
            "trend": "bullish" if (macd[-1] or 0) > (signal[-1] or 0) else "bearish",
        },
        summary=(
            f"MACD(12,26,9) for {token} over {days}d\n"
            f"  MACD:      {macd[-1]:.4f}\n"
            f"  Signal:    {signal[-1]:.4f}\n"
            f"  Histogram: {histogram[-1]:.4f}"
        ),
    )


def _handle_bollinger(token: str, prices: list[float], period: int, days: int) -> str:
    if len(prices) < period:
        return error_result(
            f"Need at least {period} data points for Bollinger; got {len(prices)}.",
            code="insufficient_data",
        )
    middle, upper, lower = _compute_bollinger(prices, period)
    last_price = prices[-1]
    band_width = (upper[-1] - lower[-1]) if upper and lower else 0
    in_band = lower[-1] <= last_price <= upper[-1] if upper and lower else True
    return json_result(
        {
            "token": token,
            "indicator": "bollinger",
            "period": period,
            "days": days,
            "middle": middle[-1] if middle else 0.0,
            "upper": upper[-1] if upper else 0.0,
            "lower": lower[-1] if lower else 0.0,
            "current_price": last_price,
            "band_width": band_width,
            "in_band": in_band,
        },
        summary=(
            f"Bollinger({period}) for {token} over {days}d\n"
            f"  Upper:   {upper[-1]:.4f}\n"
            f"  Middle:  {middle[-1]:.4f}\n"
            f"  Lower:   {lower[-1]:.4f}\n"
            f"  Price:   {last_price:.4f} ({'in band' if in_band else 'out of band'})"
        ),
    )


def _handle_volume(token: str, prices: list[float], days: int, args) -> str:
    """Volume action also fetches the same market-chart data and looks
    at the volume series. The fetch is duplicated for now — a smarter
    impl would return all three series from _fetch_prices."""
    from clawmes.services.coingecko import get_coingecko_service

    try:
        chart = get_coingecko_service().get_market_chart(token, vs_currency="usd", days=days)
    except Exception as exc:  # noqa: BLE001
        return error_result(
            f"Could not fetch volume data for {token!r}: {exc}",
            code="api_error",
        )
    raw_volumes = chart.get("total_volumes") or []
    if not raw_volumes:
        return error_result(
            f"No volume history for {token!r}.",
            code="not_found",
        )
    volumes = [float(v[1]) for v in raw_volumes if len(v) >= 2]
    if not volumes:
        return error_result(
            f"Volume series empty for {token!r}.",
            code="insufficient_data",
        )
    avg = statistics.mean(volumes)
    latest = volumes[-1]
    relative = (latest / avg) if avg > 0 else 0
    classification = "elevated" if relative > 1.5 else "depressed" if relative < 0.5 else "normal"
    return json_result(
        {
            "token": token,
            "indicator": "volume",
            "days": days,
            "latest_volume_usd": latest,
            "avg_volume_usd": avg,
            "ratio": relative,
            "classification": classification,
        },
        summary=(
            f"Volume for {token} over {days}d\n"
            f"  Latest:    ${latest:,.0f}\n"
            f"  {days}d avg: ${avg:,.0f}\n"
            f"  Ratio:     {relative:.2f}x ({classification})"
        ),
    )


# --- math helpers ---------------------------------------------------------


def _compute_rsi(prices: list[float], period: int) -> list[float]:
    """Wilder's RSI. Returns a series same length as input minus period.

    Uses Wilder's smoothing (the canonical RSI form, not simple moving
    average). Formula:

        avg_gain[t] = (avg_gain[t-1] * (period-1) + gain[t]) / period
        avg_loss[t] = (avg_loss[t-1] * (period-1) + loss[t]) / period
        rs = avg_gain / avg_loss
        rsi = 100 - 100 / (1 + rs)
    """
    deltas = [prices[i + 1] - prices[i] for i in range(len(prices) - 1)]
    if len(deltas) < period:
        return []
    gains = [max(d, 0) for d in deltas]
    losses = [-min(d, 0) for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    rsis: list[float] = []
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            rsis.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsis.append(100 - 100 / (1 + rs))
    return rsis


def _ema(values: list[float], period: int) -> list[float]:
    """Exponential moving average. Uses 2/(period+1) smoothing."""
    if len(values) < period:
        return []
    k = 2 / (period + 1)
    # Seed with SMA of first ``period`` values
    seed = sum(values[:period]) / period
    out = [seed]
    for v in values[period:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def _compute_macd(
    prices: list[float],
) -> tuple[list[float], list[float], list[float]]:
    """Standard MACD(12,26,9): MACD = EMA(12) - EMA(26); signal = EMA(9) of MACD."""
    ema12 = _ema(prices, 12)
    ema26 = _ema(prices, 26)
    # Align lengths: ema26 is shorter (starts later)
    offset = len(ema12) - len(ema26)
    ema12_aligned = ema12[offset:]
    macd_line = [a - b for a, b in zip(ema12_aligned, ema26, strict=True)]
    signal_line = _ema(macd_line, 9)
    sig_offset = len(macd_line) - len(signal_line)
    macd_aligned = macd_line[sig_offset:]
    histogram = [m - s for m, s in zip(macd_aligned, signal_line, strict=True)]
    return macd_aligned, signal_line, histogram


def _compute_bollinger(
    prices: list[float], period: int
) -> tuple[list[float], list[float], list[float]]:
    """SMA + 2σ bands. Returns (middle, upper, lower) aligned to length
    len(prices) - period + 1."""
    middle: list[float] = []
    upper: list[float] = []
    lower: list[float] = []
    for i in range(period - 1, len(prices)):
        window = prices[i - period + 1 : i + 1]
        mean = statistics.mean(window)
        sd = statistics.pstdev(window) if len(window) > 1 else 0.0
        middle.append(mean)
        upper.append(mean + 2 * sd)
        lower.append(mean - 2 * sd)
    return middle, upper, lower


def _resolve_token(value: str) -> str:
    norm = value.strip().lower()
    return _SYMBOL_ALIASES.get(norm, norm)


def register(ctx) -> None:
    """Wire ``analytics`` into Hermes."""
    register_with_ctx(ctx, analytics)
