"""``WalletState`` — immutable snapshot.

Held by the active mode and surfaced via
``services.wallet.get_wallet_state()``. Frozen so callers can hold a
reference and not race with reconnects.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from clawmes.lib.chains import get_chain


@dataclass(frozen=True)
class WalletState:
    connected: bool = False
    mode: str | None = None  # "walletconnect" | "local" | "bankr"
    address: str | None = None
    chain_id: int | None = None
    chain_name: str | None = None

    # Coarse balance summary cache. Refreshed by the wallet service on a
    # tick. Tools that need precise balances should read directly from RPC.
    balances: dict[str, str] = field(default_factory=dict)

    # Names of active spending policies. Detail in policy/storage.py.
    policy_names: tuple[str, ...] = ()

    @classmethod
    def disconnected(cls) -> WalletState:
        return cls()

    @classmethod
    def for_chain(
        cls,
        *,
        mode: str,
        address: str,
        chain_id: int,
        balances: dict[str, str] | None = None,
        policy_names: tuple[str, ...] = (),
    ) -> WalletState:
        chain = get_chain(chain_id)
        return cls(
            connected=True,
            mode=mode,
            address=address,
            chain_id=chain_id,
            chain_name=chain.name,
            balances=dict(balances or {}),
            policy_names=policy_names,
        )

    def balance_summary(self) -> str:
        if not self.balances:
            return "(no cached balances)"
        items = sorted(self.balances.items())
        return ", ".join(f"{v} {k}" for k, v in items[:5])

    def policy_summary(self) -> str:
        if not self.policy_names:
            return "(no policies configured)"
        return ", ".join(self.policy_names)
