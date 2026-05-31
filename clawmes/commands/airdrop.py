"""``/airdrop scan|claim`` — autonomous airdrop checker + claimer.

A Clawmes Unlimited feature. Maintains a registry of known airdrop
checkers, queries each one for the active wallet's eligibility, and
optionally submits claim transactions.

The registry is intentionally conservative and append-only — we only
add airdrops whose claim contracts are well-known and verified.
False positives here cost real gas, and a bad-faith airdrop checker
could leak the active wallet address. Registry entries include:

  * ``name`` — display name
  * ``check_url`` — endpoint that returns eligibility for an address
  * ``check_method`` — ``GET`` (most checkers) or ``POST``
  * ``claim_contract`` — contract address for the claim function
  * ``claim_selector`` — 4-byte function selector for claim
  * ``eligibility_path`` — JSON path into the check response for the
    "eligible amount" or "amount" field

Surface:

  * ``/airdrop scan``           — check eligibility on every registered
                                  airdrop using the active wallet.
  * ``/airdrop list``           — show registered airdrops + their
                                  check URLs (read-only, no wallet
                                  query)
  * ``/airdrop claim <name>``   — submit the claim transaction for
                                  ``name``. Requires prior /scan to
                                  populate the eligibility cache.
  * ``/airdrop history``        — past scans + claims

State: ``${HERMES_HOME}/clawmes/airdrop/state.json``.

The v1 registry ships with placeholder entries so the surface is
exercised end-to-end. Real registry entries land as we verify
specific airdrops' claim ABIs. Adding a verified airdrop is a
single-line config change.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from clawmes.lib.http import http_get

# ── registry ───────────────────────────────────────────────────────


# Maintained registry. Each entry must point at a verified claim
# contract; we never claim on unverified contracts because a malicious
# claim() can drain approvals. ``None`` for ``claim_contract`` /
# ``claim_selector`` means "checker-only" — we report eligibility but
# don't auto-claim.
_REGISTRY: list[dict[str, Any]] = [
    {
        "name": "demo-checker",
        "check_url": "https://example.com/airdrop/check",
        "check_method": "GET",
        "claim_contract": None,
        "claim_selector": None,
        "eligibility_path": "amount",
        "description": "Demo entry — replace with real airdrops as we verify them.",
    },
]


# ── state I/O ───────────────────────────────────────────────────────


def _state_path() -> Path:
    from clawmes.lib.paths import state_dir

    return state_dir("airdrop") / "state.json"


def _load_state() -> dict[str, Any]:
    path = _state_path()
    if not path.exists():
        return {"scans": [], "claims": []}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {"scans": [], "claims": []}
    if not isinstance(data, dict):
        return {"scans": [], "claims": []}
    if not isinstance(data.get("scans"), list):
        data["scans"] = []
    if not isinstance(data.get("claims"), list):
        data["claims"] = []
    return data


def _save_state(state: dict[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now_epoch() -> int:
    return int(time.time())


def _new_id() -> str:
    return f"drop_{uuid.uuid4().hex[:10]}"


def _record(name: str, args: str, result: str) -> None:
    try:
        from clawmes.services.command_history import record_command_call

        record_command_call(name, args, result)
    except Exception:  # noqa: BLE001
        pass


# ── dispatch ────────────────────────────────────────────────────────


async def handle_airdrop(raw_args: str, *, sender_id: str = "default", **_kwargs: Any) -> str:
    raw = (raw_args or "").strip()
    if not raw:
        out = _render_usage()
    else:
        # UNLIMITED gate. The reasoning: this command reads sensitive
        # wallet eligibility data from third-party endpoints. Pinning
        # it behind the highest tier ensures only committed users
        # exercise it.
        from clawmes.services.token_gate import Tier, check_tier_or_error

        gate_err = check_tier_or_error(Tier.UNLIMITED, feature="/airdrop")
        if gate_err:
            return gate_err

        parts = raw.split()
        sub = parts[0].lower()
        rest = parts[1:]
        if sub == "scan":
            out = await _cmd_scan(sender_id)
        elif sub == "list":
            out = _cmd_list()
        elif sub == "claim":
            out = await _cmd_claim(sender_id, rest)
        elif sub == "history":
            out = _cmd_history(sender_id)
        else:
            out = f"Unknown subcommand: {sub!r}\n\n" + _render_usage()
    _record("airdrop", raw_args, out)
    return out


def _render_usage() -> str:
    return (
        "Autonomous airdrop scanner + claimer.\n"
        "  (Clawmes Unlimited tier — hold 100M+ $CLAWNCH.)\n"
        "\n"
        "  /airdrop scan            Check eligibility on every registered airdrop\n"
        "                           using the active wallet's address\n"
        "  /airdrop list            Show registered airdrops (read-only)\n"
        "  /airdrop claim <name>    Submit the claim tx for <name>\n"
        "  /airdrop history         Past scans + claims for this sender\n"
        "\n"
        "The registry is conservative — we only auto-claim airdrops whose\n"
        "claim contracts are verified. Checker-only entries report eligibility\n"
        "without exposing your wallet to claim() calls on unverified code."
    )


# ── /airdrop list ──────────────────────────────────────────────────


def _cmd_list() -> str:
    if not _REGISTRY:
        return "No airdrops registered."
    lines = [f"Registered airdrops ({len(_REGISTRY)}):", ""]
    for entry in _REGISTRY:
        claimable = "✓ auto-claim" if entry.get("claim_contract") else "○ check-only"
        lines.append(f"  {entry['name']:<20s}  {claimable}  {entry.get('description', '')}")
    return "\n".join(lines)


# ── /airdrop scan ──────────────────────────────────────────────────


async def _cmd_scan(sender_id: str) -> str:
    """Check every registered airdrop's eligibility for the active wallet."""
    from clawmes.services.wallet import get_wallet_state

    wstate = get_wallet_state()
    if not wstate.connected or not wstate.address:
        return "No wallet connected. Run /connect first."

    address = wstate.address.lower()
    scan_id = _new_id()
    results: list[dict[str, Any]] = []
    for entry in _REGISTRY:
        amount = _check_eligibility(entry, address)
        results.append(
            {
                "name": entry["name"],
                "claim_contract": entry.get("claim_contract"),
                "eligible_amount": amount,
                "at": _now_iso(),
            }
        )

    state = _load_state()
    state["scans"].append(
        {
            "id": scan_id,
            "sender_id": sender_id,
            "address": address,
            "at": _now_iso(),
            "results": results,
        }
    )
    _save_state(state)

    eligible = [r for r in results if r.get("eligible_amount")]
    if not eligible:
        return (
            f"Scan {scan_id} complete. {len(_REGISTRY)} airdrops checked, "
            f"none eligible for {address}."
        )

    lines = [
        f"Scan {scan_id} complete. {len(eligible)}/{len(_REGISTRY)} eligible:",
        "",
    ]
    for r in eligible:
        claimable = "auto-claim available" if r.get("claim_contract") else "check-only"
        lines.append(f"  {r['name']:<20s}  amount: {r['eligible_amount']}  ({claimable})")
    lines.append("")
    lines.append("Submit a claim: /airdrop claim <name>")
    return "\n".join(lines)


def _check_eligibility(entry: dict[str, Any], address: str) -> Any:
    """Query the checker endpoint. Returns the "amount" field or None.

    Never raises. A checker that's offline or malformed shows up as
    "not eligible" rather than an error — we don't want one bad
    endpoint to break the scan loop.
    """
    url = entry.get("check_url")
    method = (entry.get("check_method") or "GET").upper()
    if not url:
        return None
    try:
        if method == "GET":
            body = http_get(url, params={"address": address}, timeout=10.0)
        else:
            # POST checker: send {"address": "..."} as JSON. The http_get
            # helper doesn't support POST; we use urllib directly here.
            body = _post_json(url, {"address": address})
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(body, dict):
        return None
    path = entry.get("eligibility_path") or "amount"
    return body.get(path)


def _post_json(url: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    """Minimal POST-JSON helper that never raises."""
    try:
        import urllib.request as _req

        data = json.dumps(payload).encode("utf-8")
        req = _req.Request(
            url,
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "User-Agent": "clawmes-airdrop/1.0",
            },
        )
        with _req.urlopen(req, timeout=10.0) as resp:  # noqa: S310 — registry URL
            raw = resp.read().decode("utf-8")
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        return None


# ── /airdrop claim ─────────────────────────────────────────────────


async def _cmd_claim(sender_id: str, parts: list[str]) -> str:
    if not parts:
        return "Usage: /airdrop claim <name>"
    name = parts[0]
    entry = next((e for e in _REGISTRY if e["name"] == name), None)
    if entry is None:
        return f"No airdrop registered with name {name!r}. Use /airdrop list."

    contract = entry.get("claim_contract")
    selector = entry.get("claim_selector")
    if not contract or not selector:
        return (
            f"{name!r} is check-only (claim contract not verified).\n"
            "Visit the project's official UI to claim manually."
        )

    # Verify the last scan saw this wallet eligible. We refuse to fire
    # a claim tx without recent eligibility proof — too easy to waste
    # gas on a "you're not eligible" revert.
    state = _load_state()
    recent_scans = [
        s
        for s in state["scans"]
        if s.get("sender_id") == sender_id
        and any(r.get("name") == name and r.get("eligible_amount") for r in s.get("results", []))
    ]
    if not recent_scans:
        return (
            f"No recent /airdrop scan showed {name!r} as eligible for this "
            "sender. Run /airdrop scan first."
        )

    from clawmes.services.wallet import get_wallet_service, get_wallet_state

    wstate = get_wallet_state()
    if not wstate.connected or not wstate.address:
        return "No wallet connected. Run /connect first."

    mode = get_wallet_service().active_mode
    if mode is None:
        return "No active wallet mode. Run /connect."

    # Build minimal calldata: selector + address arg. Most claim()
    # functions take the recipient as a single address argument. If
    # an airdrop needs a more complex shape, its registry entry will
    # need a custom encoder hook (out of scope for v1).
    from clawmes.lib.abi import encode_address

    calldata = selector + encode_address(wstate.address)

    try:
        tx_hash = mode.send_transaction(
            to=contract,
            value=0,
            data=calldata,
            chain_id=8453,
        )
    except Exception as exc:  # noqa: BLE001
        return f"Claim tx failed: {exc}"

    state["claims"].append(
        {
            "id": _new_id(),
            "sender_id": sender_id,
            "name": name,
            "tx_hash": tx_hash,
            "at": _now_iso(),
        }
    )
    _save_state(state)

    return (
        f"Claim submitted for {name!r}.\n"
        f"  Tx:       {tx_hash}\n"
        f"  Basescan: https://basescan.org/tx/{tx_hash}"
    )


# ── /airdrop history ───────────────────────────────────────────────


def _cmd_history(sender_id: str) -> str:
    state = _load_state()
    scans = [s for s in state["scans"] if s.get("sender_id") == sender_id]
    claims = [c for c in state["claims"] if c.get("sender_id") == sender_id]
    if not scans and not claims:
        return f"No airdrop history for {sender_id}."
    lines = [f"Airdrop history for {sender_id}:", ""]
    if scans:
        lines.append(f"  Scans ({len(scans)}):")
        for s in scans[-10:]:
            lines.append(f"    {s.get('at')}  {s['id']}  {len(s.get('results', []))} checked")
    if claims:
        lines.append("")
        lines.append(f"  Claims ({len(claims)}):")
        for c in claims[-10:]:
            lines.append(f"    {c.get('at')}  {c['name']}  tx {c.get('tx_hash', '')[:14]}…")
    return "\n".join(lines)


def register(ctx) -> None:
    ctx.register_command(
        name="airdrop",
        handler=handle_airdrop,
        description="Autonomous airdrop scanner + claimer (Clawmes Unlimited)",
        args_hint="scan | list | claim <name> | history",
    )
