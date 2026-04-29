"""Liquid staking integrations — Lido + Rocket Pool.

Both protocols accept ETH and mint a liquid receipt token:

  * **Lido** — ETH → stETH (rebases daily, 1:1 ratio with ETH at deposit)
    or wstETH (non-rebasing wrapped version, available across L2s).
  * **Rocket Pool** — ETH → rETH (non-rebasing, exchange rate
    appreciates as rewards accrue).

This service only exposes the **deposit** path. Withdrawals are
multi-step (Lido has a queue with claim NFTs; Rocket Pool burns
on-demand) and are TBD for a later commit.

Mainnet only at this milestone — both protocols are Ethereum-native.
Wrapped versions on L2s exist (cbETH, wstETH on Arbitrum / Optimism /
Base) but the L2 tools route through the bridges, not direct staking.
"""

from __future__ import annotations

from typing import Any

# Lido stETH contract on Ethereum mainnet. Address has been stable
# since 2020.
LIDO_STETH_MAINNET = "0xae7ab96520DE3A18E5e111B5EaAb095312D7fE84"

# Rocket Pool rETH contract on Ethereum mainnet.
RP_RETH_MAINNET = "0xae78736Cd615f374D3085123A210448E74Fc6393"

# Lido submit(address _referral) — selector for ETH → stETH.
SELECTOR_LIDO_SUBMIT = "0xa1903eab"

# Rocket Pool deposit() — selector for ETH → rETH.
SELECTOR_RP_DEPOSIT = "0xd0e30db0"

# Per-protocol contract registries. ``balance_of`` is the ERC-20
# balanceOf selector on the receipt token (used by ``info`` to show
# the user's existing stake).
_PROTOCOLS: dict[str, dict[str, Any]] = {
    "lido": {
        "name": "Lido (stETH)",
        "deposit_to": {1: LIDO_STETH_MAINNET},
        "deposit_selector": SELECTOR_LIDO_SUBMIT,
        "receipt_token": {1: LIDO_STETH_MAINNET},
    },
    "rocketpool": {
        "name": "Rocket Pool (rETH)",
        "deposit_to": {1: RP_RETH_MAINNET},
        "deposit_selector": SELECTOR_RP_DEPOSIT,
        "receipt_token": {1: RP_RETH_MAINNET},
    },
}


class StakingError(RuntimeError):
    """Raised when a protocol/chain combination isn't supported."""


def supports(protocol: str, chain_id: int) -> bool:
    p = _PROTOCOLS.get(protocol.lower())
    if p is None:
        return False
    return chain_id in p["deposit_to"]


def deposit_target(protocol: str, chain_id: int) -> tuple[str, str]:
    """Return ``(contract_address, deposit_calldata_hex)`` for the protocol."""
    p = _PROTOCOLS.get(protocol.lower())
    if p is None or chain_id not in p["deposit_to"]:
        raise StakingError(
            f"{protocol} not supported on chain {chain_id}; "
            "Lido and Rocket Pool are mainnet-only at this milestone."
        )
    contract = p["deposit_to"][chain_id]
    selector = p["deposit_selector"]
    if protocol.lower() == "lido":
        # submit(address referral) — pass zero address for no referral
        from clawmes.lib.abi import encode_address

        calldata = selector + encode_address("0x" + "0" * 40)
    else:
        # deposit() — no args
        calldata = selector
    return contract, calldata


def receipt_token(protocol: str, chain_id: int) -> str:
    p = _PROTOCOLS.get(protocol.lower())
    if p is None or chain_id not in p["receipt_token"]:
        raise StakingError(f"{protocol} not supported on chain {chain_id}")
    return p["receipt_token"][chain_id]


def protocol_name(protocol: str) -> str:
    p = _PROTOCOLS.get(protocol.lower())
    if p is None:
        raise StakingError(f"unknown protocol: {protocol}")
    return p["name"]


def supported_protocols() -> list[str]:
    return list(_PROTOCOLS.keys())
