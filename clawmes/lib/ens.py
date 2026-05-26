"""ENS resolution for human-readable wallet addresses.

LLMs frequently encounter ENS names in user-facing prose ("send 0.1 to
vitalik.eth"). The wallet tools accept these directly — this module
turns them into 0x addresses by walking the on-chain ENS registry on
Ethereum mainnet (chain id 1).

ENS resolves on mainnet only, by convention; addresses returned here
are valid on every EVM chain. The protocol is two RPC calls:

  1. ``resolver(node)`` on the ENS Registry contract — returns the
     resolver contract for that name's namehash.
  2. ``addr(node)`` on that resolver — returns the user's address.

Either step can return the zero address, which we treat as "name not
registered" and report via :class:`EnsError`.

We intentionally don't depend on ``ens.py`` (the third-party library)
to keep clawmes' dep tree small; the algorithm is short enough to
implement inline.
"""

from __future__ import annotations

from clawmes.lib.logger import logger_for
from clawmes.services.rpc import RpcError, get_rpc_service

_log = logger_for("lib.ens")

# Canonical ENS Registry on Ethereum mainnet. Same address as
# https://docs.ens.domains/contract-api-reference/ens — never moved.
ENS_REGISTRY = "0x00000000000C2E074eC69A0dFb2997BA6C7d2e1e"

#: Base ENS L2 Registry (Coinbase's L2 ENS deployment). Resolves
#: ``*.base.eth`` names natively on Base L2 — bypasses the CCIP-Read
#: hop that ``base.eth`` parent resolution on mainnet would require.
#: Doc: https://docs.base.org/basenames
BASE_ENS_REGISTRY = "0xB94704422c2A1E396835A571837Aa5AE53285a95"

# Function selectors (first 4 bytes of keccak256 of the function signature).
_SELECTOR_RESOLVER = bytes.fromhex("0178b8bf")  # resolver(bytes32)
_SELECTOR_ADDR = bytes.fromhex("3b3b57de")  # addr(bytes32)

_ZERO_ADDRESS = "0x" + "0" * 40
_ZERO_BYTES32 = "0x" + "0" * 64

# Mainnet chain id used for ENS lookups, regardless of which chain the
# wallet is currently on. Bridged ENS variants (e.g. on Base) exist but
# the canonical resolver lives on mainnet.
_ENS_CHAIN_ID = 1

#: Base mainnet chain id for ``*.base.eth`` lookups.
_BASE_CHAIN_ID = 8453


class EnsError(RuntimeError):
    """Raised when an ENS name fails to resolve."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def is_ens_name(value: str) -> bool:
    """Return True if ``value`` looks like an ENS name rather than a hex
    address. We treat any string containing a ``.`` as a candidate so
    subdomains (``app.foo.eth``) work; we do not enforce the ``.eth``
    suffix because OffchainResolver chains use ``.cb.id``, ``.box``,
    etc."""
    if not value:
        return False
    if value.startswith(("0x", "0X")):
        return False
    return "." in value


def namehash(name: str) -> bytes:
    """Compute the ENS namehash of ``name``.

    Recursive definition (per EIP-137):

      namehash("") = 0x00 * 32
      namehash(<label>.<rest>) = keccak256(namehash(<rest>) || keccak256(<label>))

    Names are normalized to lowercase before hashing — the canonical
    ENS spec also requires UTS#46 normalization but most real-world
    names are already lowercase ASCII; we accept the small false-
    negative rate in exchange for not pulling a 200KB IDNA table.
    """
    from eth_utils import keccak

    if not name:
        return b"\x00" * 32
    name = name.lower()
    label, _, rest = name.partition(".")
    return keccak(namehash(rest) + keccak(label.encode("utf-8")))


def is_basename(value: str) -> bool:
    """Return True if ``value`` looks like a Coinbase Basename.

    Basenames are subdomains of ``.base.eth`` (e.g. ``jesse.base.eth``).
    They live on the Base L2 ENS registry rather than the Ethereum
    mainnet registry; ``resolve()`` routes them automatically so most
    callers don't need to branch on this.
    """
    return bool(value) and value.lower().endswith(".base.eth")


def resolve(name: str) -> str:
    """Resolve an ENS name (or Basename) to a checksummed 0x address.

    Detects ``*.base.eth`` names and routes the lookup to the Base L2
    ENS registry. All other names go to Ethereum mainnet's canonical
    ENS registry. The two registries share the same ABI so the
    resolution algorithm is identical — only the registry contract
    address and RPC chain id differ.

    Raises :class:`EnsError` with one of these codes:

      * ``not_registered`` — the registry has no resolver for the name.
      * ``no_address``     — the resolver returned the zero address.
      * ``rpc_error``      — the underlying RPC call failed.
    """
    if is_basename(name):
        registry = BASE_ENS_REGISTRY
        chain_id = _BASE_CHAIN_ID
    else:
        registry = ENS_REGISTRY
        chain_id = _ENS_CHAIN_ID

    rpc = get_rpc_service()
    node = namehash(name)
    node_hex = "0x" + node.hex()

    try:
        resolver_result = rpc.eth_call(
            to=registry,
            data="0x" + (_SELECTOR_RESOLVER + node).hex(),
            chain_id=chain_id,
        )
    except RpcError as exc:
        raise EnsError("rpc_error", f"ENS resolver lookup failed: {exc.message}") from exc

    resolver_addr = _decode_address(resolver_result)
    if resolver_addr is None or resolver_addr == _ZERO_ADDRESS:
        raise EnsError(
            "not_registered",
            f"ENS name {name!r} has no resolver (not registered)",
        )

    try:
        addr_result = rpc.eth_call(
            to=resolver_addr,
            data="0x" + (_SELECTOR_ADDR + node).hex(),
            chain_id=chain_id,
        )
    except RpcError as exc:
        raise EnsError("rpc_error", f"ENS addr lookup failed: {exc.message}") from exc

    address = _decode_address(addr_result)
    if address is None or address == _ZERO_ADDRESS:
        raise EnsError(
            "no_address",
            f"ENS name {name!r} has a resolver but no associated address. (node={node_hex})",
        )

    _log.info("resolved ENS %s -> %s", name, address)

    # Best-effort checksum — eth_utils is already a dep for the rest of
    # the wallet path. Falls back to the lowercase form if checksumming
    # is unavailable for any reason.
    try:
        from eth_utils import to_checksum_address

        return to_checksum_address(address)
    except Exception:  # noqa: BLE001 — defensive fallback
        return address


def _decode_address(result: str | None) -> str | None:
    """Decode a 32-byte ABI-encoded address from an ``eth_call`` result.

    Returns ``None`` for empty / zero responses; the canonical zero
    address (``0x0000…0000``) when the slot is set but unfilled.
    """
    if not result or result == "0x":
        return None
    # Strip 0x and ensure we got 32 bytes (64 hex chars) — anything else
    # means the contract returned a malformed value.
    body = result[2:] if result.startswith("0x") else result
    if len(body) != 64:
        return None
    if body == _ZERO_BYTES32[2:]:
        return _ZERO_ADDRESS
    return "0x" + body[24:].lower()
