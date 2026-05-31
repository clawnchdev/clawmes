"""``/mev-protect`` — global MEV protection for swap routing.

When enabled, swap transactions are routed through a privacy-preserving
RPC endpoint (Flashbots Protect on mainnet, or the Base equivalent
when available) instead of the public mempool. This prevents
sandwich attacks: the tx is bundled directly with miners/validators
rather than sitting in the mempool where front-runners can race it.

How it works in clawmes:

  * State lives in ``${HERMES_HOME}/clawmes/mev_protect/state.json``.
  * The active setting is a per-sender boolean. Default off.
  * When on, the ``defi_swap`` tool consults
    :func:`get_protected_rpc_url` and submits via that endpoint
    instead of the default RPC. Swaps on other chains where no
    privacy RPC is registered fall back to default with a warning.
  * No other transactions are affected. Token transfers, deploys,
    and burns still go through the default mempool because they
    aren't sandwich-attackable.

Surface:

  * ``/mev-protect on``      enable for this sender
  * ``/mev-protect off``     disable
  * ``/mev-protect status``  show current setting + active endpoint

Tier: HOLDER (any wallet with 10M+ $CLAWNCH). It's a quality-of-life
trader feature, not an autopilot feature — the user still initiates
every swap; the only thing changing is the submission path.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Flashbots Protect endpoint URLs by chain id. None means no
# privacy RPC is registered for that chain; swaps fall back.
_PROTECTED_RPCS: dict[int, str | None] = {
    1: "https://rpc.flashbots.net/fast",  # Ethereum mainnet
    8453: None,  # Base — no Flashbots Protect equivalent at session time
}


def _state_path() -> Path:
    from clawmes.lib.paths import state_dir

    return state_dir("mev_protect") / "state.json"


def _load_state() -> dict[str, bool]:
    path = _state_path()
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    # Coerce all values to bool defensively. JSON keys are always strings
    # on load, so we don't filter them.
    return {k: bool(v) for k, v in data.items()}


def _save_state(state: dict[str, bool]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)


def _record(name: str, args: str, result: str) -> None:
    try:
        from clawmes.services.command_history import record_command_call

        record_command_call(name, args, result)
    except Exception:  # noqa: BLE001
        pass


# ── public accessors (consumed by defi_swap) ───────────────────────


def is_enabled(sender_id: str) -> bool:
    """Return True if ``sender_id`` has MEV protection enabled."""
    return bool(_load_state().get(sender_id, False))


def get_protected_rpc_url(chain_id: int) -> str | None:
    """Return the privacy RPC URL for ``chain_id``, or None if not registered."""
    return _PROTECTED_RPCS.get(chain_id)


# ── command dispatch ───────────────────────────────────────────────


async def handle_mev_protect(raw_args: str, *, sender_id: str = "default", **_kwargs: Any) -> str:
    raw = (raw_args or "").strip().lower()

    # HOLDER tier gate. The toggle protects user value; we don't want
    # to leave it open to free users so that the tier story stays clean.
    from clawmes.services.token_gate import Tier, check_tier_or_error

    gate_err = check_tier_or_error(Tier.HOLDER, feature="/mev-protect")
    if gate_err:
        return gate_err

    if not raw or raw == "status":
        out = _cmd_status(sender_id)
    elif raw == "on":
        out = _cmd_on(sender_id)
    elif raw == "off":
        out = _cmd_off(sender_id)
    else:
        out = f"Unknown subcommand: {raw!r}\n\nUsage: /mev-protect on | off | status"
    _record("mev-protect", raw_args, out)
    return out


def _cmd_on(sender_id: str) -> str:
    state = _load_state()
    state[sender_id] = True
    _save_state(state)
    return (
        "MEV protection enabled.\n"
        "\n"
        "Swap transactions will route through privacy-preserving RPCs when\n"
        "available. Sandwich attacks become impossible on chains with a\n"
        "registered privacy endpoint.\n"
        "\n"
        "Use /mev-protect status to see active endpoints. Other transactions\n"
        "(transfers, deploys, burns) are unaffected — they don't need\n"
        "protection."
    )


def _cmd_off(sender_id: str) -> str:
    state = _load_state()
    if sender_id in state:
        del state[sender_id]
        _save_state(state)
    return "MEV protection disabled. Swaps will use the default RPC mempool."


def _cmd_status(sender_id: str) -> str:
    enabled = is_enabled(sender_id)
    lines = [
        f"MEV protection status for {sender_id}: {'ENABLED' if enabled else 'disabled'}",
        "",
        "Registered privacy RPCs by chain:",
    ]
    for chain_id, url in sorted(_PROTECTED_RPCS.items()):
        if url is None:
            lines.append(f"  chain {chain_id}: (none registered)")
        else:
            lines.append(f"  chain {chain_id}: {url}")
    lines.append("")
    if enabled:
        lines.append("Use /mev-protect off to disable.")
    else:
        lines.append("Use /mev-protect on to enable.")
    return "\n".join(lines)


def register(ctx) -> None:
    ctx.register_command(
        name="mev-protect",
        handler=handle_mev_protect,
        description="Toggle MEV-protected swap routing via privacy RPC (Holder tier)",
        args_hint="on | off | status",
    )
