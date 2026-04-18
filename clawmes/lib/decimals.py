"""Human-readable ↔ wei conversion.

LLMs work in human units ("0.5 ETH"); RPCs work in integer base units
("500000000000000000"). This module is the boundary.

We use ``decimal.Decimal`` for the human side to avoid float precision
loss. Token decimals come from ``clawmes.services.token_decimals`` (which
caches on-chain ``decimals()`` calls).
"""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal


def to_base_units(human_amount: str | Decimal | float | int, decimals: int) -> int:
    """Convert ``human_amount`` to base units (wei equivalent).

    Truncates (round-down) any precision below ``decimals`` — never rounds
    up, which would cause a "tried to send more than balance" failure on
    edge cases.

    Examples:
        >>> to_base_units("1.5", 18)
        1500000000000000000
        >>> to_base_units("0.0001", 6)
        100
    """
    quantum = Decimal(10) ** decimals
    amount = Decimal(str(human_amount))
    if amount < 0:
        raise ValueError(f"Negative amount: {human_amount!r}")
    base = (amount * quantum).to_integral_value(rounding=ROUND_DOWN)
    return int(base)


def from_base_units(
    base_amount: int | str,
    decimals: int,
    *,
    precision: int | None = None,
) -> str:
    """Convert base units back to a human-readable decimal string.

    ``precision`` clips trailing fractional digits for display
    (e.g. for showing balances). ``None`` returns full precision.
    """
    quantum = Decimal(10) ** decimals
    amount = Decimal(int(base_amount)) / quantum
    if precision is not None:
        if precision < 0:
            raise ValueError("precision must be >= 0")
        if precision == 0:
            return str(amount.to_integral_value(rounding=ROUND_DOWN))
        amount = amount.quantize(Decimal(10) ** -precision, rounding=ROUND_DOWN)
    text = format(amount, "f")
    # Strip trailing zeros but preserve at least "0" before the point.
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def format_human(
    base_amount: int | str,
    decimals: int,
    symbol: str | None = None,
    *,
    precision: int = 6,
) -> str:
    """Render base units as a display string with optional unit suffix."""
    body = from_base_units(base_amount, decimals, precision=precision)
    if symbol:
        return f"{body} {symbol}"
    return body
