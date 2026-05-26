"""x402 payment-required HTTP detection helpers.

x402 (https://www.x402.org) is Coinbase's machine-payment protocol
built on HTTP 402 Payment Required. A server that wants payment for
an endpoint responds with status 402 + a JSON body describing what
to pay (amount, recipient, currency, network). Clients pay on-chain
and retry with proof.

This module is a minimal client-side detector — it parses x402
challenges out of HTTP responses so other clawmes tools can opt into
paying for premium endpoints. We don't yet wire payment auto-handling
into the request pipeline; that's a follow-up that involves wallet
signing + retry-with-proof.

Server-side x402 (the Clawnch backend exposing paid endpoints) is a
separate, downstream concern.

Spec reference: https://www.x402.org/

Why ship this as a helper module before wiring it into the request
pipeline: gives downstream tools (premium /api/bv7x_oracle, premium
yield aggregators, paid analytics) a clean way to surface payment
requirements to the user without needing each one to re-parse the
challenge shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class X402Challenge:
    """Structured representation of an x402 payment challenge.

    The spec lets servers describe multiple acceptable payment options;
    we expose them via :attr:`accepts`. ``primary()`` picks the first
    Base-mainnet USDC option (the most common) or the first option if
    none match.
    """

    accepts: tuple[dict[str, Any], ...]
    raw: dict[str, Any]

    def primary(self) -> dict[str, Any] | None:
        """Return the most likely payment option (Base USDC > first)."""
        if not self.accepts:
            return None
        for option in self.accepts:
            network = (option.get("network") or "").lower()
            asset = (option.get("asset") or "").upper()
            if network == "base" and asset == "USDC":
                return option
        return self.accepts[0]


def is_x402_response(body: Any, status_code: int | None = None) -> bool:
    """Return True if ``body`` (parsed JSON or dict) looks like an x402 challenge.

    The x402 spec keys responses with an ``"accepts"`` array describing
    payment options. We accept either ``status_code == 402`` OR the
    body shape — some implementations return 200 with the challenge in
    the body to dodge proxies that swallow 4xx.
    """
    if status_code == 402:
        return True
    if not isinstance(body, dict):
        return False
    accepts = body.get("accepts")
    return isinstance(accepts, list) and len(accepts) > 0


def parse_challenge(body: dict[str, Any]) -> X402Challenge:
    """Parse the body of an x402 response into a structured challenge."""
    accepts = body.get("accepts") or []
    options: tuple[dict[str, Any], ...] = tuple(opt for opt in accepts if isinstance(opt, dict))
    return X402Challenge(accepts=options, raw=body)


def format_challenge(challenge: X402Challenge) -> str:
    """Render a challenge as a short user-facing message.

    Tools that hit an x402-protected endpoint can use this to surface
    the payment requirement to the user without inventing their own
    format.
    """
    primary = challenge.primary()
    if not primary:
        return "Payment required but no acceptable payment options were offered."

    network = primary.get("network") or "?"
    asset = primary.get("asset") or "?"
    amount = primary.get("maxAmountRequired") or primary.get("amount") or "?"
    recipient = primary.get("payTo") or primary.get("recipient") or "?"
    description = primary.get("description") or ""

    lines = [
        f"Payment required: {amount} {asset} on {network}",
        f"  Pay to: {recipient}",
    ]
    if description:
        lines.append(f"  For: {description}")
    if len(challenge.accepts) > 1:
        lines.append(f"  (+ {len(challenge.accepts) - 1} alternate payment option(s))")
    return "\n".join(lines)
