"""Wallet service — singleton accessor for wallet state.

This is the public face the rest of clawmes talks to. Implementations
(WalletConnect, local key, Bankr custodial) live under ``clawmes/wallet/``
and the service routes to whichever is configured.

At this milestone the implementation is a stub: ``get_wallet_state()``
returns a disconnected state so tools that depend on a wallet fail with
a clean "no wallet connected" error rather than crashing.
"""

from __future__ import annotations

from dataclasses import dataclass

from clawmes.services._base import Service


@dataclass(frozen=True)
class WalletState:
    """Snapshot of wallet state at a point in time.

    Immutable so callers can hold a reference and not race with reconnects.
    """

    connected: bool = False
    mode: str | None = None  # "walletconnect" | "local" | "bankr"
    address: str | None = None
    chain_id: int | None = None
    chain_name: str | None = None

    def balance_summary(self) -> str:
        return "(not implemented)"

    def policy_summary(self) -> str:
        return "(no policies configured)"


class WalletService(Service):
    id = "clawmes.wallet"

    def __init__(self) -> None:
        self._state = WalletState(connected=False)

    def start(self) -> None:
        # Lazy: real key validation deferred to first use.
        pass

    def stop(self) -> None:
        pass

    @property
    def state(self) -> WalletState:
        return self._state


_instance: WalletService | None = None


def get_wallet_service() -> WalletService:
    global _instance
    if _instance is None:
        _instance = WalletService()
    return _instance


def get_wallet_state() -> WalletState:
    return get_wallet_service().state
