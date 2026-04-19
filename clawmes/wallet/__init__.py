"""Wallet mode implementations.

Three modes:

  * :class:`walletconnect.WalletConnectMode` — pairs to user's phone
    wallet via the Node ``clawmes-wc-bridge`` subprocess. Every write tx
    goes to the phone for approval.
  * :class:`local_key.LocalKeyMode` — BIP-39 mnemonic generated locally,
    encrypted with scrypt + AES-256-GCM, stored via ``keyring``
    (macOS Keychain) or encrypted file fallback.
  * :class:`bankr.BankrMode` — custodial. Talks to Bankr HTTP API. Multi
    -chain. Required for Avantis leverage and Polymarket prediction
    markets.

The ``services.wallet.WalletService`` selects an active mode at start
based on ``clawmes.wallet.mode`` config and dispatches every read/write
through it.
"""

from __future__ import annotations

from clawmes.wallet._base import WalletMode
from clawmes.wallet.state import WalletState

__all__ = ["WalletMode", "WalletState"]
