"""Aave V3 lending integration.

Aave V3 is the canonical lending market on EVM chains — over $20B
TVL across all deployments. Each chain has its own Pool contract;
calldata format is identical across deployments.

This service exposes:

  * The Pool contract address for each supported chain.
  * Calldata encoders for the five user-facing operations: supply,
    withdraw, borrow, repay, and getUserAccountData (read).

The tool wraps these into ``defi_lend`` actions; this service just
provides the on-chain contract surface so the tool body stays focused
on UX (input validation, simulation, receipt rendering).
"""

from __future__ import annotations

from typing import Any

# Aave V3 Pool contract addresses. Cross-checked against
# https://aave.com/docs/resources/addresses (2024). These contracts
# are upgradeable proxies; the V3 logic is what matters, the proxy
# address is stable.
_POOL_ADDRESSES: dict[int, str] = {
    1: "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2",  # Ethereum mainnet
    8453: "0xA238Dd80C259a72e81d7e4664a9801593F98d1c5",  # Base
    42161: "0x794a61358D6845594F94dc1DB02A252b5b4814aD",  # Arbitrum
    10: "0x794a61358D6845594F94dc1DB02A252b5b4814aD",  # Optimism
    137: "0x794a61358D6845594F94dc1DB02A252b5b4814aD",  # Polygon
}

# Function selectors for the Aave V3 Pool contract. Pinned constants —
# they're keccak256(signature)[:4] and never change.
SELECTOR_SUPPLY = "0x617ba037"  # supply(address,uint256,address,uint16)
SELECTOR_WITHDRAW = "0x69328dec"  # withdraw(address,uint256,address)
SELECTOR_BORROW = "0xa415bcad"  # borrow(address,uint256,uint256,uint16,address)
SELECTOR_REPAY = "0x573ade81"  # repay(address,uint256,uint256,address)
SELECTOR_USER_ACCOUNT_DATA = "0xbf92857c"  # getUserAccountData(address)

# Interest rate mode constants. Aave V3 only supports variable rate
# now (mode=2); stable was deprecated post-incident in 2023.
RATE_MODE_VARIABLE = 2

# Aave reports values in 8-decimal USD (their "base currency"). We
# expose the raw integer; the tool's renderer converts to human USD
# via the standard convention (base / 10^8).
USD_DECIMALS = 8


class AaveError(RuntimeError):
    """Raised on unsupported-chain queries."""


def pool_address(chain_id: int) -> str:
    """Return the Aave V3 Pool address for ``chain_id`` or raise."""
    if chain_id not in _POOL_ADDRESSES:
        raise AaveError(f"Aave V3 not deployed on chain {chain_id}")
    return _POOL_ADDRESSES[chain_id]


def supports_chain(chain_id: int) -> bool:
    return chain_id in _POOL_ADDRESSES


def encode_supply(asset: str, amount: int, on_behalf_of: str) -> str:
    """Build calldata for ``Pool.supply(asset, amount, onBehalfOf, 0)``.

    Referral code is hardcoded to 0 — the field exists for
    backwards-compat with V2 but is unused in V3.
    """
    from clawmes.lib.abi import encode_address, encode_uint

    return (
        SELECTOR_SUPPLY
        + encode_address(asset)
        + encode_uint(amount)
        + encode_address(on_behalf_of)
        + encode_uint(0, bits=16).rjust(64, "0")
    )


def encode_withdraw(asset: str, amount: int, to: str) -> str:
    """Build calldata for ``Pool.withdraw(asset, amount, to)``.

    ``amount = type(uint256).max`` withdraws the entire deposit.
    """
    from clawmes.lib.abi import encode_address, encode_uint

    return SELECTOR_WITHDRAW + encode_address(asset) + encode_uint(amount) + encode_address(to)


def encode_borrow(asset: str, amount: int, on_behalf_of: str) -> str:
    """Build calldata for ``Pool.borrow(asset, amount, 2, 0, onBehalfOf)``.

    Hardcodes interestRateMode=2 (variable) since stable rate was
    deprecated. Referral code 0.
    """
    from clawmes.lib.abi import encode_address, encode_uint

    return (
        SELECTOR_BORROW
        + encode_address(asset)
        + encode_uint(amount)
        + encode_uint(RATE_MODE_VARIABLE)
        + encode_uint(0, bits=16).rjust(64, "0")
        + encode_address(on_behalf_of)
    )


def encode_repay(asset: str, amount: int, on_behalf_of: str) -> str:
    """Build calldata for ``Pool.repay(asset, amount, 2, onBehalfOf)``.

    ``amount = type(uint256).max`` repays the full debt.
    """
    from clawmes.lib.abi import encode_address, encode_uint

    return (
        SELECTOR_REPAY
        + encode_address(asset)
        + encode_uint(amount)
        + encode_uint(RATE_MODE_VARIABLE)
        + encode_address(on_behalf_of)
    )


def encode_get_user_account_data(user: str) -> str:
    """Build calldata for ``Pool.getUserAccountData(user)``.

    Used as ``eth_call`` to read the user's lending position without
    paying gas.
    """
    from clawmes.lib.abi import encode_address

    return SELECTOR_USER_ACCOUNT_DATA + encode_address(user)


def decode_user_account_data(hex_data: str) -> dict[str, Any]:
    """Decode the tuple returned by ``getUserAccountData``.

    Returns a dict with all six fields:

      * ``total_collateral_base`` — sum of supplied assets in 8-dec USD
      * ``total_debt_base``       — sum of borrows in 8-dec USD
      * ``available_borrows_base`` — remaining borrowing capacity
      * ``current_liquidation_threshold`` — % at which position liquidates
      * ``ltv``                    — current loan-to-value ratio
      * ``health_factor``          — collateral/debt ratio in ray (1e18)

    Health factor < 1.0 (1e18 in raw units) means liquidatable.
    """
    body = hex_data.removeprefix("0x")
    if len(body) < 6 * 64:
        return {
            "total_collateral_base": 0,
            "total_debt_base": 0,
            "available_borrows_base": 0,
            "current_liquidation_threshold": 0,
            "ltv": 0,
            "health_factor": 0,
        }
    chunks = [int(body[i * 64 : (i + 1) * 64], 16) for i in range(6)]
    return {
        "total_collateral_base": chunks[0],
        "total_debt_base": chunks[1],
        "available_borrows_base": chunks[2],
        "current_liquidation_threshold": chunks[3],
        "ltv": chunks[4],
        "health_factor": chunks[5],
    }
