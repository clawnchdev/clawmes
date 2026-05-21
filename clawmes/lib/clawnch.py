"""$CLAWNCH token + staking-escrow constants and helpers.

Centralizes the on-chain coordinates of the Clawnch ecosystem so the
premium service, decorator, and commands don't repeat magic strings.

The token + escrow live on Base mainnet. The escrow is the
``ClawnchStakeEscrow.sol`` contract from the clawnch monorepo
(``contracts/src/clawncher/ClawnchStakeEscrow.sol``) — until that
deploys, this module exposes ``ESCROW_ADDRESS = None`` and the
premium service degrades to a balance-only check.

Tier resolution:

  * ``free`` — no qualifying stake or balance.
  * ``pro``  — weighted-stake (or fallback balance) ≥ ``PRO_THRESHOLD``.
  * ``max``  — weighted-stake (or fallback balance) ≥ ``MAX_THRESHOLD``.

Weighted stake mirrors the WETH-yield distribution formula from the
staking PRD: ``sum(stake.amount × stake.multiplierBps / 100)``. A
Bronze-tier (1x) 10M stake counts as 10M; a Diamond (8x) 10M stake
counts as 80M. Same stake powers both WETH yield and clawmes
premium — one deposit, two benefits.

Threshold defaults are tuned for the launch baseline (~$10.5M mcap
=> ~$0.000105 / CLAWNCH). 10M weighted ≈ $1,050 entry for Pro, 50M
weighted ≈ $5,250 entry for Max. Operators can override via env
(``CLAWNCH_PREMIUM_PRO_THRESHOLD`` / ``CLAWNCH_PREMIUM_MAX_THRESHOLD``)
once price / supply distribution shifts.
"""

from __future__ import annotations

import os
from typing import Final

# ──────────────────────────────────────────────────────────────────────
#  Contract addresses (Base mainnet, chain id 8453)
# ──────────────────────────────────────────────────────────────────────

#: $CLAWNCH ERC-20 on Base. Sourced from staking-prd.md / canonical
#: deployment. Lowercase — the rest of clawmes also lowercases addresses
#: for stable comparisons.
TOKEN_ADDRESS: Final[str] = "0xa1f72459dfa10bad200ac160ecd78c6b77a747be"

#: ``ClawnchStakeEscrow`` deployment. ``None`` until the contract goes
#: live on mainnet — premium service falls back to balance-only checks
#: until ``CLAWNCH_STAKE_ESCROW`` is exported or this constant is updated.
ESCROW_ADDRESS: Final[str | None] = None

#: Standard "send-to-dead" sink — every CLAWNCH burned in clawmes-side
#: per-call payments lands here. Verifiable on-chain via the ERC-20
#: ``Transfer(_, BURN_ADDRESS, value)`` event.
BURN_ADDRESS: Final[str] = "0x000000000000000000000000000000000000dead"

#: Base mainnet chain id — used to pin RPC reads to the correct network.
CHAIN_ID: Final[int] = 8453

# ──────────────────────────────────────────────────────────────────────
#  Tier thresholds (CLAWNCH whole units, before 18-decimal scaling)
# ──────────────────────────────────────────────────────────────────────


def _env_int(name: str, default: int) -> int:
    """Read an integer threshold from env, falling back to ``default``.

    Operators tune thresholds without redeploying the plugin by exporting
    ``CLAWNCH_PREMIUM_PRO_THRESHOLD`` / ``CLAWNCH_PREMIUM_MAX_THRESHOLD``.
    Garbage values (non-integer, negative) silently fall back so a
    malformed env never bricks the plugin.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    if value < 0:
        return default
    return value


#: Pro tier entry. Weighted-stake (or balance fallback) ≥ this value
#: unlocks Pro features — premium tools, faster RPC, OpenGateway high-tier
#: inference quota, multi-wallet.
PRO_THRESHOLD: int = _env_int("CLAWNCH_PREMIUM_PRO_THRESHOLD", 10_000_000)

#: Max tier entry. Adds BV-7X premium passthrough, clawmes-issued EAS
#: attestation writes, sub-agent / A2A capability, early-access tools,
#: private Discord.
MAX_THRESHOLD: int = _env_int("CLAWNCH_PREMIUM_MAX_THRESHOLD", 50_000_000)

#: ERC-20 decimals for CLAWNCH. All on-chain reads return integer wei;
#: thresholds above are expressed in whole tokens for readability.
DECIMALS: Final[int] = 18


def to_wei(amount: int) -> int:
    """Scale a whole-token threshold into 18-decimal wei units.

    ``to_wei(10_000_000)`` -> ``10_000_000 * 10**18``. Used by the
    premium service to compare against raw RPC return values without
    floating-point.
    """
    return amount * (10**DECIMALS)


# ──────────────────────────────────────────────────────────────────────
#  ClawnchStakeEscrow ABI fragments
# ──────────────────────────────────────────────────────────────────────

# Selectors derived from ``keccak256("<sig>")[:4]``. Hard-coded so the
# premium service doesn't have to round-trip through web3.py just to
# build calldata for two view functions.

#: ``getUserStakeCount(address) returns (uint256)``
SELECTOR_USER_STAKE_COUNT: Final[str] = "0x68424b1a"

#: ``getUserStakes(address) returns (uint256[])``
SELECTOR_USER_STAKES: Final[str] = "0x6859fe39"

#: ``stakes(uint256) returns (address user, uint96 amount, uint8 tierIndex,
#: uint32 stakedAt, uint16 multiplierBps)``
SELECTOR_STAKES: Final[str] = "0xa4beda63"

#: ``totalStaked() returns (uint256)``
SELECTOR_TOTAL_STAKED: Final[str] = "0x817b1cd2"


# ──────────────────────────────────────────────────────────────────────
#  Per-call burn pricing (whole-token amounts)
# ──────────────────────────────────────────────────────────────────────

#: Default per-call burn cost for each premium feature, in whole CLAWNCH.
#: Tuned at ~$0.000105/CLAWNCH to land in the $5-$50 range per call —
#: high enough to make Pro tier feel cheap by comparison, low enough
#: that an occasional power-user can still afford a one-shot.
#:
#: Operators override via ``CLAWNCH_BURN_PRICE_<FEATURE_ID>`` env vars.
DEFAULT_BURN_PRICES: Final[dict[str, int]] = {
    "bv7x_oracle_premium": 100_000,  # ~$10
    "opengateway_high_tier": 50_000,  # ~$5
    "eas_attestation_write": 200_000,  # ~$20
    "premium_rpc_hour": 500_000,  # ~$50
    "premium_bridge_routing": 50_000,  # ~$5
}


def burn_price(feature_id: str) -> int | None:
    """Return the per-call burn price (whole CLAWNCH) for a feature.

    Reads ``CLAWNCH_BURN_PRICE_<FEATURE_ID>`` env override first, then
    falls back to :data:`DEFAULT_BURN_PRICES`. Returns ``None`` for
    unknown features — callers must explicitly handle "no quote
    available" rather than charging a default.
    """
    env_name = f"CLAWNCH_BURN_PRICE_{feature_id.upper()}"
    if (raw := os.environ.get(env_name)) is not None:
        try:
            value = int(raw)
        except ValueError:
            return DEFAULT_BURN_PRICES.get(feature_id)
        if value < 0:
            return DEFAULT_BURN_PRICES.get(feature_id)
        return value
    return DEFAULT_BURN_PRICES.get(feature_id)


# ──────────────────────────────────────────────────────────────────────
#  Verifier endpoint (clawn.ch)
# ──────────────────────────────────────────────────────────────────────

#: Base URL of the clawn.ch verifier API. Override via
#: ``CLAWNCH_VERIFIER_URL`` to point at staging / local dev. The trailing
#: slash is intentional — the service appends ``clawmes/verify`` etc.
VERIFIER_URL: Final[str] = os.environ.get(
    "CLAWNCH_VERIFIER_URL",
    "https://clawn.ch/api/",
)

#: JWT cache TTL in seconds. Matches the issuer's default; we re-verify
#: at least this often so a withdraw-then-spam flow can't keep premium
#: open for days.
JWT_TTL_SECONDS: Final[int] = 24 * 60 * 60


# ──────────────────────────────────────────────────────────────────────
#  Feature → required-tier mapping
# ──────────────────────────────────────────────────────────────────────

#: Static feature catalog — adding a premium-gated op means appending
#: a row here, then decorating the implementation with
#: ``@premium_feature(feature_id=...)``. Tests assert that every
#: decorated feature is listed here and vice versa.
FEATURES: Final[dict[str, dict[str, str | int]]] = {
    "bv7x_oracle_premium": {
        "tier": "pro",
        "label": "BV-7X premium oracle (signal + attestation lineage)",
    },
    "opengateway_high_tier": {
        "tier": "pro",
        "label": "OpenGateway high-tier LLM inference",
    },
    "eas_attestation_write": {
        "tier": "max",
        "label": "Clawmes-issued EAS attestation (on-chain write)",
    },
    "premium_rpc_hour": {
        "tier": "pro",
        "label": "Premium RPC tier (1-hour session)",
    },
    "premium_bridge_routing": {
        "tier": "pro",
        "label": "Priority bridge routing (LiFi negotiated spreads)",
    },
}

#: Tier ordering — used by ``tier_at_least``. Higher index = higher tier.
TIER_ORDER: Final[tuple[str, ...]] = ("free", "pro", "max")


def tier_at_least(actual: str, required: str) -> bool:
    """``True`` iff ``actual`` is at the same tier or higher than ``required``.

    Unknown tier strings (typos, future tiers we don't recognize yet)
    are conservatively treated as ``"free"`` — better to deny than to
    silently grant access on a misconfiguration.
    """
    actual_idx = TIER_ORDER.index(actual) if actual in TIER_ORDER else 0
    required_idx = TIER_ORDER.index(required) if required in TIER_ORDER else 0
    return actual_idx >= required_idx
