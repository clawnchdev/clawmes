"""Delegation type system — contracts, chains, EIP-712 shapes, dataclasses.

All the *static* facts about the MetaMask Delegation Framework live here:
contract addresses (CREATE2-deterministic, identical on every supported
chain), the supported chain set, the EIP-712 domain/type definitions, the
root-authority sentinel, and the dataclasses that model a delegation through
its lifecycle (:class:`Caveat` → :class:`UnsignedDelegation` →
:class:`SignedDelegation`, plus the persisted :class:`DelegationRecord`).

No encoding logic here — that's :mod:`clawmes.delegation.encoding`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# ─── Contract addresses (MetaMask Delegation Framework v1.3.0) ──────────
#
# Deployed at deterministic addresses via CREATE2 across all supported
# chains. Verified from the official deployments registry. These are the
# same values the openclawnch reference stack uses and that were proven
# on Base Sepolia.

DELEGATION_MANAGER = "0xdb9B1e94B5b69Df7e401DDbedE43491141047dB3"

# DeleGator implementations (designation targets for EIP-7702 upgrade).
EIP7702_STATELESS_DELEGATOR = "0x63c0c19a282a1B52b07dD5a65b58948A07DAE32B"
HYBRID_DELEGATOR = "0x48dBe696A4D990079e039489bA2053B36E8FFEC4"
MULTISIG_DELEGATOR = "0x56a9EdB16a0105eb5a4C54f4C062e2868844f3A7"

# Caveat enforcers.
ERC20_TRANSFER_AMOUNT_ENFORCER = "0xf100b0819427117EcF76Ed94B358B1A5b5C6D2Fc"
ERC20_PERIOD_TRANSFER_ENFORCER = "0x474e3Ae7E169e940607cC624Da8A15Eb120139aB"
LIMITED_CALLS_ENFORCER = "0x04658B29F6b82ed55274221a06Fc97D318E25416"
ALLOWED_TARGETS_ENFORCER = "0x7F20f61b1f09b08D970938F6fa563634d65c4EeB"
ALLOWED_METHODS_ENFORCER = "0x2c21fD0Cb9DC8445CB3fb0DC5E7Bb0Aca01842B5"
TIMESTAMP_ENFORCER = "0x1046bb45C8d673d4ea75321280DB34899413c069"
NATIVE_TOKEN_TRANSFER_AMOUNT_ENFORCER = "0xF71af580b9c3078fbc2BBF16FbB8EEd82b330320"
NATIVE_TOKEN_PERIOD_TRANSFER_ENFORCER = "0x9BC0FAf4Aca5AE429F4c06aEEaC517520CB16BD9"
VALUE_LTE_ENFORCER = "0x92Bf12322527cAA612fd31a0e810472BBB106A8F"
NONCE_ENFORCER = "0xDE4f2FAC4B3D87A1d9953Ca5FC09FCa7F366254f"

#: Enforcer name → address, for human-readable compilation summaries.
ENFORCER_NAMES: dict[str, str] = {
    ERC20_TRANSFER_AMOUNT_ENFORCER.lower(): "ERC20TransferAmountEnforcer",
    ERC20_PERIOD_TRANSFER_ENFORCER.lower(): "ERC20PeriodTransferEnforcer",
    LIMITED_CALLS_ENFORCER.lower(): "LimitedCallsEnforcer",
    ALLOWED_TARGETS_ENFORCER.lower(): "AllowedTargetsEnforcer",
    ALLOWED_METHODS_ENFORCER.lower(): "AllowedMethodsEnforcer",
    TIMESTAMP_ENFORCER.lower(): "TimestampEnforcer",
    NATIVE_TOKEN_TRANSFER_AMOUNT_ENFORCER.lower(): "NativeTokenTransferAmountEnforcer",
    NATIVE_TOKEN_PERIOD_TRANSFER_ENFORCER.lower(): "NativeTokenPeriodTransferEnforcer",
    VALUE_LTE_ENFORCER.lower(): "ValueLteEnforcer",
    NONCE_ENFORCER.lower(): "NonceEnforcer",
}


def enforcer_name(address: str) -> str:
    """Return the human-readable enforcer name for ``address`` (or the address)."""
    return ENFORCER_NAMES.get(address.lower(), address)


# ─── Supported chains ───────────────────────────────────────────────────
#
# The framework contracts are deployed on these chains. Testnets (Sepolia,
# Base Sepolia) require a ``CLAWMES_RPC_<id>`` override to be reachable —
# the RPC service only ships mainnet defaults.

CHAIN_NAMES: dict[int, str] = {
    1: "Ethereum",
    8453: "Base",
    42161: "Arbitrum",
    10: "Optimism",
    137: "Polygon",
    59144: "Linea",
    11155111: "Sepolia",
    84532: "Base Sepolia",
}

SUPPORTED_CHAIN_IDS: frozenset[int] = frozenset(CHAIN_NAMES)

#: Default chain for delegation operations.
DEFAULT_CHAIN_ID = 8453  # Base

#: Testnet chain ids (>100000 heuristic mirrors openclawnch).
TESTNET_CHAIN_IDS: frozenset[int] = frozenset({11155111, 84532})


def is_supported_chain(chain_id: int) -> bool:
    return chain_id in SUPPORTED_CHAIN_IDS


def chain_name(chain_id: int) -> str:
    return CHAIN_NAMES.get(chain_id, str(chain_id))


# ─── Sentinels ──────────────────────────────────────────────────────────

#: Root authority for top-level delegations (no parent). The on-chain
#: DelegationManager uses 0xff..ff (32 bytes) as the sentinel — NOT zero.
ROOT_AUTHORITY = "0x" + "f" * 64

#: Default ERC-7579 single-call execution mode: all zeros.
EXECUTE_MODE_DEFAULT = "0x" + "0" * 64

#: Zero address placeholder (filled with the delegator at redemption time).
ZERO_ADDRESS = "0x" + "0" * 40


# ─── Well-known period durations (seconds) ──────────────────────────────

PERIOD_SECONDS: dict[str, int] = {
    "hourly": 3600,
    "daily": 86400,
    "weekly": 604800,
    "monthly": 2592000,  # 30 days
}


# ─── EIP-712 typed-data ─────────────────────────────────────────────────
#
# The delegator signs an EIP-712 ``Delegation`` message. NOTE: the on-chain
# DELEGATION_TYPEHASH uses ``Caveat(address enforcer,bytes terms)`` — WITHOUT
# the ``args`` field. ``args`` exists in the ABI struct (runtime data passed
# during enforcement) but is excluded from the signature. Getting this wrong
# was integration bug #1 in the reference stack.

EIP712_DELEGATION_TYPES: dict[str, list[dict[str, str]]] = {
    "Delegation": [
        {"name": "delegate", "type": "address"},
        {"name": "delegator", "type": "address"},
        {"name": "authority", "type": "bytes32"},
        {"name": "caveats", "type": "Caveat[]"},
        {"name": "salt", "type": "uint256"},
    ],
    "Caveat": [
        {"name": "enforcer", "type": "address"},
        {"name": "terms", "type": "bytes"},
    ],
}

#: EIP712Domain type entry, required by eth_account.encode_typed_data.
_EIP712_DOMAIN_TYPE = [
    {"name": "name", "type": "string"},
    {"name": "version", "type": "string"},
    {"name": "chainId", "type": "uint256"},
    {"name": "verifyingContract", "type": "address"},
]


def delegation_domain(chain_id: int) -> dict[str, object]:
    """Build the EIP-712 domain for the DelegationManager on ``chain_id``."""
    return {
        "name": "DelegationManager",
        "version": "1",
        "chainId": chain_id,
        "verifyingContract": DELEGATION_MANAGER,
    }


# ─── Dataclasses ────────────────────────────────────────────────────────

DelegationStatus = Literal["unsigned", "signed", "active", "revoked", "expired"]


@dataclass(frozen=True)
class Caveat:
    """A single restriction on a delegation.

    ``enforcer`` is the caveat-enforcer contract address; ``terms`` is the
    ABI-encoded parameters that enforcer checks (encoding varies per
    enforcer — see :mod:`clawmes.delegation.encoding`). ``args`` is runtime
    data supplied at redemption (almost always ``"0x"``) and is excluded
    from the EIP-712 signature.
    """

    enforcer: str
    terms: str  # 0x-prefixed hex
    args: str = "0x"


@dataclass(frozen=True)
class UnsignedDelegation:
    """A delegation before the delegator signs it."""

    delegate: str
    delegator: str
    authority: str  # ROOT_AUTHORITY for top-level, else parent hash
    caveats: tuple[Caveat, ...]
    salt: int


@dataclass(frozen=True)
class SignedDelegation:
    """A delegation with the delegator's EIP-712 signature attached."""

    delegate: str
    delegator: str
    authority: str
    caveats: tuple[Caveat, ...]
    salt: int
    signature: str  # 0x-prefixed hex

    @classmethod
    def from_unsigned(cls, unsigned: UnsignedDelegation, signature: str) -> SignedDelegation:
        return cls(
            delegate=unsigned.delegate,
            delegator=unsigned.delegator,
            authority=unsigned.authority,
            caveats=unsigned.caveats,
            salt=unsigned.salt,
            signature=signature,
        )


@dataclass
class DelegationRecord:
    """A stored, signed delegation plus the metadata clawmes tracks.

    Persisted as one JSON file per record by
    :class:`clawmes.delegation.store.DelegationStore`. ``salt`` is stored as
    a hex string for JSON safety; caveats carry their hex terms/args.
    """

    id: str
    chain_id: int
    delegation: SignedDelegation
    status: DelegationStatus = "signed"
    #: On-chain keccak256 hash (from getDelegationHash), or "0x" if unknown.
    hash: str = "0x"
    #: The policy this delegation was compiled from (name), if any.
    policy_name: str = ""
    #: Tool names the delegation is scoped to (empty = all write tools).
    tools: tuple[str, ...] = ()
    #: ERC-7715 permissions context hex (set only for the 7715 grant path).
    permissions_context: str = ""
    #: ISO timestamp of the earliest TimestampEnforcer expiry, if any.
    expires_at: str = ""
    #: Rules that had no on-chain enforcer (app-layer only), for display.
    unmapped: tuple[str, ...] = ()
    created_at: str = ""
    last_checked_at: str = ""
    kind: Literal["eip7710", "eip7715"] = "eip7710"

    def is_redeemable(self) -> bool:
        """A record can be redeemed only when signed/active (not revoked)."""
        return self.status in ("signed", "active")


@dataclass
class CompiledDelegation:
    """Output of the compiler: an unsigned delegation + human-facing detail."""

    delegation: UnsignedDelegation
    mapped: list[str] = field(default_factory=list)
    unmapped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    expires_at: str = ""


@dataclass(frozen=True)
class ExecutionAction:
    """A single on-chain action to redeem through a delegation.

    ``call_data`` may contain a zero-address placeholder (32 zero bytes) for
    an ``onBehalfOf``/``from``/``receiver`` slot the extractor couldn't fill;
    the executor substitutes the delegator address before redemption.
    """

    target: str
    value: int
    call_data: str  # 0x-prefixed hex ("0x" for a plain value transfer)
