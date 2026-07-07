"""ABI encoding for delegation — verified byte-for-byte against viem.

Everything the DelegationManager and caveat enforcers need on the wire:

  * caveat *terms* encoders (per enforcer)
  * the ERC-7579 single execution (``encodePacked(target,value,callData)``)
  * the permission context (``abi.encode(Delegation[])``, leaf-first)
  * the EIP-712 typed-data dict the delegator signs
  * function selectors + full calldata for ``redeemDelegations``,
    ``disableDelegation``, ``getDelegationHash``, ``disabledDelegations``

Uses ``eth-abi`` (dynamic ABI codec) and ``eth-utils.keccak``. The golden
vectors in ``tests/delegation/test_encoding.py`` were produced with viem
2.x and every function here reproduces them exactly.
"""

from __future__ import annotations

from collections.abc import Sequence

from eth_abi import encode as abi_encode
from eth_utils import keccak

from clawmes.delegation.types import SignedDelegation, UnsignedDelegation

# The Solidity signature of the Delegation tuple (with args) used for the
# permission context, disableDelegation, and getDelegationHash. Caveats
# carry (enforcer, terms, args) on the wire even though args is excluded
# from the EIP-712 signature.
_DELEGATION_TUPLE = "(address,address,bytes32,(address,bytes,bytes)[],uint256,bytes)"


# ─── low-level helpers ──────────────────────────────────────────────────


def _b(hexstr: str) -> bytes:
    """0x-hex → bytes. Empty / "0x" → b""."""
    s = hexstr[2:] if hexstr.startswith(("0x", "0X")) else hexstr
    return bytes.fromhex(s) if s else b""


def _addr_bytes(address: str) -> bytes:
    raw = _b(address)
    if len(raw) != 20:
        raise ValueError(f"not a 20-byte address: {address!r}")
    return raw


def _hex(raw: bytes) -> str:
    return "0x" + raw.hex()


def selector(signature: str) -> str:
    """4-byte keccak selector for a Solidity function signature."""
    return "0x" + keccak(text=signature)[:4].hex()


# Precomputed selectors (verified against viem toFunctionSelector).
SEL_REDEEM = selector("redeemDelegations(bytes[],bytes32[],bytes[])")
SEL_DISABLE = selector(f"disableDelegation({_DELEGATION_TUPLE})")
SEL_GET_HASH = selector(f"getDelegationHash({_DELEGATION_TUPLE})")
SEL_DISABLED = selector("disabledDelegations(bytes32)")

# Enforcer read selectors (spentMap / callCounts share the same signature).
SEL_SPENT_MAP = selector("spentMap(address,bytes32)")
SEL_CALL_COUNTS = selector("callCounts(address,bytes32)")


# ─── caveat terms encoders ──────────────────────────────────────────────


def terms_value_lte(max_value_wei: int) -> str:
    """ValueLteEnforcer — abi.encode(uint256 maxValue). Caps msg.value/call."""
    return _hex(abi_encode(["uint256"], [max_value_wei]))


def terms_native_transfer_amount(max_wei: int) -> str:
    """NativeTokenTransferAmountEnforcer — abi.encode(uint256). Lifetime cap."""
    return _hex(abi_encode(["uint256"], [max_wei]))


def terms_native_period(allowance_wei: int, start_time: int, period_seconds: int) -> str:
    """NativeTokenPeriodTransferEnforcer — abi.encode(uint256,uint256,uint256).

    ``(allowance, startTime, period)`` — a spend budget that resets each
    ``period`` seconds. ``startTime=0`` means "from first use".
    """
    return _hex(
        abi_encode(["uint256", "uint256", "uint256"], [allowance_wei, start_time, period_seconds])
    )


def terms_erc20_transfer_amount(token: str, max_amount: int) -> str:
    """ERC20TransferAmountEnforcer — encodePacked(address, uint256), 52 bytes.

    NOT abi.encode — the enforcer reads packed bytes. Getting this wrong
    (padding to 64 bytes) was integration bug #4 in the reference stack.
    """
    return _hex(_addr_bytes(token) + max_amount.to_bytes(32, "big"))


def terms_erc20_period(token: str, allowance: int, start_time: int, period_seconds: int) -> str:
    """ERC20PeriodTransferEnforcer — abi.encode(address,uint256,uint256,uint256)."""
    return _hex(
        abi_encode(
            ["address", "uint256", "uint256", "uint256"],
            [token, allowance, start_time, period_seconds],
        )
    )


def terms_limited_calls(max_calls: int) -> str:
    """LimitedCallsEnforcer — abi.encode(uint256 count). Lifetime call cap."""
    return _hex(abi_encode(["uint256"], [max_calls]))


def terms_timestamp(execute_after: int, execute_before: int) -> str:
    """TimestampEnforcer — abi.encode(uint128 executeAfter, uint128 executeBefore)."""
    return _hex(abi_encode(["uint128", "uint128"], [execute_after, execute_before]))


def terms_allowed_targets(targets: Sequence[str]) -> str:
    """AllowedTargetsEnforcer — abi.encode(address[])."""
    return _hex(abi_encode(["address[]"], [list(targets)]))


# ─── execution + permission context ─────────────────────────────────────


def encode_execution(target: str, value: int, call_data: str) -> str:
    """ERC-7579 single execution: encodePacked(address, uint256, bytes)."""
    return _hex(_addr_bytes(target) + value.to_bytes(32, "big") + _b(call_data))


def _delegation_tuple(delegation: SignedDelegation) -> tuple:
    return (
        _addr_bytes(delegation.delegate),
        _addr_bytes(delegation.delegator),
        _b(delegation.authority),
        [(_addr_bytes(c.enforcer), _b(c.terms), _b(c.args)) for c in delegation.caveats],
        delegation.salt,
        _b(delegation.signature),
    )


def encode_permission_context(chain: Sequence[SignedDelegation]) -> str:
    """abi.encode(Delegation[]) for redeemDelegations.

    Accepts a chain ordered root→leaf (as stored). The DelegationManager
    expects leaf-first (``delegations[0].delegate == msg.sender``), so a
    multi-element chain is reversed here; a single root delegation is not.
    Reversing a >1 chain was integration bug #5 in the reference stack.
    """
    ordered = list(reversed(chain)) if len(chain) > 1 else list(chain)
    tuples = [_delegation_tuple(d) for d in ordered]
    return _hex(abi_encode([f"{_DELEGATION_TUPLE}[]"], [tuples]))


# ─── EIP-712 typed data ─────────────────────────────────────────────────


def build_typed_data(delegation: UnsignedDelegation | SignedDelegation, chain_id: int) -> dict:
    """Build the EIP-712 message dict the delegator signs.

    Caveats are reduced to ``{enforcer, terms}`` (no ``args``) to match the
    on-chain DELEGATION_TYPEHASH. Includes the ``EIP712Domain`` type entry
    required by ``eth_account.encode_typed_data(full_message=...)``.
    """
    from clawmes.delegation.types import (
        _EIP712_DOMAIN_TYPE,
        EIP712_DELEGATION_TYPES,
        delegation_domain,
    )

    return {
        "types": {"EIP712Domain": _EIP712_DOMAIN_TYPE, **EIP712_DELEGATION_TYPES},
        "primaryType": "Delegation",
        "domain": delegation_domain(chain_id),
        "message": {
            "delegate": delegation.delegate,
            "delegator": delegation.delegator,
            "authority": delegation.authority,
            "caveats": [{"enforcer": c.enforcer, "terms": c.terms} for c in delegation.caveats],
            "salt": delegation.salt,
        },
    }


# ─── full calldata builders ─────────────────────────────────────────────


def build_redeem_calldata(permission_context: str, execution_call_data: str, mode: str) -> str:
    """redeemDelegations([context],[mode],[execution]) calldata."""
    args = abi_encode(
        ["bytes[]", "bytes32[]", "bytes[]"],
        [[_b(permission_context)], [_b(mode)], [_b(execution_call_data)]],
    )
    return SEL_REDEEM + args.hex()


def build_disable_calldata(delegation: SignedDelegation) -> str:
    """disableDelegation(Delegation) calldata (called by the delegator)."""
    args = abi_encode([_DELEGATION_TUPLE], [_delegation_tuple(delegation)])
    return SEL_DISABLE + args.hex()


def build_get_hash_calldata(delegation: SignedDelegation) -> str:
    """getDelegationHash(Delegation) calldata (a view call)."""
    args = abi_encode([_DELEGATION_TUPLE], [_delegation_tuple(delegation)])
    return SEL_GET_HASH + args.hex()


def build_disabled_calldata(delegation_hash: str) -> str:
    """disabledDelegations(bytes32) calldata (a view call)."""
    args = abi_encode(["bytes32"], [_b(delegation_hash)])
    return SEL_DISABLED + args.hex()


# ─── ERC-20 op encoders (used by executor extractors) ───────────────────


def encode_erc20_transfer(to: str, amount: int) -> str:
    """transfer(address,uint256) calldata."""
    return (
        "0xa9059cbb" + _addr_bytes(to).rjust(32, b"\x00").hex() + amount.to_bytes(32, "big").hex()
    )


def encode_erc20_approve(spender: str, amount: int) -> str:
    """approve(address,uint256) calldata."""
    return (
        "0x095ea7b3"
        + _addr_bytes(spender).rjust(32, b"\x00").hex()
        + amount.to_bytes(32, "big").hex()
    )


def encode_erc721_transfer_from(from_addr: str, to: str, token_id: int) -> str:
    """transferFrom(address,address,uint256) calldata."""
    return (
        "0x23b872dd"
        + _addr_bytes(from_addr).rjust(32, b"\x00").hex()
        + _addr_bytes(to).rjust(32, b"\x00").hex()
        + token_id.to_bytes(32, "big").hex()
    )
