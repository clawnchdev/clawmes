"""Tests for clawmes.delegation.service.

RPC and wallet are faked; the agent key is a fixed test key so agent-signed
transactions serialize deterministically.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from eth_account import Account

from clawmes.delegation import service as svc
from clawmes.delegation.agent_key import AgentKeyError
from clawmes.delegation.compiler import DelegationSpec
from clawmes.delegation.service import (
    DelegationError,
    build_7715_request,
    prepare_delegation,
    redeem,
    refresh_status,
    resolve_delegate,
    resolve_delegator,
    revoke,
    sign_delegation,
    store_delegation,
    upgrade_eoa_7702,
)
from clawmes.delegation.store import get_delegation_store
from clawmes.delegation.types import (
    ROOT_AUTHORITY,
    Caveat,
    DelegationRecord,
    ExecutionAction,
    SignedDelegation,
)

_AGENT_KEY = "0x" + "11" * 32
_AGENT_ADDR = Account.from_key(_AGENT_KEY).address
_DELEGATOR = "0x" + "33" * 20


class _FakeMode:
    def __init__(self, *, sign="0x" + "cd" * 65, tx="0xtxhash", raises=False):
        self._sign = sign
        self._tx = tx
        self._raises = raises
        self.sent: list[dict] = []

    def sign_typed_data_v4(self, typed):
        if self._raises:
            raise RuntimeError("user rejected")
        return self._sign

    def send_transaction(self, **kwargs):
        if self._raises:
            raise RuntimeError("wallet offline")
        self.sent.append(kwargs)
        return self._tx


class _FakeWallet:
    def __init__(self, *, address=_DELEGATOR, connected=True, mode=None, chain_id=8453):
        self.state = SimpleNamespace(address=address, connected=connected, chain_id=chain_id)
        self.active_mode = mode


class _FakeRpc:
    def __init__(self):
        self.endpoints = {8453}
        self.call_result = "0x"
        self.call_raises: Exception | None = None
        self.code = "0xffff"
        self.sent_raw: list[str] = []

    def has_endpoint(self, chain_id):
        return chain_id in self.endpoints

    def eth_call(self, *, to, data, chain_id, block="latest"):
        if self.call_raises is not None:
            raise self.call_raises
        return self.call_result

    def get_code(self, address, chain_id, block="latest"):
        return self.code

    def get_transaction_count(self, address, chain_id, block="pending"):
        return 0

    def estimate_gas(self, *, from_addr, to, value, data, chain_id):
        return 100000

    def send_raw_transaction(self, raw, chain_id):
        self.sent_raw.append(raw)
        return "0xsentraw"


class _FakeAgentStore:
    def __init__(self, *, key=_AGENT_KEY, addr=_AGENT_ADDR, info=True, raises=False):
        self._key = key
        self._addr = addr
        self._info = SimpleNamespace(address=addr) if info else None
        self._raises = raises

    def info(self):
        return self._info

    def create(self):
        self._info = SimpleNamespace(address=self._addr)
        return self._info

    def address(self):
        return self._addr

    def load_private_key(self, passphrase=None):
        if self._raises:
            raise AgentKeyError("locked")
        return self._key


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    import clawmes.delegation.store as store_mod

    monkeypatch.setattr(store_mod, "_instance", None)


def _patch(monkeypatch, *, wallet=None, rpc=None, agent=None):
    monkeypatch.setattr(svc, "_wallet", lambda: wallet or _FakeWallet())
    monkeypatch.setattr(svc, "_rpc", lambda: rpc or _FakeRpc())
    monkeypatch.setattr(svc, "get_agent_key_store", lambda: agent or _FakeAgentStore())


def _signed(sig="0x" + "cd" * 65) -> SignedDelegation:
    return SignedDelegation(
        delegate=_AGENT_ADDR,
        delegator=_DELEGATOR,
        authority=ROOT_AUTHORITY,
        caveats=(Caveat("0x" + "44" * 20, "0x64", "0x"),),
        salt=1,
        signature=sig,
    )


def _record(**kw) -> DelegationRecord:
    defaults = dict(id="r1", chain_id=8453, delegation=_signed(), status="signed")
    defaults.update(kw)
    return DelegationRecord(**defaults)


class TestModuleAccessors:
    def test_wallet_returns_singleton(self):
        from clawmes.services.wallet import get_wallet_service

        assert svc._wallet() is get_wallet_service()

    def test_rpc_returns_singleton(self):
        from clawmes.services.rpc import get_rpc_service

        assert svc._rpc() is get_rpc_service()


class TestResolve:
    def test_resolve_delegator_connected(self, monkeypatch):
        _patch(monkeypatch, wallet=_FakeWallet(address="0xabc", connected=True))
        assert resolve_delegator() == "0xabc"

    def test_resolve_delegator_disconnected(self, monkeypatch):
        _patch(monkeypatch, wallet=_FakeWallet(connected=False))
        assert resolve_delegator() is None

    def test_resolve_delegate_existing(self, monkeypatch):
        _patch(monkeypatch, agent=_FakeAgentStore(info=True))
        assert resolve_delegate() == _AGENT_ADDR

    def test_resolve_delegate_creates(self, monkeypatch):
        _patch(monkeypatch, agent=_FakeAgentStore(info=False))
        assert resolve_delegate() == _AGENT_ADDR


class TestPrepare:
    def test_prepare_ok(self, monkeypatch):
        _patch(monkeypatch)
        compiled = prepare_delegation(DelegationSpec(native_per_call_wei=10**17), chain_id=8453)
        assert compiled.delegation.delegator == _DELEGATOR
        assert compiled.delegation.delegate == _AGENT_ADDR

    def test_prepare_no_wallet(self, monkeypatch):
        _patch(monkeypatch, wallet=_FakeWallet(connected=False))
        with pytest.raises(DelegationError, match="no wallet connected"):
            prepare_delegation(DelegationSpec(native_per_call_wei=1))

    def test_prepare_compile_error(self, monkeypatch):
        _patch(monkeypatch)
        with pytest.raises(DelegationError, match="unrestricted"):
            prepare_delegation(DelegationSpec())


class TestSign:
    def test_sign_ok_and_prefixes(self, monkeypatch):
        mode = _FakeMode(sign="ab" * 65)  # no 0x prefix from signer
        _patch(monkeypatch, wallet=_FakeWallet(mode=mode))
        compiled = prepare_delegation(DelegationSpec(native_per_call_wei=1))
        signed = sign_delegation(compiled.delegation, 8453)
        assert signed.signature.startswith("0x")

    def test_sign_no_mode(self, monkeypatch):
        _patch(monkeypatch, wallet=_FakeWallet(mode=None))
        compiled_delegation = prepare_delegation(DelegationSpec(native_per_call_wei=1)).delegation
        with pytest.raises(DelegationError, match="no wallet connected to sign"):
            sign_delegation(compiled_delegation, 8453)

    def test_sign_raises(self, monkeypatch):
        _patch(monkeypatch, wallet=_FakeWallet(mode=_FakeMode(raises=True)))
        u = prepare_delegation(DelegationSpec(native_per_call_wei=1)).delegation
        with pytest.raises(DelegationError, match="signing failed"):
            sign_delegation(u, 8453)


class TestStore:
    def test_store_reads_hash(self, monkeypatch):
        rpc = _FakeRpc()
        rpc.call_result = "0x" + "ab" * 32
        _patch(monkeypatch, rpc=rpc)
        rec = store_delegation("r1", _signed(), 8453, policy_name="p", tools=("transfer",))
        assert rec.hash == "0x" + "ab" * 32
        assert get_delegation_store().load("r1") is not None

    def test_store_hash_unavailable(self, monkeypatch):
        rpc = _FakeRpc()
        rpc.call_raises = RuntimeError("no rpc")
        _patch(monkeypatch, rpc=rpc)
        rec = store_delegation("r1", _signed(), 8453)
        assert rec.hash == "0x"

    def test_store_short_hash_ignored(self, monkeypatch):
        rpc = _FakeRpc()
        rpc.call_result = "0x12"  # too short
        _patch(monkeypatch, rpc=rpc)
        assert store_delegation("r1", _signed(), 8453).hash == "0x"


class TestRedeem:
    def test_redeem_success(self, monkeypatch):
        rpc = _FakeRpc()
        _patch(monkeypatch, rpc=rpc)
        rec = _record()
        result = redeem(rec, ExecutionAction("0x" + "11" * 20, 10**17, "0x"))
        assert result.tx_hash == "0xsentraw"
        assert result.chain_id == 8453
        assert rec.status == "active"  # promoted
        assert rpc.sent_raw  # broadcast happened

    def test_redeem_not_redeemable(self, monkeypatch):
        _patch(monkeypatch)
        with pytest.raises(DelegationError, match="revoked"):
            redeem(_record(status="revoked"), ExecutionAction("0x" + "11" * 20, 0, "0x"))

    def test_redeem_no_endpoint(self, monkeypatch):
        rpc = _FakeRpc()
        rpc.endpoints = set()
        _patch(monkeypatch, rpc=rpc)
        with pytest.raises(DelegationError, match="no RPC endpoint"):
            redeem(_record(), ExecutionAction("0x" + "11" * 20, 0, "0x"))

    def test_redeem_agent_locked(self, monkeypatch):
        _patch(monkeypatch, agent=_FakeAgentStore(raises=True))
        with pytest.raises(DelegationError, match="locked"):
            redeem(_record(), ExecutionAction("0x" + "11" * 20, 0, "0x"))

    def test_redeem_simulation_revert_decoded(self, monkeypatch):
        rpc = _FakeRpc()
        rpc.call_raises = RuntimeError("execution reverted 0x155ff427")
        _patch(monkeypatch, rpc=rpc)
        with pytest.raises(DelegationError, match="signature verification failed"):
            redeem(_record(), ExecutionAction("0x" + "11" * 20, 0, "0x"))

    def test_redeem_generic_revert(self, monkeypatch):
        rpc = _FakeRpc()
        rpc.call_raises = RuntimeError("boom")
        _patch(monkeypatch, rpc=rpc)
        with pytest.raises(DelegationError, match="reverted: boom"):
            redeem(_record(), ExecutionAction("0x" + "11" * 20, 0, "0x"))

    def test_redeem_broadcast_failure(self, monkeypatch):
        rpc = _FakeRpc()

        def _boom(raw, chain_id):
            raise RuntimeError("mempool full")

        rpc.send_raw_transaction = _boom
        _patch(monkeypatch, rpc=rpc)
        with pytest.raises(DelegationError, match="broadcast failed"):
            redeem(_record(), ExecutionAction("0x" + "11" * 20, 0, "0x"))

    def test_redeem_uses_permissions_context_for_7715(self, monkeypatch):
        rpc = _FakeRpc()
        _patch(monkeypatch, rpc=rpc)
        rec = _record(kind="eip7715", permissions_context="0x" + "00" * 64)
        result = redeem(rec, ExecutionAction("0x" + "11" * 20, 0, "0x"))
        assert result.tx_hash == "0xsentraw"

    def test_redeem_estimate_gas_fallback(self, monkeypatch):
        rpc = _FakeRpc()

        def _boom(**kw):
            raise RuntimeError("estimate failed")

        rpc.estimate_gas = _boom
        _patch(monkeypatch, rpc=rpc)
        result = redeem(_record(), ExecutionAction("0x" + "11" * 20, 0, "0x"))
        assert result.tx_hash == "0xsentraw"

    def test_redeem_derives_agent_addr_when_none(self, monkeypatch):
        # agent.address() returns None → _send_agent_tx derives it from the key.
        rpc = _FakeRpc()
        agent = _FakeAgentStore(addr=None)
        _patch(monkeypatch, rpc=rpc, agent=agent)
        result = redeem(_record(), ExecutionAction("0x" + "11" * 20, 0, "0x"))
        assert result.tx_hash == "0xsentraw"


class TestRevoke:
    def test_revoke_onchain(self, monkeypatch):
        mode = _FakeMode(tx="0xrevoke")
        _patch(monkeypatch, wallet=_FakeWallet(mode=mode))
        rec = _record()
        tx = revoke(rec)
        assert tx == "0xrevoke"
        assert rec.status == "revoked"

    def test_revoke_local_only_no_mode(self, monkeypatch):
        _patch(monkeypatch, wallet=_FakeWallet(mode=None))
        rec = _record()
        assert revoke(rec) is None
        assert rec.status == "revoked"

    def test_revoke_onchain_failure_degrades(self, monkeypatch):
        _patch(monkeypatch, wallet=_FakeWallet(mode=_FakeMode(raises=True)))
        rec = _record()
        assert revoke(rec) is None
        assert rec.status == "revoked"


class TestRefreshStatus:
    def test_refresh_active(self, monkeypatch):
        rpc = _FakeRpc()
        rpc.call_result = "0x" + "00" * 32  # not disabled
        _patch(monkeypatch, rpc=rpc)
        rec = _record(hash="0x" + "ab" * 32)
        refresh_status(rec)
        assert rec.status == "active"

    def test_refresh_revoked(self, monkeypatch):
        rpc = _FakeRpc()
        rpc.call_result = "0x" + "00" * 31 + "01"  # disabled == true
        _patch(monkeypatch, rpc=rpc)
        rec = _record(hash="0x" + "ab" * 32)
        refresh_status(rec)
        assert rec.status == "revoked"

    def test_refresh_skips_when_no_hash(self, monkeypatch):
        _patch(monkeypatch)
        rec = _record(hash="0x")
        assert refresh_status(rec).status == "signed"

    def test_refresh_skips_when_revoked(self, monkeypatch):
        _patch(monkeypatch)
        rec = _record(status="revoked", hash="0x" + "ab" * 32)
        assert refresh_status(rec).status == "revoked"

    def test_refresh_rpc_error_keeps_status(self, monkeypatch):
        rpc = _FakeRpc()
        rpc.call_raises = RuntimeError("down")
        _patch(monkeypatch, rpc=rpc)
        rec = _record(hash="0x" + "ab" * 32)
        assert refresh_status(rec).status == "signed"


class Test7715Request:
    def test_native_and_erc20(self):
        from clawmes.delegation.compiler import Erc20Limit

        spec = DelegationSpec(
            native_cap_wei=10**18,
            erc20=[Erc20Limit(token="0x" + "aa" * 20, max_amount=100, period_seconds=86400)],
        )
        req = build_7715_request(spec, 8453, signer="0x" + "55" * 20, expiry=123)
        assert req["method"] == "wallet_requestExecutionPermissions"
        params = req["params"][0]
        assert params["chainId"] == "0x2105"
        types_ = {p["type"] for p in params["permissions"]}
        assert types_ == {"native-token-transfer", "erc20-token-periodic"}

    def test_per_call_only(self):
        req = build_7715_request(
            DelegationSpec(native_per_call_wei=5), 1, signer="0x" + "55" * 20, expiry=1
        )
        assert req["params"][0]["permissions"][0]["data"]["allowance"] == "0x5"


class TestUpgrade7702:
    def test_upgrade_requires_local_mode(self, monkeypatch):
        _patch(monkeypatch, wallet=_FakeWallet(mode=_FakeMode()))
        with pytest.raises(DelegationError, match="local-key wallet mode"):
            upgrade_eoa_7702(chain_id=8453)

    def test_upgrade_unsupported_chain(self, monkeypatch):
        _patch(monkeypatch)
        with pytest.raises(DelegationError, match="not supported"):
            upgrade_eoa_7702(chain_id=999999)

    def test_upgrade_success(self, monkeypatch):
        from clawmes.wallet.local_key import LocalKeyMode

        mode = LocalKeyMode(password_cache_seconds=300)
        mode.connect(password="pw-abcdefgh", generate=True)
        rpc = _FakeRpc()
        _patch(monkeypatch, wallet=_FakeWallet(mode=mode), rpc=rpc)
        tx = upgrade_eoa_7702(chain_id=8453)
        assert tx == "0xsentraw"

    def test_upgrade_no_endpoint(self, monkeypatch):
        from clawmes.wallet.local_key import LocalKeyMode

        mode = LocalKeyMode(password_cache_seconds=300)
        mode.connect(password="pw-abcdefgh", generate=True)
        rpc = _FakeRpc()
        rpc.endpoints = set()
        _patch(monkeypatch, wallet=_FakeWallet(mode=mode), rpc=rpc)
        with pytest.raises(DelegationError, match="no RPC endpoint"):
            upgrade_eoa_7702(chain_id=8453)

    def test_upgrade_key_locked(self, monkeypatch):
        from clawmes.wallet.local_key import LocalKeyMode

        mode = LocalKeyMode(password_cache_seconds=0)  # no cache → derive raises
        mode.connect(password="pw-abcdefgh", generate=True)
        _patch(monkeypatch, wallet=_FakeWallet(mode=mode))
        with pytest.raises(DelegationError, match="could not access local key"):
            upgrade_eoa_7702(chain_id=8453)

    def test_upgrade_broadcast_failure(self, monkeypatch):
        from clawmes.wallet.local_key import LocalKeyMode

        mode = LocalKeyMode(password_cache_seconds=300)
        mode.connect(password="pw-abcdefgh", generate=True)
        rpc = _FakeRpc()

        def _boom(raw, chain_id):
            raise RuntimeError("nope")

        rpc.send_raw_transaction = _boom
        _patch(monkeypatch, wallet=_FakeWallet(mode=mode), rpc=rpc)
        with pytest.raises(DelegationError, match="upgrade tx failed"):
            upgrade_eoa_7702(chain_id=8453)
