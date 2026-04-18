"""Address parsing and validation.

ENS resolution itself is in ``clawmes/services/ens.py`` — this module
covers the cheap, network-free checks: format validation, checksumming,
zero / dead address detection, and the dispatch helper that decides
whether a string needs ENS resolution.
"""

from __future__ import annotations

import re

_HEX_ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_ENS_RE = re.compile(r"^[a-z0-9-]+(\.[a-z0-9-]+)*\.eth$", re.IGNORECASE)


ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
DEAD_ADDRESS = "0x000000000000000000000000000000000000dEaD"


def is_hex_address(value: str) -> bool:
    """Quick syntactic check — does it look like a 20-byte hex address?"""
    return bool(_HEX_ADDR_RE.match(value))


def is_ens_name(value: str) -> bool:
    """Quick syntactic check — does it look like an ENS name?"""
    return bool(_ENS_RE.match(value))


def is_zero_address(value: str) -> bool:
    if not is_hex_address(value):
        return False
    return value.lower() == ZERO_ADDRESS


def is_dead_address(value: str) -> bool:
    if not is_hex_address(value):
        return False
    return value.lower() == DEAD_ADDRESS.lower()


def to_checksum(value: str) -> str:
    """Return the EIP-55 checksummed form.

    Defers to ``eth_utils.to_checksum_address`` when available. Falls back
    to lower-case if the dependency is missing (only happens in unit
    tests that monkeypatch ``eth_utils``).
    """
    try:
        from eth_utils import to_checksum_address  # type: ignore[import-not-found]

        return to_checksum_address(value)
    except ImportError:
        if not is_hex_address(value):
            raise ValueError(f"Not a hex address: {value!r}") from None
        return value.lower()


def short(value: str, *, head: int = 6, tail: int = 4) -> str:
    """Shorten an address for display (e.g. ``0x123…abcd``)."""
    if not is_hex_address(value):
        return value
    return f"{value[:head]}…{value[-tail:]}"


def needs_ens_resolution(value: str) -> bool:
    """Return ``True`` if a string is a recognizable ENS name.

    Tools dispatch on this to decide whether to call the ENS service.
    """
    return is_ens_name(value)
