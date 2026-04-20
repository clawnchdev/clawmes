"""Price triggers — fire when an asset crosses a threshold.

Trigger schema:

.. code-block:: json

    {
      "type": "price",
      "asset": "ETH",
      "operator": ">" | "<" | ">=" | "<=",
      "threshold": 2000,
      "currency": "USD"
    }

Evaluation reads the latest price from
``services.price_service.get_price()``. Caching is the price service's
responsibility; this module only does the comparison.
"""

from __future__ import annotations

from typing import Any


def evaluate(trigger: dict[str, Any], current_price: float | None) -> bool:
    """Return True if ``current_price`` satisfies the trigger condition."""
    if current_price is None:
        return False
    op = trigger.get("operator", ">")
    try:
        threshold = float(trigger.get("threshold", 0))
    except (TypeError, ValueError):
        return False
    if op == ">":
        return current_price > threshold
    if op == ">=":
        return current_price >= threshold
    if op == "<":
        return current_price < threshold
    if op == "<=":
        return current_price <= threshold
    if op in ("==", "="):
        return current_price == threshold
    return False
