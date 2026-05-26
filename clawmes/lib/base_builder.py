"""Base builder code — on-chain attribution for Coinbase builder rewards.

Coinbase rewards builders for on-chain activity attributable to their
builder ID. Attribution works by appending a known sentinel suffix to
every transaction's ``data`` field. The EVM ignores trailing calldata
beyond the ABI-decoded args, so the suffix is harmless to the
transaction's actual execution.

clawmes appends the suffix to every Base mainnet tx it broadcasts —
swaps, non-custodial deploys, $CLAWNCH burns — so the plugin earns
Coinbase builder rewards proportional to the on-chain activity it
drives. The clawnch backend's custodial deploy flow already appends
this in ``api/lib/clanker-backend.ts``; this module is the
client-side mirror.

The exact 28-byte suffix is the public Coinbase-issued code for
Clawnch's builder ID (``bc_z92vaimh``); kept identical to the
server-side constant in ``clawnch/api/lib/constants.ts``.
"""

from __future__ import annotations

#: Coinbase builder code suffix appended to Base mainnet calldata.
#: 28 bytes, public on-chain marker. Identical to the server-side
#: ``BASE_BUILDER_CODE`` in ``clawnch/api/lib/constants.ts``.
BASE_BUILDER_CODE = "0x62635f7a39327661696d680b0080218021802180218021802180218021"


#: Base mainnet chain ID. Only chain where the builder code applies.
_BASE_CHAIN_ID = 8453


def append_builder_code(data: str, chain_id: int | None) -> str:
    """Return ``data`` with the builder-code suffix appended on Base mainnet.

    Pass-through on non-Base chains so the same wrapper can be applied
    universally without hand-checking the chain id at every call site.

    Args:
      data: Hex calldata string (``0x``-prefixed) to append to.
      chain_id: Network chain id. Only ``8453`` triggers the append.

    Returns:
      The original calldata on non-Base chains. On Base, the original
      calldata concatenated with the builder-code suffix (without the
      ``0x`` prefix of the suffix, since the suffix only carries one
      hex prefix at the front of the combined value).
    """
    if chain_id != _BASE_CHAIN_ID:
        return data
    if not data.startswith("0x"):
        # Defensive: build a clean prefix if the caller fed in raw hex.
        data = "0x" + data
    return data + BASE_BUILDER_CODE[2:]
