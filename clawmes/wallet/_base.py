"""Abstract base for wallet modes.

A wallet mode is an implementation of "how do we sign and submit
transactions for this user?" Three modes exist; all return identical
shapes (``tx_hash`` strings, ``WalletState`` snapshots) so callers
don't need to care which is active.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from clawmes.wallet.state import WalletState


class WalletMode(ABC):
    """ABC for the three wallet modes."""

    name: str = ""

    @abstractmethod
    def connect(self, **kwargs: Any) -> WalletState:
        """Initialize / pair / authenticate. Return the resulting state."""

    @abstractmethod
    def disconnect(self) -> None:
        """Tear down the active session."""

    @abstractmethod
    def state(self) -> WalletState:
        """Current snapshot."""

    @abstractmethod
    def send_transaction(
        self,
        *,
        to: str,
        value: int,
        data: bytes | str = b"",
        chain_id: int | None = None,
        gas: int | None = None,
        max_fee_per_gas: int | None = None,
        max_priority_fee_per_gas: int | None = None,
    ) -> str:
        """Submit a transaction; return the tx hash.

        For WalletConnect mode this hands off to the user's phone for
        approval and may block until they sign. For local-key it signs
        and submits directly. For Bankr it POSTs to the custodial API.
        """

    @abstractmethod
    def sign_typed_data_v4(self, typed_data: dict[str, Any]) -> str:
        """EIP-712 sign — returns a hex signature."""

    def sign_personal_message(self, message: bytes | str) -> str:
        """``personal_sign`` — most modes implement; default raises."""
        raise NotImplementedError(f"{self.name} does not support personal_sign")

    @abstractmethod
    def switch_chain(self, chain_id: int) -> WalletState:
        """Switch the active chain.

        Each mode implements differently:
          * WalletConnect — relays the switch to the user's phone via
            the bridge.
          * LocalKey      — pure metadata update (no session anywhere).
          * Bankr         — re-pulls the address map and picks the
            address for the new chain.

        Returns the resulting WalletState. Raises if the switch can't
        be applied (wallet not connected, chain unsupported, user
        rejects on the phone, etc.).
        """
