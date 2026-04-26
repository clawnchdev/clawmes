"""Minimal ABI encoding/decoding for the read paths we actually use.

We don't pull in ``web3.py``'s codec for the few function selectors
clawmes needs at the read layer — it's overkill for ``balanceOf`` and
``decimals`` and adds boot time. Anything more elaborate (write-side
encoding for swaps, multi-arg structs) can defer to web3 later.

The two functions we need:

  * ``balanceOf(address) returns (uint256)`` — selector ``0x70a08231``
  * ``decimals() returns (uint8)`` — selector ``0x313ce567``

Both are static across every ERC-20 token; the constants below are
derived from ``keccak256("balanceOf(address)")[:4]`` /
``keccak256("decimals()")[:4]``.
"""

from __future__ import annotations

# Function selectors (4-byte keccak256 prefixes) for the ERC-20 reads
# we issue. Pinned as constants because they'll never change.
SELECTOR_BALANCE_OF = "0x70a08231"
SELECTOR_DECIMALS = "0x313ce567"
SELECTOR_SYMBOL = "0x95d89b41"
SELECTOR_NAME = "0x06fdde03"


def encode_address(address: str) -> str:
    """Encode an Ethereum address as a 32-byte left-padded hex string.

    Returns the 64-hex-char value (no ``0x`` prefix). Raises ``ValueError``
    on malformed input.
    """
    if not isinstance(address, str):
        raise ValueError(f"expected str, got {type(address).__name__}")
    cleaned = address.lower().removeprefix("0x")
    if len(cleaned) != 40 or not all(c in "0123456789abcdef" for c in cleaned):
        raise ValueError(f"not a hex address: {address!r}")
    return cleaned.rjust(64, "0")


def encode_balance_of(address: str) -> str:
    """Build calldata for ``balanceOf(<address>)``.

    Returns a ``0x``-prefixed hex string suitable for ``eth_call.data``.
    """
    return SELECTOR_BALANCE_OF + encode_address(address)


def encode_decimals_call() -> str:
    """Build calldata for ``decimals()``."""
    return SELECTOR_DECIMALS


def decode_uint(hex_data: str) -> int:
    """Decode a single uint256 (or any uint up to 256 bits).

    Accepts ``0x``-prefixed or bare hex. Empty / ``"0x"`` returns 0
    (some RPCs return that for un-deployed contracts).
    """
    if not hex_data:
        return 0
    cleaned = hex_data.removeprefix("0x")
    if not cleaned:
        return 0
    return int(cleaned, 16)


def decode_uint8(hex_data: str) -> int:
    """Decode a uint8. Same as :func:`decode_uint` but caps at 255."""
    value = decode_uint(hex_data)
    if value > 255:
        raise ValueError(f"value {value} exceeds uint8 range")
    return value
