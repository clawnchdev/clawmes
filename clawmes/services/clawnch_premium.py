"""Clawnch premium service — on-chain tier resolution + JWT cache.

Reads the active wallet's $CLAWNCH balance and (when deployed) stakes
held in ``ClawnchStakeEscrow`` to determine which premium tier the user
qualifies for. Premium features check :meth:`has_access` before
serving; users without a qualifying stake/balance are pointed at
``/verify`` (sign-in) or ``/burn_and_call`` (one-shot pay) to proceed.

Three layers of evidence, queried in order:

  1. **Active premium JWT** — short-lived token issued by the clawn.ch
     verifier. If we hold one and it's not expired, the embedded tier
     wins. This is the fast path.
  2. **On-chain stake** — read ``getUserStakes(addr)`` against
     ``ClawnchStakeEscrow`` (when deployed). Sum the weighted stake
     (``amount × multiplierBps / 100``) and compare to thresholds.
  3. **On-chain balance** — fallback when escrow isn't deployed yet
     OR when the user holds tokens without staking. Used for the
     pre-staking-launch window.

Everything is cached for 60 seconds against (chain_id, address) to
avoid hammering the RPC on every tool call. The cache is intentionally
short — if a user just staked or burned, they shouldn't wait minutes
for the gate to reflect.

Per-call burns: when a user lacks tier but invokes a premium feature,
:meth:`request_burn_quote` returns the calldata they need to sign
(``transfer(BURN_ADDRESS, amount)``). Once the tx confirms,
:meth:`redeem_burn` exchanges the hash for a one-shot JWT via the
verifier. The one-shot is consumed on first use.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from clawmes.lib import clawnch as clawnch_const
from clawmes.lib.abi import (
    decode_uint,
    encode_address,
    encode_balance_of,
)
from clawmes.lib.logger import logger_for
from clawmes.services._base import Service

_log = logger_for("services.clawnch_premium")

#: Cache TTL for tier lookups. Short on purpose — see module docstring.
_TIER_CACHE_TTL = 60.0


class ClawnchPremiumService(Service):
    """Resolves the active wallet's premium tier from on-chain state."""

    id = "clawnch_premium"

    def __init__(self) -> None:
        self._lock = threading.RLock()
        # (chain_id, address.lower()) -> (tier, expires_at, weighted_stake_wei, balance_wei)
        self._tier_cache: dict[tuple[int, str], tuple[str, float, int, int]] = {}
        # JWT for the active session, when verified. Keyed by lowercased
        # wallet address.
        self._jwt_cache: dict[str, tuple[str, float, str]] = {}
        # One-shot tokens (burn-and-call results). Mapped to feature_id
        # so a redeem call for one feature can't unlock a different one.
        self._one_shot: dict[str, tuple[str, float]] = {}

    # ── lifecycle ───────────────────────────────────────────────────

    def start(self) -> None:
        _log.info(
            "clawnch_premium service started "
            "(token=%s, escrow=%s, pro_threshold=%d, max_threshold=%d)",
            clawnch_const.TOKEN_ADDRESS,
            clawnch_const.ESCROW_ADDRESS or "<not-deployed>",
            clawnch_const.PRO_THRESHOLD,
            clawnch_const.MAX_THRESHOLD,
        )

    def stop(self) -> None:
        with self._lock:
            self._tier_cache.clear()
            self._jwt_cache.clear()
            self._one_shot.clear()

    def health(self) -> dict[str, Any]:
        with self._lock:
            return {
                "id": self.id,
                "status": "ok",
                "cached_tiers": len(self._tier_cache),
                "active_jwts": len(self._jwt_cache),
                "one_shots": len(self._one_shot),
                "escrow_deployed": clawnch_const.ESCROW_ADDRESS is not None,
            }

    # ── tier resolution ─────────────────────────────────────────────

    def get_tier(self, address: str | None = None) -> str:
        """Return the premium tier for the given (or active) wallet.

        Resolution order: cached JWT → on-chain stake → on-chain balance.
        Returns ``"free"`` for any of: missing address, zero balance,
        RPC failure (logged but never raised — gating must be a
        non-throwing predicate so a flaky RPC doesn't block reads).
        """
        addr = self._resolve_address(address)
        if addr is None:
            return "free"

        # JWT short-circuit.
        jwt_tier = self._jwt_tier(addr)
        if jwt_tier is not None:
            return jwt_tier

        # Cache check.
        chain_id = clawnch_const.CHAIN_ID
        key = (chain_id, addr)
        now = time.monotonic()
        with self._lock:
            cached = self._tier_cache.get(key)
        if cached and cached[1] > now:
            return cached[0]

        # Read on-chain state.
        weighted_stake, balance = self._read_onchain(addr, chain_id)
        tier = self._classify(weighted_stake, balance)
        with self._lock:
            self._tier_cache[key] = (tier, now + _TIER_CACHE_TTL, weighted_stake, balance)
        return tier

    def has_access(self, feature_id: str, address: str | None = None) -> bool:
        """``True`` iff the wallet's tier meets the feature's requirement.

        Falls back to ``False`` for unknown feature IDs — the catalog
        is curated, anything missing is a bug and should not be
        granted by default.
        """
        feature = clawnch_const.FEATURES.get(feature_id)
        if feature is None:
            return False
        required = str(feature["tier"])
        if required == "free":
            return True

        # One-shot burn-and-call wins over tier.
        if self._consume_one_shot(feature_id, address):
            return True

        actual = self.get_tier(address)
        return clawnch_const.tier_at_least(actual, required)

    # ── JWT (verify-via-clawn.ch) ───────────────────────────────────

    def set_jwt(self, address: str, jwt: str, tier: str, ttl: float | None = None) -> None:
        """Cache a JWT issued by the verifier. Called by the ``/verify`` command.

        ``ttl`` defaults to :data:`clawnch.JWT_TTL_SECONDS`. Verifier
        responses include the actual ``expires_at`` so we honor the
        issuer's clock here rather than make up our own.
        """
        if not address or not jwt or tier not in clawnch_const.TIER_ORDER:
            return
        addr = address.lower()
        ttl_s = float(ttl) if ttl is not None else float(clawnch_const.JWT_TTL_SECONDS)
        expires = time.monotonic() + ttl_s
        with self._lock:
            self._jwt_cache[addr] = (jwt, expires, tier)
        # Bust the tier cache for this address so the next lookup uses
        # the JWT immediately, not the older stake/balance read.
        self._invalidate(addr)

    def get_jwt(self, address: str | None = None) -> str | None:
        """Return the active JWT for the wallet, if any and unexpired."""
        addr = self._resolve_address(address)
        if addr is None:
            return None
        with self._lock:
            entry = self._jwt_cache.get(addr)
        if entry is None:
            return None
        jwt, expires, _ = entry
        if expires <= time.monotonic():
            self._evict_jwt(addr)
            return None
        return jwt

    # ── per-call burn ───────────────────────────────────────────────

    def request_burn_quote(self, feature_id: str) -> dict[str, Any]:
        """Build a burn quote (calldata + cost) for a one-shot premium call.

        Returns a dict with:
          * ``feature_id`` / ``label`` — what the user is paying for.
          * ``required_tier`` — the tier that would otherwise unlock this.
          * ``cost_clawnch`` — whole-token CLAWNCH cost.
          * ``cost_wei`` — same in 18-decimal units.
          * ``burn_address`` — destination (always the canonical dead
            address; verifiable on-chain).
          * ``token`` — CLAWNCH ERC-20 contract.
          * ``calldata`` — pre-built ``transfer(BURN_ADDRESS, amount)``
            payload the wallet signs.
          * ``unsupported`` — present + ``True`` when the feature
            has no published burn price (caller should fall back to
            "stake to unlock" messaging).
        """
        feature = clawnch_const.FEATURES.get(feature_id)
        if feature is None:
            return {"error": "unknown_feature", "feature_id": feature_id}
        cost = clawnch_const.burn_price(feature_id)
        if cost is None:
            return {
                "feature_id": feature_id,
                "label": feature["label"],
                "required_tier": feature["tier"],
                "unsupported": True,
            }
        cost_wei = clawnch_const.to_wei(cost)
        # transfer(burn_address, cost_wei) — uses the same ERC-20
        # encoder as the rest of clawmes. The ``SELECTOR_TRANSFER``
        # constant already carries the ``0x`` prefix; the address /
        # uint helpers return prefix-less hex, so the concatenation
        # produces a single ``0x``-prefixed calldata payload.
        from clawmes.lib.abi import SELECTOR_TRANSFER, encode_uint

        calldata = (
            SELECTOR_TRANSFER + encode_address(clawnch_const.BURN_ADDRESS) + encode_uint(cost_wei)
        )
        return {
            "feature_id": feature_id,
            "label": feature["label"],
            "required_tier": feature["tier"],
            "cost_clawnch": cost,
            "cost_wei": cost_wei,
            "burn_address": clawnch_const.BURN_ADDRESS,
            "token": clawnch_const.TOKEN_ADDRESS,
            "calldata": calldata,
        }

    def redeem_burn(self, feature_id: str, tx_hash: str, address: str | None = None) -> bool:
        """Record a confirmed burn → grant one-shot access to the feature.

        Verification of the on-chain burn happens server-side at the
        clawn.ch verifier (we ship the tx hash, the verifier confirms
        the burn amount + destination + age, and returns a one-shot
        token). The service exposes this method as the post-verifier
        wire-in: pass the issued token and the feature it's scoped to,
        and a single :meth:`has_access` call for that feature returns
        ``True``.

        Returns ``True`` if the one-shot was accepted; ``False`` on
        empty input. We don't re-validate the hash here — that's the
        verifier's job — but we do require non-empty hash + a known
        feature to avoid storing junk.
        """
        if not tx_hash or feature_id not in clawnch_const.FEATURES:
            return False
        addr = self._resolve_address(address) or "default"
        key = self._one_shot_key(feature_id, addr)
        # One-shot has a 5-minute window — long enough for clock skew
        # between the verifier and the local clock, short enough to
        # keep the redeem path from being a long-lived auth surface.
        with self._lock:
            self._one_shot[key] = (tx_hash, time.monotonic() + 300.0)
        return True

    # ── internals ───────────────────────────────────────────────────

    def _read_onchain(self, address: str, chain_id: int) -> tuple[int, int]:
        """Return ``(weighted_stake_wei, balance_wei)`` from RPC."""
        try:
            from clawmes.services.rpc import get_rpc_service

            rpc = get_rpc_service()
        except Exception:  # noqa: BLE001 — rpc unavailable = no premium
            _log.exception("rpc service unavailable; defaulting to free tier")
            return (0, 0)

        balance = self._read_balance(rpc, address, chain_id)
        weighted = self._read_weighted_stake(rpc, address, chain_id)
        return (weighted, balance)

    @staticmethod
    def _read_balance(rpc: Any, address: str, chain_id: int) -> int:
        try:
            raw = rpc.eth_call(
                to=clawnch_const.TOKEN_ADDRESS,
                data=encode_balance_of(address),
                chain_id=chain_id,
            )
            return decode_uint(raw)
        except Exception:  # noqa: BLE001 — RPC flakes shouldn't lock users out
            _log.exception("balanceOf read failed for %s", address)
            return 0

    @staticmethod
    def _read_weighted_stake(rpc: Any, address: str, chain_id: int) -> int:
        """Sum the weighted stake across all of ``address``'s stakes.

        Returns 0 when the escrow contract isn't deployed yet — that's
        the pre-launch window where premium falls back to balance.
        Also returns 0 on any RPC failure or decode mismatch.
        """
        escrow = clawnch_const.ESCROW_ADDRESS
        if escrow is None:
            return 0
        try:
            # 1. getUserStakes(address) -> uint256[]
            data = clawnch_const.SELECTOR_USER_STAKES + encode_address(address)
            stake_ids_raw = rpc.eth_call(to=escrow, data=data, chain_id=chain_id)
            stake_ids = ClawnchPremiumService._decode_uint_array(stake_ids_raw)
        except Exception:  # noqa: BLE001
            _log.exception("getUserStakes failed for %s", address)
            return 0
        if not stake_ids:
            return 0
        weighted = 0
        for stake_id in stake_ids:
            try:
                amount, multiplier_bps = ClawnchPremiumService._read_stake(
                    rpc, escrow, stake_id, chain_id
                )
            except Exception:  # noqa: BLE001
                _log.exception("stakes(%d) read failed", stake_id)
                continue
            if amount == 0:
                continue
            # multiplier_bps is 100 = 1x, 200 = 2x, etc.
            weighted += (amount * multiplier_bps) // 100
        return weighted

    @staticmethod
    def _read_stake(rpc: Any, escrow: str, stake_id: int, chain_id: int) -> tuple[int, int]:
        """Return ``(amount_wei, multiplier_bps)`` for a single stake.

        The Solidity ``stakes(uint256)`` accessor returns 5 fields:
        ``(address user, uint96 amount, uint8 tierIndex, uint32 stakedAt,
        uint16 multiplierBps)`` — packed into a tuple of 32-byte slots
        in the standard order.
        """
        from clawmes.lib.abi import encode_uint

        data = clawnch_const.SELECTOR_STAKES + encode_uint(stake_id)
        raw = rpc.eth_call(to=escrow, data=data, chain_id=chain_id)
        cleaned = raw.removeprefix("0x") if isinstance(raw, str) else ""
        if len(cleaned) < 320:  # 5 fields × 64 hex chars
            return (0, 0)
        # Field 1 (user) — slot 0; not needed.
        # Field 2 (amount, uint96) — slot 1; right-aligned in 32 bytes.
        amount = int(cleaned[64:128], 16)
        # Field 3 (tierIndex, uint8) — slot 2; not needed (multiplier
        # snapshot is in slot 4).
        # Field 4 (stakedAt, uint32) — slot 3; not needed.
        # Field 5 (multiplierBps, uint16) — slot 4.
        multiplier_bps = int(cleaned[256:320], 16)
        # An active stake records its amount inline; a withdrawn stake
        # has amount=0 and we skip it via the caller's check.
        return (amount, multiplier_bps)

    @staticmethod
    def _decode_uint_array(raw: str) -> list[int]:
        """Decode a Solidity ``uint256[]`` return into a list of ints.

        ABI layout: ``[offset (32B)] [length (32B)] [item_0] [item_1] ...``
        We're tolerant — empty / malformed input returns ``[]`` rather
        than raising, because the caller is treating zero-stake as the
        default outcome anyway.
        """
        cleaned = raw.removeprefix("0x") if isinstance(raw, str) else ""
        if len(cleaned) < 128:  # at minimum need offset + length slots
            return []
        try:
            length = int(cleaned[64:128], 16)
        except ValueError:
            return []
        items: list[int] = []
        for i in range(length):
            start = 128 + i * 64
            end = start + 64
            if end > len(cleaned):
                break
            try:
                items.append(int(cleaned[start:end], 16))
            except ValueError:
                break
        return items

    def _classify(self, weighted_stake_wei: int, balance_wei: int) -> str:
        """Map (stake, balance) onto a tier.

        Pre-escrow-launch fallback: when ``weighted_stake_wei`` is 0
        because the escrow isn't deployed, balance acts as the gate —
        less durable than stake but lets us ship pricing before the
        full staking system goes live.
        """
        pro_wei = clawnch_const.to_wei(clawnch_const.PRO_THRESHOLD)
        max_wei = clawnch_const.to_wei(clawnch_const.MAX_THRESHOLD)
        effective = max(weighted_stake_wei, balance_wei)
        if effective >= max_wei:
            return "max"
        if effective >= pro_wei:
            return "pro"
        return "free"

    def _jwt_tier(self, address: str) -> str | None:
        with self._lock:
            entry = self._jwt_cache.get(address)
        if entry is None:
            return None
        _, expires, tier = entry
        if expires <= time.monotonic():
            self._evict_jwt(address)
            return None
        return tier

    def _evict_jwt(self, address: str) -> None:
        with self._lock:
            self._jwt_cache.pop(address, None)

    def _invalidate(self, address: str) -> None:
        with self._lock:
            for key in list(self._tier_cache):
                if key[1] == address:
                    del self._tier_cache[key]

    def _consume_one_shot(self, feature_id: str, address: str | None) -> bool:
        addr = self._resolve_address(address) or "default"
        key = self._one_shot_key(feature_id, addr)
        with self._lock:
            entry = self._one_shot.pop(key, None)
        if entry is None:
            return False
        _, expires = entry
        return expires > time.monotonic()

    @staticmethod
    def _one_shot_key(feature_id: str, address: str) -> str:
        return f"{feature_id}@{address}"

    @staticmethod
    def _resolve_address(address: str | None) -> str | None:
        if address:
            return address.lower()
        try:
            from clawmes.services.wallet import get_wallet_state

            state = get_wallet_state()
        except Exception:  # noqa: BLE001 — wallet not initialized
            return None
        if state.address is None:
            return None
        return state.address.lower()


_instance: ClawnchPremiumService | None = None


def get_clawnch_premium_service() -> ClawnchPremiumService:
    """Singleton accessor for the premium service."""
    global _instance
    if _instance is None:
        _instance = ClawnchPremiumService()
    return _instance
