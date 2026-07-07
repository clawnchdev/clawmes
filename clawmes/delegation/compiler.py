"""Compile a :class:`DelegationSpec` into on-chain caveats.

A :class:`DelegationSpec` is the human-facing description of the limits a
delegation should enforce — richer than a :class:`clawmes.policy.types.Policy`
because on-chain enforcers support dimensions the app-layer gate doesn't
(spend budgets that reset per period, allowed-target lists, absolute expiry).

:func:`compile_spec` turns a spec into an :class:`UnsignedDelegation` whose
``caveats`` are the ABI-encoded enforcer terms. :func:`spec_from_policy` seeds
a spec from an existing named policy so ``/delegate create <policy>`` works
without re-typing limits.

Design note vs. the openclawnch reference: clawmes policy amounts are already
in wei, so there is **no USD→ETH price oracle** in the compile path — the
numbers are exact.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

from clawmes.delegation import encoding as E
from clawmes.delegation.types import (
    ALLOWED_TARGETS_ENFORCER,
    DEFAULT_CHAIN_ID,
    ERC20_PERIOD_TRANSFER_ENFORCER,
    ERC20_TRANSFER_AMOUNT_ENFORCER,
    LIMITED_CALLS_ENFORCER,
    NATIVE_TOKEN_PERIOD_TRANSFER_ENFORCER,
    NATIVE_TOKEN_TRANSFER_AMOUNT_ENFORCER,
    PERIOD_SECONDS,
    ROOT_AUTHORITY,
    TIMESTAMP_ENFORCER,
    VALUE_LTE_ENFORCER,
    Caveat,
    CompiledDelegation,
    UnsignedDelegation,
    enforcer_name,
    is_supported_chain,
)


class CompileError(ValueError):
    """Raised when a spec can't be compiled (bad chain, no restrictions)."""


@dataclass
class Erc20Limit:
    """An ERC-20 spend cap for one token. ``period_seconds=0`` → lifetime cap."""

    token: str
    max_amount: int  # base units (already scaled by token decimals)
    period_seconds: int = 0


@dataclass
class DelegationSpec:
    """Everything needed to compile a delegation's caveats.

    Every field is optional; at least one restriction must be present unless
    ``allow_unrestricted`` is set (which is refused by default — an
    unrestricted delegation hands the agent the whole account).
    """

    #: Per-call native (ETH) value cap in wei → ValueLteEnforcer.
    native_per_call_wei: int | None = None
    #: Native spend budget in wei → NativeToken(Period|TransferAmount)Enforcer.
    native_cap_wei: int | None = None
    #: Reset period (seconds) for the native budget. 0 → lifetime cap.
    native_period_seconds: int = 0
    #: ERC-20 spend caps, one per token.
    erc20: list[Erc20Limit] = field(default_factory=list)
    #: Lifetime cap on number of redemptions → LimitedCallsEnforcer.
    max_calls: int | None = None
    #: If set with ``max_calls``, bounds the call window → TimestampEnforcer.
    calls_window_seconds: int = 0
    #: Restrict which target contracts may be called → AllowedTargetsEnforcer.
    allowed_targets: list[str] = field(default_factory=list)
    #: Absolute delegation lifetime (seconds from now) → TimestampEnforcer.
    expiry_seconds: int = 0
    #: Escape hatch: permit a delegation with zero caveats.
    allow_unrestricted: bool = False


def compile_spec(
    spec: DelegationSpec,
    *,
    delegate: str,
    delegator: str,
    chain_id: int = DEFAULT_CHAIN_ID,
    salt: int | None = None,
    now_ts: int | None = None,
) -> CompiledDelegation:
    """Compile ``spec`` into an :class:`UnsignedDelegation`.

    Raises :class:`CompileError` for an unsupported chain or when the spec
    produces no caveats and ``allow_unrestricted`` is False.
    """
    if not is_supported_chain(chain_id):
        raise CompileError(f"chain {chain_id} is not supported by the Delegation Framework")

    now = int(now_ts if now_ts is not None else time.time())
    caveats: list[Caveat] = []
    mapped: list[str] = []
    warnings: list[str] = []
    expiry_befores: list[int] = []

    if spec.native_per_call_wei is not None:
        if spec.native_per_call_wei < 0:
            raise CompileError("native_per_call_wei must be non-negative")
        caveats.append(Caveat(VALUE_LTE_ENFORCER, E.terms_value_lte(spec.native_per_call_wei)))
        mapped.append(f"native per-call ≤ {spec.native_per_call_wei} wei → ValueLteEnforcer")

    if spec.native_cap_wei is not None:
        if spec.native_cap_wei <= 0:
            raise CompileError("native_cap_wei must be positive")
        if spec.native_period_seconds > 0:
            caveats.append(
                Caveat(
                    NATIVE_TOKEN_PERIOD_TRANSFER_ENFORCER,
                    E.terms_native_period(spec.native_cap_wei, 0, spec.native_period_seconds),
                )
            )
            mapped.append(
                f"native ≤ {spec.native_cap_wei} wei / {spec.native_period_seconds}s "
                "→ NativeTokenPeriodTransferEnforcer"
            )
        else:
            caveats.append(
                Caveat(
                    NATIVE_TOKEN_TRANSFER_AMOUNT_ENFORCER,
                    E.terms_native_transfer_amount(spec.native_cap_wei),
                )
            )
            mapped.append(
                f"native lifetime ≤ {spec.native_cap_wei} wei → NativeTokenTransferAmountEnforcer"
            )
            warnings.append(
                "native_cap_wei has no period → lifetime cap; the delegation "
                "stops working once cumulative spend hits the cap. Add a period "
                "for a budget that resets."
            )

    for limit in spec.erc20:
        if limit.max_amount <= 0:
            raise CompileError(f"erc20 cap for {limit.token} must be positive")
        if limit.period_seconds > 0:
            caveats.append(
                Caveat(
                    ERC20_PERIOD_TRANSFER_ENFORCER,
                    E.terms_erc20_period(limit.token, limit.max_amount, 0, limit.period_seconds),
                )
            )
            mapped.append(
                f"{limit.token} ≤ {limit.max_amount} / {limit.period_seconds}s "
                "→ ERC20PeriodTransferEnforcer"
            )
        else:
            caveats.append(
                Caveat(
                    ERC20_TRANSFER_AMOUNT_ENFORCER,
                    E.terms_erc20_transfer_amount(limit.token, limit.max_amount),
                )
            )
            mapped.append(
                f"{limit.token} lifetime ≤ {limit.max_amount} → ERC20TransferAmountEnforcer"
            )

    if spec.max_calls is not None:
        if spec.max_calls <= 0:
            raise CompileError("max_calls must be positive")
        caveats.append(Caveat(LIMITED_CALLS_ENFORCER, E.terms_limited_calls(spec.max_calls)))
        mapped.append(f"≤ {spec.max_calls} total calls → LimitedCallsEnforcer")
        if spec.calls_window_seconds > 0:
            before = now + spec.calls_window_seconds
            caveats.append(Caveat(TIMESTAMP_ENFORCER, E.terms_timestamp(now, before)))
            expiry_befores.append(before)
            mapped.append(f"call window {spec.calls_window_seconds}s → TimestampEnforcer")
            warnings.append(
                "rate limit uses a lifetime call cap + a time window; the "
                "delegation expires at the end of the window — create a new one "
                "for the next window."
            )

    if spec.allowed_targets:
        bad = [t for t in spec.allowed_targets if not _is_address(t)]
        if bad:
            raise CompileError(f"allowed_targets contains non-addresses: {bad}")
        caveats.append(
            Caveat(ALLOWED_TARGETS_ENFORCER, E.terms_allowed_targets(spec.allowed_targets))
        )
        mapped.append(
            f"targets restricted to {len(spec.allowed_targets)} address(es) "
            "→ AllowedTargetsEnforcer"
        )

    if spec.expiry_seconds > 0:
        before = now + spec.expiry_seconds
        caveats.append(Caveat(TIMESTAMP_ENFORCER, E.terms_timestamp(0, before)))
        expiry_befores.append(before)
        mapped.append(f"expires in {spec.expiry_seconds}s → TimestampEnforcer")

    if not caveats and not spec.allow_unrestricted:
        raise CompileError(
            "refusing to build an unrestricted delegation — it would grant the "
            "agent unlimited access to the delegator account. Add at least one "
            "limit (per-call cap, budget, target allowlist, expiry, or call cap)."
        )

    if salt is None:
        salt = _salt_from(delegate, delegator, now)

    unsigned = UnsignedDelegation(
        delegate=delegate,
        delegator=delegator,
        authority=ROOT_AUTHORITY,
        caveats=tuple(caveats),
        salt=salt,
    )

    expires_at = ""
    if expiry_befores:
        expires_at = datetime.fromtimestamp(min(expiry_befores), tz=UTC).isoformat()

    return CompiledDelegation(
        delegation=unsigned,
        mapped=mapped,
        unmapped=[],
        warnings=warnings,
        expires_at=expires_at,
    )


def spec_from_policy(policy, *, expiry_seconds: int = 0) -> tuple[DelegationSpec, list[str]]:
    """Seed a :class:`DelegationSpec` from a named policy.

    Maps the policy's quantitative gates to their exact on-chain equivalents:

      * ``max_amount_wei`` → ``native_per_call_wei`` (ValueLteEnforcer) — a
        per-action value cap, the precise semantic of the app-layer gate.
      * ``max_per_hour``   → ``max_calls`` + a 1-hour ``calls_window_seconds``
        (LimitedCalls + Timestamp). Flagged in the returned notes because the
        on-chain form expires after the window.

    Returns ``(spec, notes)`` where ``notes`` explains any approximations /
    unmapped fields for the user.
    """
    notes: list[str] = []
    spec = DelegationSpec(expiry_seconds=expiry_seconds)

    if policy.max_amount_wei is not None:
        spec.native_per_call_wei = policy.max_amount_wei
    if policy.max_per_hour is not None:
        spec.max_calls = policy.max_per_hour
        spec.calls_window_seconds = PERIOD_SECONDS["hourly"]
        notes.append(
            "max_per_hour mapped to an on-chain call cap over a 1-hour window; "
            "the delegation expires after that hour."
        )
    if not policy.chain_ids:
        notes.append("policy is chain-agnostic; the delegation targets one chain only.")

    return spec, notes


def format_compilation(compiled: CompiledDelegation, chain_id: int) -> str:
    """Human-readable summary of a compiled delegation for user review."""
    from clawmes.delegation.types import chain_name

    lines = ["Delegation preview", ""]
    lines.append(f"  Chain:     {chain_name(chain_id)} ({chain_id})")
    lines.append(f"  Delegate:  {compiled.delegation.delegate}")
    lines.append(f"  Delegator: {compiled.delegation.delegator}")
    lines.append(f"  Caveats:   {len(compiled.delegation.caveats)}")
    if compiled.mapped:
        lines.append("")
        lines.append("  On-chain enforced:")
        for m in compiled.mapped:
            lines.append(f"    • {m}")
    if compiled.expires_at:
        lines.append(f"  Expires:   {compiled.expires_at}")
    if compiled.warnings:
        lines.append("")
        lines.append("  Warnings:")
        for w in compiled.warnings:
            lines.append(f"    ! {w}")
    return "\n".join(lines)


def enforcers_in(compiled: CompiledDelegation) -> list[str]:
    """List enforcer names used, for display/testing."""
    return [enforcer_name(c.enforcer) for c in compiled.delegation.caveats]


def _is_address(value: str) -> bool:
    if not isinstance(value, str) or not value.startswith("0x") or len(value) != 42:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def _salt_from(delegate: str, delegator: str, now: int) -> int:
    from eth_utils import keccak

    digest = keccak(text=f"{delegate.lower()}:{delegator.lower()}:{now}")
    return int.from_bytes(digest, "big")
