"""Delegation commands: ``/delegate``, ``/delegations``, ``/revoke``.

User-facing surface for EIP-7710 / EIP-7715 on-chain delegation. These run
synchronously (no LLM) — the user types them directly.

  /delegate                         overview of active delegations
  /delegate create [policy] [flags] compile → sign → store a delegation
  /delegate revoke <id>             disable a delegation on-chain
  /delegate revoke-all              disable every active delegation
  /delegate status                  detailed status (refreshed on-chain)
  /delegate chains                  supported chains
  /delegate agent [passphrase]      show / create the agent (delegate) key
  /delegate upgrade [chain]         EIP-7702 upgrade the local EOA delegator
  /delegate permissions <policy>    request MetaMask ERC-7715 permissions

  /delegations                      alias for /delegate
  /revoke <id>                      alias for /delegate revoke <id>

``create`` flags: ``--chain <id>``, ``--expiry <7d|24h|…>``,
``--per-call <eth>``, ``--cap <eth>``, ``--period <hourly|daily|weekly|monthly>``,
``--calls <n>``, ``--targets <addr,addr>``, ``--erc20 <token>:<amt>:<dec>[:period]``.
"""

from __future__ import annotations

from clawmes.delegation.agent_key import AgentKeyError, get_agent_key_store
from clawmes.delegation.compiler import (
    DelegationSpec,
    Erc20Limit,
    format_compilation,
    spec_from_policy,
)
from clawmes.delegation.service import (
    DelegationError,
    build_7715_request,
    prepare_delegation,
    refresh_status,
    sign_delegation,
    store_delegation,
    upgrade_eoa_7702,
)
from clawmes.delegation.service import (
    revoke as svc_revoke,
)
from clawmes.delegation.store import get_delegation_store
from clawmes.delegation.types import (
    DEFAULT_CHAIN_ID,
    PERIOD_SECONDS,
    SUPPORTED_CHAIN_IDS,
    TESTNET_CHAIN_IDS,
    chain_name,
    is_supported_chain,
)

_DEFAULT_EXPIRY_SECONDS = 7 * 86400


# ─── dispatch ───────────────────────────────────────────────────────────


async def handle_delegate(raw_args: str) -> str:
    parts = raw_args.strip().split()
    if not parts:
        return _overview()
    sub = parts[0].lower()
    rest = " ".join(parts[1:])
    if sub == "create":
        return _create(rest)
    if sub == "revoke":
        return _revoke(rest)
    if sub == "revoke-all":
        return _revoke_all()
    if sub == "status":
        return _status()
    if sub == "chains":
        return _chains()
    if sub == "agent":
        return _agent(rest)
    if sub == "upgrade":
        return _upgrade(rest)
    if sub == "permissions":
        return _permissions(rest)
    # Unknown subcommand — treat as a lookup by delegation id.
    return _show(sub)


async def handle_delegations(raw_args: str) -> str:
    return await handle_delegate(raw_args.strip() or "status")


async def handle_revoke(raw_args: str) -> str:
    target = raw_args.strip()
    if not target:
        return "Usage: /revoke <delegation-id>"
    return _revoke(target)


# ─── overview / status ──────────────────────────────────────────────────


def _overview() -> str:
    records = get_delegation_store().list_records()
    lines = ["On-chain delegations (EIP-7710)", ""]
    if not records:
        lines.append("No delegations yet.")
        lines.append("")
        lines.append("Create one from a policy or inline limits:")
        lines.append("  /delegate create <policy-name> --expiry 7d")
        lines.append("  /delegate create --per-call 0.1 --cap 1 --period daily")
        lines.append("")
        lines.append("Commands: create · revoke · revoke-all · status · chains · agent · upgrade")
        return "\n".join(lines)
    for r in records:
        lines.append(f"  • {r.id}  [{r.status}]  {chain_name(r.chain_id)}")
        if r.policy_name:
            lines.append(f"      policy: {r.policy_name}")
        lines.append(f"      delegate:  {r.delegation.delegate}")
        lines.append(f"      delegator: {r.delegation.delegator}")
        if r.expires_at:
            lines.append(f"      expires: {r.expires_at}")
    lines.append("")
    lines.append("Detail: /delegate status · Revoke: /delegate revoke <id>")
    return "\n".join(lines)


def _status() -> str:
    records = get_delegation_store().list_records()
    if not records:
        return "No delegations found. Create one with /delegate create."
    lines = [f"Delegation status ({len(records)})", ""]
    for r in records:
        try:
            refresh_status(r)
        except Exception:  # noqa: BLE001 — best-effort refresh
            pass
        lines.append(f"  • {r.id}  [{r.status}]  {chain_name(r.chain_id)} ({r.chain_id})")
        lines.append(f"      delegate:  {r.delegation.delegate}")
        lines.append(f"      delegator: {r.delegation.delegator}")
        lines.append(f"      caveats:   {len(r.delegation.caveats)}")
        if r.tools:
            lines.append(f"      tools:     {', '.join(r.tools)}")
        if r.hash and r.hash != "0x":
            lines.append(f"      hash:      {r.hash}")
        if r.expires_at:
            lines.append(f"      expires:   {r.expires_at}")
        if r.unmapped:
            lines.append(f"      app-layer: {', '.join(r.unmapped)}")
    return "\n".join(lines)


def _chains() -> str:
    lines = ["Supported chains for EIP-7710 delegations", ""]
    for cid in sorted(SUPPORTED_CHAIN_IDS):
        tag = " [testnet]" if cid in TESTNET_CHAIN_IDS else ""
        note = ""
        if cid in TESTNET_CHAIN_IDS or cid == 59144:
            note = f"  (set CLAWMES_RPC_{cid})"
        lines.append(f"  {chain_name(cid)} ({cid}){tag}{note}")
    lines.append("")
    lines.append(f"Default: {chain_name(DEFAULT_CHAIN_ID)} ({DEFAULT_CHAIN_ID})")
    lines.append("Use --chain <id> with /delegate create to target another chain.")
    return "\n".join(lines)


def _show(record_id: str) -> str:
    record = get_delegation_store().load(record_id)
    if record is None:
        return f"No delegation {record_id!r}. See /delegate status."
    lines = [f"Delegation {record.id} [{record.status}]", ""]
    lines.append(f"  chain:     {chain_name(record.chain_id)} ({record.chain_id})")
    lines.append(f"  delegate:  {record.delegation.delegate}")
    lines.append(f"  delegator: {record.delegation.delegator}")
    lines.append(f"  caveats:   {len(record.delegation.caveats)}")
    if record.hash and record.hash != "0x":
        lines.append(f"  hash:      {record.hash}")
    if record.expires_at:
        lines.append(f"  expires:   {record.expires_at}")
    return "\n".join(lines)


# ─── create ─────────────────────────────────────────────────────────────


def _create(rest: str) -> str:
    try:
        spec, chain_id, record_id, policy_name, notes = _parse_create(rest)
    except _CreateError as exc:
        return f"Couldn't build delegation: {exc}"

    try:
        compiled = prepare_delegation(spec, chain_id=chain_id)
    except DelegationError as exc:
        return str(exc)

    try:
        signed = sign_delegation(compiled.delegation, chain_id)
    except DelegationError as exc:
        return (
            f"{exc}\n\n(Delegation compiled but not signed. Ensure your wallet "
            "is connected and can sign EIP-712.)"
        )

    record = store_delegation(
        record_id,
        signed,
        chain_id,
        policy_name=policy_name,
        tools=spec_tools(policy_name),
        expires_at=compiled.expires_at,
        unmapped=tuple(compiled.unmapped),
    )

    lines = [format_compilation(compiled, chain_id), ""]
    lines.append(f"Signed and stored as {record.id!r}.")
    agent = get_agent_key_store().info()
    if agent is not None:
        lines.append("")
        lines.append(f"Agent (delegate) key: {agent.address}")
        lines.append("Fund it with a little ETH — it pays gas for redemptions.")
    lines.append("")
    lines.append(
        "The delegator must be a smart account or EIP-7702-upgraded EOA for "
        "on-chain enforcement. If it's a plain EOA, run /delegate upgrade."
    )
    for note in notes:
        lines.append(f"  note: {note}")
    return "\n".join(lines)


class _CreateError(ValueError):
    pass


def _parse_create(rest: str) -> tuple[DelegationSpec, int, str, str, list[str]]:
    tokens = rest.split()
    flags: dict[str, str] = {}
    positional: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.startswith("--"):
            key = tok[2:]
            if i + 1 >= len(tokens):
                raise _CreateError(f"flag --{key} needs a value")
            flags[key] = tokens[i + 1]
            i += 2
        else:
            positional.append(tok)
            i += 1

    chain_id = _parse_int(flags.get("chain"), DEFAULT_CHAIN_ID, "chain")
    if not is_supported_chain(chain_id):
        raise _CreateError(f"chain {chain_id} not supported")

    notes: list[str] = []
    policy_name = positional[0] if positional else ""
    if policy_name:
        policy = _find_policy(policy_name)
        if policy is None:
            raise _CreateError(f"policy {policy_name!r} not found (see /policy)")
        expiry = _parse_duration(flags.get("expiry"), _DEFAULT_EXPIRY_SECONDS)
        spec, seed_notes = spec_from_policy(policy, expiry_seconds=expiry)
        notes.extend(seed_notes)
    else:
        spec = DelegationSpec(
            expiry_seconds=_parse_duration(flags.get("expiry"), _DEFAULT_EXPIRY_SECONDS)
        )

    _apply_flags(spec, flags)
    record_id = policy_name or f"del-{chain_id}-{spec.expiry_seconds}"
    return spec, chain_id, record_id, policy_name, notes


def _apply_flags(spec: DelegationSpec, flags: dict[str, str]) -> None:
    if "per-call" in flags:
        spec.native_per_call_wei = _eth_to_wei(flags["per-call"], "per-call")
    if "cap" in flags:
        spec.native_cap_wei = _eth_to_wei(flags["cap"], "cap")
    if "period" in flags:
        period = flags["period"].lower()
        if period not in PERIOD_SECONDS:
            raise _CreateError(f"period must be one of {', '.join(PERIOD_SECONDS)}")
        spec.native_period_seconds = PERIOD_SECONDS[period]
    if "calls" in flags:
        spec.max_calls = _parse_int(flags["calls"], 0, "calls")
    if "targets" in flags:
        spec.allowed_targets = [t for t in flags["targets"].split(",") if t]
    if "erc20" in flags:
        spec.erc20.append(_parse_erc20(flags["erc20"]))


def _parse_erc20(value: str) -> Erc20Limit:
    bits = value.split(":")
    if len(bits) < 3:
        raise _CreateError("--erc20 format: <token>:<amount>:<decimals>[:period]")
    token, amount_str, decimals_str = bits[0], bits[1], bits[2]
    try:
        decimals = int(decimals_str)
        base = _scaled(amount_str, decimals)
    except (ValueError, ArithmeticError) as exc:
        raise _CreateError(f"bad --erc20 amount/decimals: {exc}") from exc
    period = 0
    if len(bits) >= 4 and bits[3]:
        if bits[3].lower() not in PERIOD_SECONDS:
            raise _CreateError(f"--erc20 period must be one of {', '.join(PERIOD_SECONDS)}")
        period = PERIOD_SECONDS[bits[3].lower()]
    return Erc20Limit(token=token, max_amount=base, period_seconds=period)


# ─── revoke ─────────────────────────────────────────────────────────────


def _revoke(record_id: str) -> str:
    record_id = record_id.strip()
    if not record_id:
        return "Usage: /delegate revoke <delegation-id>"
    record = get_delegation_store().load(record_id)
    if record is None:
        return f"No delegation {record_id!r}. See /delegate status."
    if record.status == "revoked":
        return f"Delegation {record_id!r} is already revoked."
    tx = svc_revoke(record)
    if tx:
        return (
            f"Revoked {record_id!r} on-chain.\n  Tx: {tx}\n  Chain: "
            f"{chain_name(record.chain_id)}\nThe delegation can no longer be redeemed."
        )
    return (
        f"Revoked {record_id!r} locally (no wallet connected for the on-chain "
        "disableDelegation call). Connect the delegator wallet and revoke again "
        "to disable it on-chain."
    )


def _revoke_all() -> str:
    records = [r for r in get_delegation_store().list_records() if r.status != "revoked"]
    if not records:
        return "No active delegations to revoke."
    lines = [f"Revoking {len(records)} delegation(s)…", ""]
    for r in records:
        tx = svc_revoke(r)
        lines.append(f"  • {r.id}: {'tx ' + tx if tx else 'local only'}")
    return "\n".join(lines)


# ─── agent / upgrade / permissions ──────────────────────────────────────


def _agent(rest: str) -> str:
    store = get_agent_key_store()
    info = store.info()
    if info is not None:
        return (
            f"Agent (delegate) key\n  address: {info.address}\n  storage: "
            f"{info.storage}\n  created: {info.created_at}\n\n"
            "This key signs and pays gas for delegation redemptions. Fund it "
            "with a small amount of ETH."
        )
    passphrase = rest.strip() or None
    try:
        created = store.create(passphrase=passphrase)
    except AgentKeyError as exc:
        return str(exc)
    return (
        f"Generated agent (delegate) key.\n  address: {created.address}\n  "
        f"storage: {created.storage}\n\nFund it with a little ETH for gas, then "
        "create a delegation with /delegate create."
    )


def _upgrade(rest: str) -> str:
    chain_id = _parse_int(rest.strip() or None, DEFAULT_CHAIN_ID, "chain")
    try:
        tx = upgrade_eoa_7702(chain_id=chain_id)
    except DelegationError as exc:
        return str(exc)
    return (
        f"Submitted EIP-7702 upgrade on {chain_name(chain_id)}.\n  Tx: {tx}\n\n"
        "Your EOA keeps its address but gains smart-account powers, so the "
        "DelegationManager can enforce caveats on-chain. Create a delegation "
        "with /delegate create."
    )


def _permissions(rest: str) -> str:
    policy_name = rest.strip()
    if not policy_name:
        return "Usage: /delegate permissions <policy-name>"
    policy = _find_policy(policy_name)
    if policy is None:
        return f"Policy {policy_name!r} not found (see /policy)."

    from clawmes.services.wallet import get_wallet_service

    svc = get_wallet_service()
    state = svc.state
    mode = svc.active_mode
    if not state.connected or state.address is None or mode is None:
        return "Connect a wallet (WalletConnect + a smart account) first."

    spec, _notes = spec_from_policy(policy)
    chain_id = state.chain_id or DEFAULT_CHAIN_ID
    import time

    expiry = int(time.time()) + _DEFAULT_EXPIRY_SECONDS
    request = build_7715_request(spec, chain_id, signer=state.address, expiry=expiry)

    requester = getattr(mode, "request_execution_permissions", None)
    if requester is None:
        return (
            "The connected wallet mode doesn't support ERC-7715 permission "
            "requests. Use WalletConnect with a MetaMask smart account, or use "
            "/delegate create for the raw EIP-7710 path."
        )
    try:
        granted = requester(request["params"])
    except Exception as exc:  # noqa: BLE001 — surface wallet/bridge errors
        return f"Permission request failed: {exc}"
    return (
        f"Requested ERC-7715 permissions for policy {policy_name!r} on "
        f"{chain_name(chain_id)}.\n  Response: {granted}\n\nThe agent can now "
        "redeem within these permissions."
    )


# ─── helpers ────────────────────────────────────────────────────────────


def spec_tools(policy_name: str) -> tuple[str, ...]:
    if not policy_name:
        return ()
    policy = _find_policy(policy_name)
    if policy is None:
        return ()
    return tuple(policy.applies_to_tools)


def _find_policy(name: str):
    from clawmes.policy.storage import load_policies

    for policy in load_policies():
        if policy.name == name:
            return policy
    return None


def _parse_int(value: str | None, default: int, label: str) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise _CreateError(f"{label} must be an integer, got {value!r}") from exc


def _eth_to_wei(value: str, label: str) -> int:
    try:
        return _scaled(value, 18)
    except (ValueError, ArithmeticError) as exc:
        raise _CreateError(f"bad {label} amount {value!r}: {exc}") from exc


def _scaled(amount: str, decimals: int) -> int:
    from clawmes.lib.decimals import to_base_units

    return to_base_units(amount, decimals)


def _parse_duration(value: str | None, default: int) -> int:
    if not value:
        return default
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
    suffix = value[-1].lower()
    if suffix in units:
        try:
            return int(value[:-1]) * units[suffix]
        except ValueError as exc:
            raise _CreateError(f"bad duration {value!r}") from exc
    try:
        return int(value)
    except ValueError as exc:
        raise _CreateError(f"bad duration {value!r} (use 7d, 24h, 30m…)") from exc


# ─── registration ───────────────────────────────────────────────────────


def register(ctx) -> None:
    ctx.register_command(
        name="delegate",
        handler=handle_delegate,
        description="On-chain delegation (EIP-7710): create/revoke/status/upgrade",
        args_hint="[create|revoke|revoke-all|status|chains|agent|upgrade|permissions] [args]",
    )
    ctx.register_command(
        name="delegations",
        handler=handle_delegations,
        description="List active on-chain delegations",
    )
    ctx.register_command(
        name="revoke",
        handler=handle_revoke,
        description="Revoke an on-chain delegation by id",
        args_hint="<delegation-id>",
    )
