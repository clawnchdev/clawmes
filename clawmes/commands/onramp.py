"""``/onramp`` slash command — Coinbase Onramp link generator.

Removes the "need ETH first" friction for new users by emitting a
Coinbase Onramp URL pre-filled with their connected wallet's address.
The user opens the link in a browser, completes KYC (if not already
done), and the purchased ETH lands on Base directly.

The URL is a hosted Coinbase widget; clawmes never touches fiat or
custody. We just construct the deep link and surface it.

Two configuration env vars:

  * ``CLAWMES_COINBASE_ONRAMP_APP_ID`` — Coinbase Developer Platform
    app ID for clawmes / Clawnch. Required for production use.
  * ``CLAWMES_COINBASE_ONRAMP_DEFAULT_AMOUNT`` — default USD amount
    when the user doesn't specify one. Default: ``25``.

When ``APP_ID`` isn't configured, the command still emits a generic
Coinbase Onramp landing-page URL so the user can fall back manually.
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlencode

from clawmes.services.wallet import get_wallet_state

_ONRAMP_BASE_URL = "https://pay.coinbase.com/buy/select-asset"
_FALLBACK_LANDING = "https://www.coinbase.com/onramp"
_DEFAULT_AMOUNT_USD = "25"


def _record(name: str, args: str, result: str) -> None:
    try:
        from clawmes.services.command_history import record_command_call

        record_command_call(name, args, result)
    except Exception:  # noqa: BLE001
        pass


def _parse_amount(raw: str) -> float | str | None:
    """Return parsed positive float, error string, or ``None`` (no input)."""
    raw = raw.strip()
    if not raw:
        return None
    try:
        amount = float(raw)
    except ValueError:
        return f"Invalid amount {raw!r}. Expected a positive USD number (e.g. 50)."
    if amount <= 0:
        return f"Invalid amount {raw!r}. Must be positive."
    return amount


def _build_onramp_url(*, address: str, amount: str, asset: str = "ETH") -> str:
    """Build a Coinbase Onramp URL with destination wallet pre-filled.

    Falls back to the generic landing page when ``APP_ID`` env var
    isn't set so users always get *something* clickable, even on a
    fresh install without Coinbase Developer Platform setup.
    """
    app_id = os.environ.get("CLAWMES_COINBASE_ONRAMP_APP_ID")
    if not app_id:
        return _FALLBACK_LANDING

    destination_wallets = (
        '[{"address":"' + address + '","blockchains":["base"],"assets":["' + asset + '"]}]'
    )
    params = {
        "appId": app_id,
        "destinationWallets": destination_wallets,
        "defaultAsset": asset,
        "defaultNetwork": "base",
        "presetFiatAmount": amount,
        "fiatCurrency": "USD",
    }
    return f"{_ONRAMP_BASE_URL}?{urlencode(params)}"


async def handle_onramp(raw_args: str, **_kwargs: Any) -> str:
    parsed = _parse_amount(raw_args or "")
    if isinstance(parsed, str):
        return parsed
    amount_usd = (
        str(parsed)
        if isinstance(parsed, float)
        else os.environ.get("CLAWMES_COINBASE_ONRAMP_DEFAULT_AMOUNT", _DEFAULT_AMOUNT_USD)
    )

    state = get_wallet_state()
    if not state.connected or not state.address:
        out = (
            "No wallet connected — Onramp needs a destination address.\n"
            "Run /connect / /connect_local / /connect_bankr first.\n"
            "\n"
            "Generic Coinbase Onramp (you'll have to enter the address manually):\n"
            f"  {_FALLBACK_LANDING}"
        )
        _record("onramp", raw_args, out)
        return out

    url = _build_onramp_url(address=state.address, amount=amount_usd)
    is_fallback = url == _FALLBACK_LANDING

    lines = [
        f"Coinbase Onramp link for {state.address}:",
        "",
        f"  {url}",
        "",
        f"Buy ~${amount_usd} of ETH on Base via Coinbase. Opens a hosted widget — "
        "KYC + payment happen on Coinbase's side, clawmes doesn't touch fiat. "
        "ETH lands on Base directly to your connected wallet.",
    ]
    if is_fallback:
        lines.append("")
        lines.append(
            "⚠ CLAWMES_COINBASE_ONRAMP_APP_ID not configured — using the generic Coinbase "
            "Onramp landing page. Set the env var to get pre-filled destination + amount."
        )
    out = "\n".join(lines)
    _record("onramp", raw_args, out)
    return out


def register(ctx) -> None:
    ctx.register_command(
        name="onramp",
        handler=handle_onramp,
        description="Generate a Coinbase Onramp link pre-filled with your wallet address",
        args_hint="[usd_amount]",
    )
