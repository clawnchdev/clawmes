"""Token gating — power features unlock based on $CLAWNCH balance.

Three tiers:

  * ``free`` — no balance required. Everything you need to onboard and
    play around: ``/buy`` ``/trending`` ``/balance`` ``/leaderboard``
    ``/claim`` ``/onramp`` ``/launch`` ``/burn`` ``/agent`` single-step.
    Plus capped versions of the power features: 1 active ``/dca``
    schedule, 1 active ``/copy`` follow, 3 active ``/alerts``, no
    safeguard flags on ``/dca``.

  * ``holder`` — any wallet holding **at least 10,000,000 $CLAWNCH**
    (~$105 at session-time price). Unlocks:
      - Unlimited ``/dca`` schedules + safeguard flags (slippage,
        daily-cap, max-total, max-failures)
      - Unlimited ``/copy`` follows + advanced flags (blocklist, etc.)
      - ``/agent`` multi-step prompts (``then`` chains)
      - Unlimited ``/alerts``
      - ``/copy --pct`` percentage-based sizing

  * ``unlimited`` — any wallet holding **at least 100,000,000 $CLAWNCH**
    (~$1,050 at session-time price). "Clawmes Unlimited" — autopilot
    tier. Unlocks everything HOLDER gets, plus:
      - ``/sniper`` — auto-buy newly-launched Clawnch tokens within
        seconds of detection
      - ``/agent --ai`` — LLM fallback for unparsed prompts via
        OpenGateway. Free-form intent extraction layered on top of
        the regex parser.
      - Future: priority service tick cadence, mempool-tier ``/copy``
        latency via Alchemy WS subscribe.

The gate is reviewable in one place (:data:`HOLDER_THRESHOLD_WEI` /
:data:`UNLIMITED_THRESHOLD_WEI`) so we can adjust as the price moves
without scattering magic numbers. Tier ordering is intentional: FREE
< HOLDER < UNLIMITED. ``Tier.value`` is the rank — checks use
``tier.value >= required.value`` so adding a tier later doesn't
require rewriting every call site.

Implementation: each gated command imports
:func:`check_tier_or_error` and calls it before touching state. The
helper returns ``None`` when allowed and a human-readable error
string otherwise (with the exact balance shortfall + a hint at how
to buy more). When no wallet is connected, the user is treated as
free tier — the gate never crashes on a missing wallet, it just
returns the free-tier limit.
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Any

from clawmes.lib.logger import logger_for
from clawmes.services._base import Service

_log = logger_for("services.token_gate")

# $CLAWNCH token on Base. Pinned constant — the gate is currently
# single-token; multi-token gating (e.g. CLAWNCH-or-USDC) would land
# as a tuple/list here and a "highest tier across all" resolution.
CLAWNCH_ADDR = "0xa1F72459dfA10BAD200Ac160eCd78C6b77a747be"

# Holder threshold: 10,000,000 $CLAWNCH at 18 decimals.
# At session-time price of ~$0.0000105 / $CLAWNCH, this is ~$105 USD —
# meaningful enough to function as a real signal of commitment to the
# ecosystem, accessible enough that any serious user can clear it.
HOLDER_THRESHOLD = 10_000_000
HOLDER_THRESHOLD_WEI = HOLDER_THRESHOLD * (10**18)

# Unlimited threshold: 100,000,000 $CLAWNCH at 18 decimals.
# ~$1,050 USD at session-time price. Power-user / pro-trader tier —
# unlocks autopilot features (/sniper, /agent --ai). Meaningful jump
# from HOLDER, but still accessible to anyone who's serious about
# trading on the platform.
UNLIMITED_THRESHOLD = 100_000_000
UNLIMITED_THRESHOLD_WEI = UNLIMITED_THRESHOLD * (10**18)

# Cache TTL — balance reads hit the RPC, which is slow + rate-limited.
# A 60-second cache is fine because tier changes are rare (you bought
# 10M $CLAWNCH; you don't immediately sell 9M of it).
_CACHE_TTL_SECONDS = 60


class Tier(Enum):
    """Resolved tier for a wallet. Ordered FREE < HOLDER < UNLIMITED."""

    FREE = 0
    HOLDER = 1
    UNLIMITED = 2


# Per-command free-tier caps. Keys are command names; values are the
# maximum number of active items that command allows at the free tier.
# Free tier still gets each feature, just rate-limited; upgrading to
# HOLDER (any 10k+ balance) removes the cap.
FREE_TIER_CAPS = {
    "dca": 1,
    "copy": 1,
    "alerts": 3,
    "limit_order": 1,
}


_SERVICE: TokenGateService | None = None


class TokenGateService(Service):
    """Singleton that resolves wallet → tier via on-chain balance reads."""

    id = "clawmes.token_gate"

    def __init__(self) -> None:
        self._running = False
        # Cache key: lowercased address. Value: (tier, balance_wei, expires_at_epoch).
        self._cache: dict[str, tuple[Tier, int, float]] = {}

    def start(self) -> None:
        self._running = True
        _log.info("token gate started (threshold=%d $CLAWNCH)", HOLDER_THRESHOLD)

    def stop(self) -> None:
        self._running = False

    def health(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": "running" if self._running else "stopped",
            "holder_threshold_clawnch": HOLDER_THRESHOLD,
            "unlimited_threshold_clawnch": UNLIMITED_THRESHOLD,
            "cached_entries": len(self._cache),
        }

    def resolve_tier(self, address: str | None) -> tuple[Tier, int]:
        """Return (tier, balance_wei) for the wallet.

        No wallet → FREE with 0 balance. Cache hits return without
        touching RPC. Cache misses (or expired entries) make one
        ``balanceOf`` eth_call. Tier resolution checks UNLIMITED
        threshold first so a wallet straddling both cutoffs lands in
        the highest tier.
        """
        if not address:
            return Tier.FREE, 0
        key = address.lower()
        now = time.time()
        cached = self._cache.get(key)
        if cached and cached[2] > now:
            return cached[0], cached[1]

        balance = _read_clawnch_balance(address)
        if balance >= UNLIMITED_THRESHOLD_WEI:
            tier = Tier.UNLIMITED
        elif balance >= HOLDER_THRESHOLD_WEI:
            tier = Tier.HOLDER
        else:
            tier = Tier.FREE
        self._cache[key] = (tier, balance, now + _CACHE_TTL_SECONDS)
        return tier, balance

    def invalidate(self, address: str | None) -> None:
        """Drop cached tier for ``address`` — call after a buy/sell tx."""
        if address:
            self._cache.pop(address.lower(), None)


def _read_clawnch_balance(address: str) -> int:
    """Read the wallet's $CLAWNCH balance via ``balanceOf`` eth_call.

    Catches all errors and returns ``0`` — the gate must never crash a
    command on a network blip. A user with a transient RPC failure will
    be treated as free tier for ~60s until the cache naturally refreshes.
    """
    try:
        from clawmes.lib.abi import decode_uint, encode_balance_of
        from clawmes.services.rpc import get_rpc_service

        rpc = get_rpc_service()
        raw = rpc.eth_call(
            to=CLAWNCH_ADDR,
            data=encode_balance_of(address),
            chain_id=8453,
        )
        return decode_uint(raw)
    except Exception:  # noqa: BLE001 — gate never raises
        return 0


def get_token_gate_service() -> TokenGateService:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = TokenGateService()
    return _SERVICE


def _reset_for_tests() -> None:
    global _SERVICE
    _SERVICE = None


# ── high-level helpers used by gated command handlers ──────────────


_TIER_THRESHOLD_TOKENS = {
    Tier.HOLDER: HOLDER_THRESHOLD,
    Tier.UNLIMITED: UNLIMITED_THRESHOLD,
}

_TIER_LABELS = {
    Tier.HOLDER: "Holder",
    Tier.UNLIMITED: "Clawmes Unlimited",
}


def check_tier_or_error(min_tier: Tier, *, feature: str) -> str | None:
    """Return ``None`` if the active wallet meets ``min_tier``, else an error.

    Reads the active wallet via :func:`clawmes.services.wallet.get_wallet_state`.
    If no wallet is connected, treats the user as free tier (so the
    error message tells them what they need to unlock the feature).

    Tier comparison uses ``Tier.value`` so a wallet at a HIGHER tier
    than required passes automatically — an UNLIMITED holder calling
    a HOLDER-gated feature never gets blocked.
    """
    if min_tier == Tier.FREE:
        return None  # always allowed

    address = _active_wallet_address()
    tier, balance = get_token_gate_service().resolve_tier(address)
    if tier.value >= min_tier.value:
        return None

    required_tokens = _TIER_THRESHOLD_TOKENS[min_tier]
    tier_label = _TIER_LABELS[min_tier]
    held = balance // (10**18)
    shortfall = required_tokens - held
    lines = [
        f"{feature} requires {tier_label} tier: hold {required_tokens:,}+ $CLAWNCH.",
    ]
    if not address:
        lines.append("No wallet connected. Run /connect to read your balance.")
    else:
        lines.append(f"Active wallet holds {held:,} $CLAWNCH (need {shortfall:,} more).")
    lines.extend(
        [
            "",
            f"Buy with: /buy {CLAWNCH_ADDR} 0.01",
            "Or load up via: /onramp 1",
        ]
    )
    return "\n".join(lines)


def free_tier_cap(command: str) -> int | None:
    """Return the free-tier active-item cap for ``command``, or ``None``."""
    return FREE_TIER_CAPS.get(command)


def check_cap_or_error(command: str, *, active_count: int, feature: str) -> str | None:
    """Return ``None`` if the user is under cap or HOLDER+, else error.

    Used by ``/dca add`` / ``/copy add`` / ``/alerts add`` /
    ``/limit_order add`` to enforce per-command active-item limits at
    the free tier. HOLDER and UNLIMITED tiers have no cap.
    """
    cap = free_tier_cap(command)
    if cap is None or active_count < cap:
        return None
    address = _active_wallet_address()
    tier, _ = get_token_gate_service().resolve_tier(address)
    if tier.value >= Tier.HOLDER.value:
        return None
    return (
        f"Free tier allows {cap} active {feature}(s). "
        f"You have {active_count}. "
        f"Hold {HOLDER_THRESHOLD:,}+ $CLAWNCH to unlock unlimited."
    )


def _active_wallet_address() -> str | None:
    try:
        from clawmes.services.wallet import get_wallet_state

        state = get_wallet_state()
        if state.connected and state.address:
            return state.address
    except Exception:  # noqa: BLE001
        return None
    return None
