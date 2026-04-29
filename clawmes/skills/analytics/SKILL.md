---
name: analytics
description: Technical-analysis indicators (RSI, MACD, Bollinger, volume) on token price history
metadata:
  hermes:
    tags: [crypto, defi, ta, indicators]
    category: clawmes
    requires_tools: [analytics]
---

# Technical Analysis

The `analytics` tool computes indicators from CoinGecko's
historical-price endpoint. Read-only, no API key required for the
free tier.

## When to use

- User says: "what's the RSI for ETH"
- User says: "is BTC overbought"
- User says: "show me the MACD on chainlink"
- User says: "Bollinger bands for AAVE"
- User says: "is volume spiking on solana right now"

## Actions

### `rsi` — Relative Strength Index

```json
{"action": "rsi", "token": "ETH", "days": 30, "period": 14}
```

Returns the latest RSI value (0–100). Standard interpretation:

- `> 70` — overbought, classic short / sell signal.
- `< 30` — oversold, classic long / buy signal.
- `30–70` — neutral.

Wilder's smoothing (the canonical RSI form). Default period 14.

### `macd` — Moving Average Convergence Divergence

```json
{"action": "macd", "token": "BTC", "days": 90}
```

Returns MACD line, signal line, histogram, and bullish/bearish
classification based on MACD vs. signal crossover.

Standard MACD(12,26,9): fast EMA 12, slow EMA 26, signal EMA 9.

### `bollinger` — Bollinger Bands

```json
{"action": "bollinger", "token": "SOL", "days": 30, "period": 20}
```

Returns upper / middle / lower bands plus current price + in-band
flag. Price near upper band = extension; near lower = contraction.

Default period 20, ±2σ.

### `volume` — recent vs. period average

```json
{"action": "volume", "token": "LDO", "days": 30}
```

Returns latest 24h volume + period average + ratio + classification:

- `elevated` — ratio > 1.5x (something's happening).
- `normal` — 0.5x to 1.5x.
- `depressed` — < 0.5x (interest fading).

### `funding` — perp funding rate

Currently `not_implemented`. Funding rates require a derivatives-
exchange integration (Binance, Bybit, Hyperliquid). Direct the user
to the exchange UI for now.

## Token IDs

Common-symbol aliases work: `ETH`, `BTC`, `SOL`, `MATIC`, `ARB`,
`OP`, `LINK`, `UNI`, `AAVE`, `LDO`, etc.

For less popular tokens, use the CoinGecko slug directly (lower-case
ID). Find slugs at coingecko.com/en/coins/<token>.

## Lookback windows

The `days` parameter controls both the lookback and the granularity:

- `1` — 5-minute ticks. Use for intraday analysis.
- `2–90` — hourly ticks. Standard for short/medium-term analysis.
- `91+` — daily ticks. Use for trend / quarterly analysis.

## Errors

- `insufficient_data` — not enough price points for the indicator
  (e.g. RSI needs `period+1` minimum). Try a larger `days` window.
- `not_found` — token slug not recognized by CoinGecko.
- `api_error` — CoinGecko rate-limited or unreachable.

## Caveats

These indicators are signals, not predictions. They tell you what
the price has been doing; they don't tell you what it will do.
Surface to the user but never present as a "buy/sell recommendation"
— just data.
