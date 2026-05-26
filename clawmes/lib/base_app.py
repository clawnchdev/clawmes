"""Base App deep links.

Coinbase's Base App (https://base.app) is the consumer wallet + mini-app
shell most Base users live in day-to-day. Whenever clawmes returns a
"here's the thing you just did on-chain" message, surfacing a Base App
link lets the user jump straight to the token / tx in their wallet
without having to copy the address into a separate browser.

Two URL patterns:

  * ``token_url(address)`` — opens the token's page in Base App's
    Wallet tab (balance, swap, send).
  * ``tx_url(tx_hash)`` — opens the tx details in the Base App
    transactions view.

These are best-effort renderings of public URLs. The exact patterns
can shift as Base App's URL scheme evolves; override via env vars
(``CLAWMES_BASE_APP_TOKEN_URL``, ``CLAWMES_BASE_APP_TX_URL``) if the
defaults stop working.
"""

from __future__ import annotations

import os

_DEFAULT_TOKEN_URL_TEMPLATE = "https://base.app/?token={address}"
_DEFAULT_TX_URL_TEMPLATE = "https://base.app/?tx={tx_hash}"


def token_url(address: str) -> str:
    """Return a Base App URL that opens ``address`` in the Wallet tab.

    Empty / falsy inputs return an empty string so callers can chain
    this into renderers without null-checking.
    """
    if not address:
        return ""
    template = os.environ.get("CLAWMES_BASE_APP_TOKEN_URL", _DEFAULT_TOKEN_URL_TEMPLATE)
    return template.format(address=address)


def tx_url(tx_hash: str) -> str:
    """Return a Base App URL that opens ``tx_hash`` in the tx detail view.

    Empty / falsy inputs return an empty string.
    """
    if not tx_hash:
        return ""
    template = os.environ.get("CLAWMES_BASE_APP_TX_URL", _DEFAULT_TX_URL_TEMPLATE)
    return template.format(tx_hash=tx_hash)
