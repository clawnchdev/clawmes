"""Chain registry — known EVM chains and their native tokens.

Used by:
  * ``transfer`` and other write tools to display readable chain names
  * ``defi_swap`` to validate the user-requested chain is supported
  * ``defi_balance`` to iterate across configured chains
  * ``hermes clawmes doctor`` to report which chains have an RPC
    configured

The registry below is the curated default. Users can extend by setting
``clawmes.chains.extra`` in ``~/.hermes/config.yaml``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Chain:
    """Static metadata for a single EVM chain."""

    chain_id: int
    name: str
    short_name: str
    native_symbol: str
    native_decimals: int
    block_explorer_url: str
    is_l2: bool = False


# Curated set — matches the openclawnch chain support matrix. Bump cautiously
# — adding a chain implicitly extends the network allowlist (RPCs are added)
# and may require adapter updates (LiFi, 0x, Uniswap addresses, etc.).
CHAINS: dict[int, Chain] = {
    1: Chain(
        chain_id=1,
        name="Ethereum Mainnet",
        short_name="ethereum",
        native_symbol="ETH",
        native_decimals=18,
        block_explorer_url="https://etherscan.io",
    ),
    8453: Chain(
        chain_id=8453,
        name="Base",
        short_name="base",
        native_symbol="ETH",
        native_decimals=18,
        block_explorer_url="https://basescan.org",
        is_l2=True,
    ),
    42161: Chain(
        chain_id=42161,
        name="Arbitrum One",
        short_name="arbitrum",
        native_symbol="ETH",
        native_decimals=18,
        block_explorer_url="https://arbiscan.io",
        is_l2=True,
    ),
    10: Chain(
        chain_id=10,
        name="Optimism",
        short_name="optimism",
        native_symbol="ETH",
        native_decimals=18,
        block_explorer_url="https://optimistic.etherscan.io",
        is_l2=True,
    ),
    137: Chain(
        chain_id=137,
        name="Polygon",
        short_name="polygon",
        native_symbol="MATIC",
        native_decimals=18,
        block_explorer_url="https://polygonscan.com",
    ),
    324: Chain(
        chain_id=324,
        name="zkSync Era",
        short_name="zksync",
        native_symbol="ETH",
        native_decimals=18,
        block_explorer_url="https://explorer.zksync.io",
        is_l2=True,
    ),
    534352: Chain(
        chain_id=534352,
        name="Scroll",
        short_name="scroll",
        native_symbol="ETH",
        native_decimals=18,
        block_explorer_url="https://scrollscan.com",
        is_l2=True,
    ),
    81457: Chain(
        chain_id=81457,
        name="Blast",
        short_name="blast",
        native_symbol="ETH",
        native_decimals=18,
        block_explorer_url="https://blastscan.io",
        is_l2=True,
    ),
}


_BY_SHORT_NAME = {c.short_name: c for c in CHAINS.values()}


def get_chain(chain_id_or_name: int | str) -> Chain:
    """Look up a chain by ID, short name, or full name (case-insensitive)."""
    if isinstance(chain_id_or_name, int):
        chain = CHAINS.get(chain_id_or_name)
        if chain is None:
            raise KeyError(f"Unknown chain id: {chain_id_or_name}")
        return chain

    key = chain_id_or_name.strip().lower()
    chain = _BY_SHORT_NAME.get(key)
    if chain is not None:
        return chain
    for c in CHAINS.values():
        if c.name.lower() == key:
            return c
    raise KeyError(f"Unknown chain: {chain_id_or_name!r}")


def is_supported(chain_id: int) -> bool:
    return chain_id in CHAINS


def chain_ids() -> list[int]:
    return list(CHAINS.keys())


def default_chain_id() -> int:
    """Default chain when the user hasn't specified one. Base."""
    return 8453
