"""Delegation lifecycle service — prepare, sign, store, redeem, revoke.

The orchestrator that ties the compiler, store, agent key, wallet modes and
RPC together. Pure Python; the only chain I/O is through
:class:`clawmes.services.rpc.RpcService` (``eth_call`` for simulation/reads,
``eth_sendRawTransaction`` for the agent-signed redeem/upgrade txs).

Signing model (EIP-7710):
  * the **delegator** (user wallet) signs the EIP-712 ``Delegation`` — via
    whatever wallet mode is active (local key / WalletConnect / Bankr).
  * the **delegate** (agent key) signs + broadcasts ``redeemDelegations`` and
    pays gas.
  * revocation (``disableDelegation``) must come from the delegator, so it
    routes through the wallet mode too.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from clawmes.delegation import encoding as E
from clawmes.delegation.agent_key import AgentKeyError, get_agent_key_store
from clawmes.delegation.compiler import (
    CompileError,
    DelegationSpec,
    compile_spec,
)
from clawmes.delegation.store import get_delegation_store
from clawmes.delegation.types import (
    DEFAULT_CHAIN_ID,
    DELEGATION_MANAGER,
    EIP7702_STATELESS_DELEGATOR,
    EXECUTE_MODE_DEFAULT,
    CompiledDelegation,
    DelegationRecord,
    ExecutionAction,
    SignedDelegation,
    UnsignedDelegation,
    is_supported_chain,
)
from clawmes.lib.logger import logger_for

if TYPE_CHECKING:
    from clawmes.services.rpc import RpcService
    from clawmes.services.wallet import WalletService

_log = logger_for("delegation.service")

# Known DelegationManager custom-error selectors → human messages. Surfaced
# when an eth_call simulation reverts, so the agent doesn't burn gas.
_ERROR_SELECTORS: dict[str, str] = {
    "0x155ff427": "signature verification failed (delegator did not sign, or "
    "is not a smart account / EIP-7702 upgraded)",
    "0xded4370e": "invalid authority (root delegations must use 0xff..ff)",
    "0x8baa579f": "invalid signature (ECDSA recovery mismatch)",
    "0xb5863604": "caller is not the authorized delegate",
    "0xa9e649e9": "invalid delegation struct",
    "0xac241e11": "empty signature",
    "0x0ab29062": "no delegations provided",
}


class DelegationError(RuntimeError):
    """Raised for delegation lifecycle failures with a user-facing message."""


@dataclass
class RedemptionResult:
    tx_hash: str
    chain_id: int


# ─── wallet / rpc access ────────────────────────────────────────────────


def _wallet() -> WalletService:
    from clawmes.services.wallet import get_wallet_service

    return get_wallet_service()


def _rpc() -> RpcService:
    from clawmes.services.rpc import get_rpc_service

    return get_rpc_service()


def resolve_delegator() -> str | None:
    """The connected wallet address (the delegator). ``None`` if disconnected."""
    state = _wallet().state
    return state.address if state.connected else None


def resolve_delegate() -> str:
    """The agent (delegate) address, creating the agent key if needed."""
    store = get_agent_key_store()
    info = store.info()
    if info is not None:
        return info.address
    return store.create().address


# ─── prepare + sign + store ─────────────────────────────────────────────


def prepare_delegation(
    spec: DelegationSpec,
    *,
    chain_id: int = DEFAULT_CHAIN_ID,
    delegator: str | None = None,
    delegate: str | None = None,
    salt: int | None = None,
) -> CompiledDelegation:
    """Compile ``spec`` into an unsigned delegation ready for signing.

    Resolves delegate (agent key, auto-created) and delegator (connected
    wallet) when not given. Raises :class:`DelegationError` if no delegator
    is available or the spec is invalid.
    """
    delegate = delegate or resolve_delegate()
    delegator = delegator or resolve_delegator()
    if not delegator:
        raise DelegationError(
            "no wallet connected — connect a wallet (the delegator that grants "
            "the delegation) with /connect first."
        )
    try:
        return compile_spec(
            spec, delegate=delegate, delegator=delegator, chain_id=chain_id, salt=salt
        )
    except CompileError as exc:
        raise DelegationError(str(exc)) from exc


def sign_delegation(unsigned: UnsignedDelegation, chain_id: int) -> SignedDelegation:
    """Have the delegator (connected wallet) EIP-712 sign ``unsigned``."""
    mode = _wallet().active_mode
    if mode is None:
        raise DelegationError("no wallet connected to sign the delegation")
    typed = E.build_typed_data(unsigned, chain_id)
    try:
        signature = mode.sign_typed_data_v4(typed)
    except Exception as exc:  # noqa: BLE001 — surface any signer/bridge error
        raise DelegationError(f"delegation signing failed: {exc}") from exc
    if not signature.startswith("0x"):
        signature = "0x" + signature
    return SignedDelegation.from_unsigned(unsigned, signature)


def store_delegation(
    record_id: str,
    signed: SignedDelegation,
    chain_id: int,
    *,
    policy_name: str = "",
    tools: tuple[str, ...] = (),
    expires_at: str = "",
    unmapped: tuple[str, ...] = (),
) -> DelegationRecord:
    """Persist a signed delegation and best-effort fetch its on-chain hash."""
    record = DelegationRecord(
        id=record_id,
        chain_id=chain_id,
        delegation=signed,
        status="signed",
        policy_name=policy_name,
        tools=tools,
        expires_at=expires_at,
        unmapped=unmapped,
        created_at=datetime.now(tz=UTC).isoformat(),
    )
    record.hash = _try_get_hash(signed, chain_id)
    get_delegation_store().save(record)
    return record


def _try_get_hash(signed: SignedDelegation, chain_id: int) -> str:
    try:
        raw = _rpc().eth_call(
            to=DELEGATION_MANAGER,
            data=E.build_get_hash_calldata(signed),
            chain_id=chain_id,
        )
        if raw and raw != "0x" and len(raw) >= 66:
            return raw[:66]
    except Exception as exc:  # noqa: BLE001 — non-fatal
        _log.debug("getDelegationHash read failed: %s", exc)
    return "0x"


# ─── redemption (agent-signed) ──────────────────────────────────────────


def redeem(record: DelegationRecord, action: ExecutionAction) -> RedemptionResult:
    """Redeem ``record`` to execute ``action`` on-chain, signed by the agent.

    Simulates via ``eth_call`` first (parsing known revert selectors) so a
    doomed redemption never spends gas. Raises :class:`DelegationError` with
    a decoded reason on any failure.
    """
    if not record.is_redeemable():
        raise DelegationError(f"delegation {record.id} is {record.status}; cannot redeem")

    chain_id = record.chain_id
    if not _rpc().has_endpoint(chain_id):
        raise DelegationError(
            f"no RPC endpoint configured for chain {chain_id} (set CLAWMES_RPC_{chain_id})"
        )

    permission_context = (
        record.permissions_context
        if record.kind == "eip7715" and record.permissions_context
        else E.encode_permission_context([record.delegation])
    )
    execution = E.encode_execution(action.target, action.value, action.call_data)
    calldata = E.build_redeem_calldata(permission_context, execution, EXECUTE_MODE_DEFAULT)

    agent = get_agent_key_store()
    try:
        agent_key = agent.load_private_key()
        agent_addr = agent.address()
    except AgentKeyError as exc:
        raise DelegationError(str(exc)) from exc

    # Simulate.
    try:
        _rpc().eth_call(to=DELEGATION_MANAGER, data=calldata, chain_id=chain_id)
    except Exception as exc:  # noqa: BLE001
        raise DelegationError(_decode_revert(str(exc))) from exc

    # Broadcast (agent-signed type-2 tx).
    try:
        tx_hash = _send_agent_tx(
            agent_key=agent_key,
            agent_addr=agent_addr,
            to=DELEGATION_MANAGER,
            value=0,
            data=calldata,
            chain_id=chain_id,
        )
    except Exception as exc:  # noqa: BLE001
        raise DelegationError(f"redeem broadcast failed: {exc}") from exc

    if record.status == "signed":
        record.status = "active"
        record.last_checked_at = datetime.now(tz=UTC).isoformat()
        get_delegation_store().save(record)
    return RedemptionResult(tx_hash=tx_hash, chain_id=chain_id)


def _send_agent_tx(
    *, agent_key: str, agent_addr: str | None, to: str, value: int, data: str, chain_id: int
) -> str:
    import os

    from eth_account import Account
    from eth_utils import to_checksum_address

    rpc = _rpc()
    if agent_addr is None:
        agent_addr = Account.from_key(agent_key).address
    nonce = rpc.get_transaction_count(agent_addr, chain_id)
    try:
        gas = int(
            rpc.estimate_gas(from_addr=agent_addr, to=to, value=value, data=data, chain_id=chain_id)
        )
        gas = int(gas * 1.2)
    except Exception:  # noqa: BLE001 — fall back to a generous ceiling
        gas = 500_000
    tx = {
        "to": to_checksum_address(to),
        "value": int(value),
        "gas": gas,
        "maxFeePerGas": int(os.environ.get("CLAWMES_MAX_FEE_WEI") or 10**10),
        "maxPriorityFeePerGas": int(os.environ.get("CLAWMES_PRIORITY_FEE_WEI") or 10**9),
        "nonce": int(nonce),
        "chainId": int(chain_id),
        "type": 2,
        "data": data,
    }
    signed = Account.sign_transaction(tx, agent_key)
    raw = signed.raw_transaction.hex()
    return rpc.send_raw_transaction(raw, chain_id)


def _decode_revert(message: str) -> str:
    for sel, human in _ERROR_SELECTORS.items():
        if sel in message:
            return f"delegation simulation reverted: {human}"
    return f"delegation simulation reverted: {message[:200]}"


# ─── revocation (delegator-signed) + status ─────────────────────────────


def revoke(record: DelegationRecord) -> str | None:
    """Revoke ``record`` on-chain via ``disableDelegation`` (delegator wallet).

    Returns the tx hash on success, or ``None`` if only a local revoke was
    possible (no wallet). Always marks the local record revoked.
    """
    store = get_delegation_store()
    tx_hash: str | None = None
    mode = _wallet().active_mode
    if mode is not None:
        try:
            tx_hash = mode.send_transaction(
                to=DELEGATION_MANAGER,
                value=0,
                data=E.build_disable_calldata(record.delegation),
                chain_id=record.chain_id,
            )
        except Exception as exc:  # noqa: BLE001 — degrade to local revoke
            _log.warning("on-chain revoke failed for %s: %s", record.id, exc)
            tx_hash = None
    record.status = "revoked"
    record.last_checked_at = datetime.now(tz=UTC).isoformat()
    store.save(record)
    return tx_hash


def refresh_status(record: DelegationRecord) -> DelegationRecord:
    """Read ``disabledDelegations`` on-chain and update the record status."""
    if record.status == "revoked" or not record.hash or record.hash == "0x":
        return record
    try:
        raw = _rpc().eth_call(
            to=DELEGATION_MANAGER,
            data=E.build_disabled_calldata(record.hash),
            chain_id=record.chain_id,
        )
        disabled = int(raw, 16) != 0 if raw and raw != "0x" else False
        record.status = "revoked" if disabled else "active"
        record.last_checked_at = datetime.now(tz=UTC).isoformat()
        get_delegation_store().save(record)
    except Exception as exc:  # noqa: BLE001 — keep cached status
        _log.debug("status refresh failed for %s: %s", record.id, exc)
    return record


# ─── EIP-7715 request builder ───────────────────────────────────────────


def build_7715_request(spec: DelegationSpec, chain_id: int, *, signer: str, expiry: int) -> dict:
    """Build a ``wallet_requestExecutionPermissions`` JSON-RPC payload.

    Maps spec fields to ERC-7715 permission objects. Sent to wallets that
    support the method (e.g. MetaMask smart accounts) via the WC bridge.
    """
    permissions: list[dict] = []
    if spec.native_per_call_wei is not None or spec.native_cap_wei is not None:
        amount = spec.native_cap_wei or spec.native_per_call_wei or 0
        permissions.append(
            {
                "type": "native-token-transfer",
                "data": {"allowance": hex(amount)},
            }
        )
    for limit in spec.erc20:
        permissions.append(
            {
                "type": "erc20-token-periodic",
                "data": {
                    "tokenAddress": limit.token,
                    "periodAmount": hex(limit.max_amount),
                    "periodDuration": limit.period_seconds or 86400,
                },
            }
        )
    return {
        "method": "wallet_requestExecutionPermissions",
        "params": [
            {
                "chainId": hex(chain_id),
                "signer": {"type": "account", "data": {"address": signer}},
                "permissions": permissions,
                "expiry": expiry,
            }
        ],
    }


# ─── EIP-7702 upgrade (local-key delegator only) ────────────────────────


def upgrade_eoa_7702(*, chain_id: int, implementation: str | None = None) -> str:
    """Upgrade the connected local-key EOA to a smart account via EIP-7702.

    Signs an EIP-7702 authorization designating the MetaMask stateless
    DeleGator implementation and submits a type-4 transaction. Only supported
    for the local-key wallet mode (needs direct key access). Returns the tx
    hash. Raises :class:`DelegationError` otherwise.
    """
    if not is_supported_chain(chain_id):
        raise DelegationError(f"chain {chain_id} not supported for 7702 upgrade")

    from clawmes.wallet.local_key import LocalKeyMode

    mode = _wallet().active_mode
    if not isinstance(mode, LocalKeyMode):
        raise DelegationError(
            "EIP-7702 upgrade requires local-key wallet mode (direct key access). "
            "WalletConnect wallets should use their own 7702 UI."
        )

    impl = implementation or EIP7702_STATELESS_DELEGATOR
    try:
        private_key = mode._derive_privkey()  # noqa: SLF001 — same package trust boundary
    except Exception as exc:  # noqa: BLE001
        raise DelegationError(
            f"could not access local key for 7702 signing: {exc}. Reconnect with "
            "password_cache_seconds > 0."
        ) from exc

    from eth_account import Account
    from eth_utils import to_checksum_address

    rpc = _rpc()
    if not rpc.has_endpoint(chain_id):
        raise DelegationError(f"no RPC endpoint for chain {chain_id} (set CLAWMES_RPC_{chain_id})")

    acct = Account.from_key(private_key)
    address = acct.address
    nonce = rpc.get_transaction_count(address, chain_id)
    # The authorization nonce is the account nonce + 1 (the type-4 tx consumes
    # `nonce`, the authorization is checked against the next value).
    auth = acct.sign_authorization(
        {"chainId": chain_id, "address": to_checksum_address(impl), "nonce": nonce + 1}
    )
    tx = {
        "type": 4,
        "chainId": chain_id,
        "nonce": nonce,
        "gas": 200_000,
        "maxFeePerGas": 10**10,
        "maxPriorityFeePerGas": 10**9,
        "to": address,
        "value": 0,
        "authorizationList": [auth],
    }
    try:
        signed = Account.sign_transaction(tx, private_key)
        return rpc.send_raw_transaction(signed.raw_transaction.hex(), chain_id)
    except Exception as exc:  # noqa: BLE001
        raise DelegationError(f"7702 upgrade tx failed: {exc}") from exc
