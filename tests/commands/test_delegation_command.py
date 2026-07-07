"""Tests for clawmes.commands.delegation."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from clawmes.commands import delegation as cmd
from clawmes.delegation.store import get_delegation_store
from clawmes.delegation.types import (
    ROOT_AUTHORITY,
    Caveat,
    DelegationRecord,
    SignedDelegation,
)
from clawmes.policy import storage as policy_storage
from clawmes.policy.types import Policy

_DELEGATOR = "0x" + "33" * 20
_AGENT = "0x" + "22" * 20


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    import clawmes.delegation.store as store_mod

    monkeypatch.setattr(store_mod, "_instance", None)
    policy_storage.save_policies([])


def _store_record(record_id="pol", **kw):
    signed = SignedDelegation(
        delegate=_AGENT,
        delegator=_DELEGATOR,
        authority=ROOT_AUTHORITY,
        caveats=(Caveat("0x" + "44" * 20, "0x64", "0x"),),
        salt=1,
        signature="0x" + "cd" * 65,
    )
    defaults = dict(id=record_id, chain_id=8453, delegation=signed, status="signed")
    defaults.update(kw)
    rec = DelegationRecord(**defaults)
    get_delegation_store().save(rec)
    return rec


class TestRegister:
    def test_registers_three_commands(self):
        recorded = []

        class Ctx:
            def register_command(self, **kw):
                recorded.append(kw["name"])

        cmd.register(Ctx())
        assert set(recorded) == {"delegate", "delegations", "revoke"}


class TestOverviewAndStatus:
    async def test_overview_empty(self):
        out = await cmd.handle_delegate("")
        assert "No delegations yet" in out

    async def test_overview_with_records(self):
        _store_record(policy_name="pol", expires_at="2025-06-22T00:00:00+00:00")
        out = await cmd.handle_delegate("")
        assert "pol" in out and "expires" in out

    async def test_status_empty(self):
        out = await cmd.handle_delegate("status")
        assert "No delegations found" in out

    async def test_status_lists(self, monkeypatch):
        _store_record(
            hash="0x" + "ab" * 32,
            tools=("transfer",),
            unmapped=("blocklist",),
            expires_at="2025-06-22T00:00:00+00:00",
        )
        monkeypatch.setattr(cmd, "refresh_status", lambda r: r)
        out = await cmd.handle_delegate("status")
        assert "pol" in out and "transfer" in out and "blocklist" in out and "expires" in out

    async def test_status_refresh_error_tolerated(self, monkeypatch):
        _store_record()

        def _boom(r):
            raise RuntimeError("rpc")

        monkeypatch.setattr(cmd, "refresh_status", _boom)
        out = await cmd.handle_delegate("status")
        assert "pol" in out

    async def test_delegations_alias(self):
        _store_record()
        out = await cmd.handle_delegations("")
        assert "Delegation status" in out

    async def test_show_by_id(self):
        _store_record(hash="0x" + "ab" * 32, expires_at="2025-06-22T00:00:00+00:00")
        out = await cmd.handle_delegate("pol")
        assert "Delegation pol" in out and "expires" in out

    async def test_show_unknown(self):
        out = await cmd.handle_delegate("nope")
        assert "No delegation" in out


class TestChains:
    async def test_chains(self):
        out = await cmd.handle_delegate("chains")
        assert "Base" in out and "testnet" in out and "CLAWMES_RPC_84532" in out


class TestCreate:
    async def test_create_inline_spec(self, monkeypatch):
        compiled = SimpleNamespace(
            delegation=SimpleNamespace(delegate=_AGENT, delegator=_DELEGATOR, caveats=()),
            mapped=["native per-call"],
            unmapped=[],
            warnings=[],
            expires_at="2025-06-22T00:00:00+00:00",
        )
        monkeypatch.setattr(cmd, "prepare_delegation", lambda spec, chain_id: compiled)
        monkeypatch.setattr(cmd, "format_compilation", lambda c, cid: "PREVIEW")
        signed = _store_record().delegation
        monkeypatch.setattr(cmd, "sign_delegation", lambda d, cid: signed)
        monkeypatch.setattr(
            cmd,
            "store_delegation",
            lambda *a, **k: _store_record(record_id=a[0]),
        )
        monkeypatch.setattr(
            cmd,
            "get_agent_key_store",
            lambda: SimpleNamespace(info=lambda: SimpleNamespace(address=_AGENT)),
        )
        out = await cmd.handle_delegate("create --per-call 0.1 --expiry 7d")
        assert "PREVIEW" in out and "Signed and stored" in out and _AGENT in out

    async def test_create_from_policy(self, monkeypatch):
        policy_storage.save_policies(
            [
                Policy(
                    name="daily",
                    decision="confirm",
                    max_amount_wei=10**17,
                    applies_to_tools=("transfer",),
                )
            ]
        )
        compiled = SimpleNamespace(
            delegation=SimpleNamespace(delegate=_AGENT, delegator=_DELEGATOR, caveats=()),
            mapped=[],
            unmapped=[],
            warnings=[],
            expires_at="",
        )
        monkeypatch.setattr(cmd, "prepare_delegation", lambda spec, chain_id: compiled)
        monkeypatch.setattr(cmd, "format_compilation", lambda c, cid: "PREVIEW")
        monkeypatch.setattr(
            cmd, "sign_delegation", lambda d, cid: _store_record("daily").delegation
        )
        monkeypatch.setattr(cmd, "store_delegation", lambda *a, **k: _store_record(record_id=a[0]))
        monkeypatch.setattr(cmd, "get_agent_key_store", lambda: SimpleNamespace(info=lambda: None))
        out = await cmd.handle_delegate("create daily")
        assert "Signed and stored" in out

    async def test_create_unknown_policy(self):
        out = await cmd.handle_delegate("create ghost-policy")
        assert "not found" in out

    async def test_create_bad_chain(self):
        out = await cmd.handle_delegate("create --per-call 0.1 --chain 999999")
        assert "not supported" in out

    async def test_create_prepare_error(self, monkeypatch):
        from clawmes.delegation.service import DelegationError

        def _boom(spec, chain_id):
            raise DelegationError("no wallet")

        monkeypatch.setattr(cmd, "prepare_delegation", _boom)
        out = await cmd.handle_delegate("create --per-call 0.1")
        assert "no wallet" in out

    async def test_create_sign_error(self, monkeypatch):
        from clawmes.delegation.service import DelegationError

        compiled = SimpleNamespace(
            delegation=SimpleNamespace(delegate=_AGENT, delegator=_DELEGATOR, caveats=()),
            mapped=[],
            unmapped=[],
            warnings=[],
            expires_at="",
        )
        monkeypatch.setattr(cmd, "prepare_delegation", lambda s, chain_id: compiled)
        monkeypatch.setattr(cmd, "format_compilation", lambda c, cid: "PREVIEW")

        def _boom(d, cid):
            raise DelegationError("rejected")

        monkeypatch.setattr(cmd, "sign_delegation", _boom)
        out = await cmd.handle_delegate("create --per-call 0.1")
        assert "rejected" in out

    async def test_create_flag_missing_value(self):
        out = await cmd.handle_delegate("create --per-call")
        assert "needs a value" in out

    async def test_create_bad_period(self):
        out = await cmd.handle_delegate("create --cap 1 --period fortnightly")
        assert "period must be one of" in out

    async def test_create_erc20_flag(self, monkeypatch):
        compiled = SimpleNamespace(
            delegation=SimpleNamespace(delegate=_AGENT, delegator=_DELEGATOR, caveats=()),
            mapped=[],
            unmapped=[],
            warnings=[],
            expires_at="",
        )
        monkeypatch.setattr(cmd, "prepare_delegation", lambda s, chain_id: compiled)
        monkeypatch.setattr(cmd, "format_compilation", lambda c, cid: "PREVIEW")
        monkeypatch.setattr(cmd, "sign_delegation", lambda d, cid: _store_record().delegation)
        monkeypatch.setattr(cmd, "store_delegation", lambda *a, **k: _store_record(record_id=a[0]))
        monkeypatch.setattr(cmd, "get_agent_key_store", lambda: SimpleNamespace(info=lambda: None))
        out = await cmd.handle_delegate(
            "create --cap 1 --period daily "
            "--erc20 0x833589fcd6edb6e08f4c7c32d4f71b54bda02913:100:6:daily "
            "--targets 0x1111111111111111111111111111111111111111 --calls 5"
        )
        assert "Signed and stored" in out

    async def test_create_bad_erc20_format(self):
        out = await cmd.handle_delegate("create --erc20 justtoken")
        assert "format" in out

    async def test_create_bad_erc20_amount(self):
        out = await cmd.handle_delegate("create --erc20 0x" + "a" * 40 + ":notnum:6")
        assert "bad --erc20" in out

    async def test_create_bad_erc20_period(self):
        out = await cmd.handle_delegate("create --erc20 0x" + "a" * 40 + ":100:6:decade")
        assert "period must be one of" in out

    async def test_create_bad_duration(self):
        out = await cmd.handle_delegate("create --per-call 0.1 --expiry banana")
        assert "bad duration" in out

    async def test_create_bad_per_call(self):
        out = await cmd.handle_delegate("create --per-call not-a-number")
        assert "bad per-call" in out

    async def test_create_bad_calls_int(self):
        out = await cmd.handle_delegate("create --calls abc")
        assert "must be an integer" in out


class TestRevoke:
    async def test_revoke_onchain(self, monkeypatch):
        _store_record()
        monkeypatch.setattr(cmd, "svc_revoke", lambda r: "0xrevoketx")
        out = await cmd.handle_delegate("revoke pol")
        assert "0xrevoketx" in out

    async def test_revoke_local_only(self, monkeypatch):
        _store_record()
        monkeypatch.setattr(cmd, "svc_revoke", lambda r: None)
        out = await cmd.handle_delegate("revoke pol")
        assert "locally" in out

    async def test_revoke_unknown(self):
        out = await cmd.handle_delegate("revoke nope")
        assert "No delegation" in out

    async def test_revoke_already_revoked(self):
        _store_record(status="revoked")
        out = await cmd.handle_delegate("revoke pol")
        assert "already revoked" in out

    async def test_revoke_no_arg(self):
        out = await cmd.handle_delegate("revoke")
        assert "Usage" in out

    async def test_revoke_alias(self, monkeypatch):
        _store_record()
        monkeypatch.setattr(cmd, "svc_revoke", lambda r: "0xtx")
        out = await cmd.handle_revoke("pol")
        assert "0xtx" in out

    async def test_revoke_alias_no_arg(self):
        out = await cmd.handle_revoke("")
        assert "Usage" in out

    async def test_revoke_all(self, monkeypatch):
        _store_record("a")
        _store_record("b")
        monkeypatch.setattr(cmd, "svc_revoke", lambda r: "0xtx")
        out = await cmd.handle_delegate("revoke-all")
        assert "Revoking 2" in out

    async def test_revoke_all_none(self):
        out = await cmd.handle_delegate("revoke-all")
        assert "No active delegations" in out

    async def test_revoke_all_local(self, monkeypatch):
        _store_record("a")
        monkeypatch.setattr(cmd, "svc_revoke", lambda r: None)
        out = await cmd.handle_delegate("revoke-all")
        assert "local only" in out


class TestAgent:
    async def test_agent_show_existing(self, monkeypatch):
        monkeypatch.setattr(
            cmd,
            "get_agent_key_store",
            lambda: SimpleNamespace(
                info=lambda: SimpleNamespace(
                    address=_AGENT, storage="keyring", created_at="2026-01-01"
                )
            ),
        )
        out = await cmd.handle_delegate("agent")
        assert _AGENT in out and "keyring" in out

    async def test_agent_create(self, monkeypatch):
        state = {"info": None}

        def _create(passphrase=None):
            state["info"] = SimpleNamespace(address=_AGENT, storage="file")
            return state["info"]

        monkeypatch.setattr(
            cmd,
            "get_agent_key_store",
            lambda: SimpleNamespace(info=lambda: state["info"], create=_create),
        )
        out = await cmd.handle_delegate("agent my-passphrase")
        assert "Generated agent" in out

    async def test_agent_create_error(self, monkeypatch):
        from clawmes.delegation.agent_key import AgentKeyError

        def _create(passphrase=None):
            raise AgentKeyError("cannot persist")

        monkeypatch.setattr(
            cmd,
            "get_agent_key_store",
            lambda: SimpleNamespace(info=lambda: None, create=_create),
        )
        out = await cmd.handle_delegate("agent")
        assert "cannot persist" in out


class TestUpgrade:
    async def test_upgrade_success(self, monkeypatch):
        monkeypatch.setattr(cmd, "upgrade_eoa_7702", lambda chain_id: "0xupgradetx")
        out = await cmd.handle_delegate("upgrade")
        assert "0xupgradetx" in out

    async def test_upgrade_error(self, monkeypatch):
        from clawmes.delegation.service import DelegationError

        def _boom(chain_id):
            raise DelegationError("local-key only")

        monkeypatch.setattr(cmd, "upgrade_eoa_7702", _boom)
        out = await cmd.handle_delegate("upgrade 8453")
        assert "local-key only" in out


class TestPermissions:
    async def test_permissions_no_policy(self):
        out = await cmd.handle_delegate("permissions ghost")
        assert "not found" in out

    async def test_permissions_no_arg(self):
        out = await cmd.handle_delegate("permissions")
        assert "Usage" in out

    async def test_permissions_no_wallet(self, monkeypatch):
        policy_storage.save_policies([Policy(name="p", decision="confirm", max_amount_wei=1)])
        monkeypatch.setattr(
            "clawmes.services.wallet.get_wallet_service",
            lambda: SimpleNamespace(
                state=SimpleNamespace(connected=False, address=None, chain_id=8453),
                active_mode=None,
            ),
        )
        out = await cmd.handle_delegate("permissions p")
        assert "Connect a wallet" in out

    async def test_permissions_mode_unsupported(self, monkeypatch):
        policy_storage.save_policies([Policy(name="p", decision="confirm", max_amount_wei=1)])
        monkeypatch.setattr(
            "clawmes.services.wallet.get_wallet_service",
            lambda: SimpleNamespace(
                state=SimpleNamespace(connected=True, address="0x" + "55" * 20, chain_id=8453),
                active_mode=SimpleNamespace(),  # no request_execution_permissions
            ),
        )
        out = await cmd.handle_delegate("permissions p")
        assert "doesn't support ERC-7715" in out

    async def test_permissions_success(self, monkeypatch):
        policy_storage.save_policies([Policy(name="p", decision="confirm", max_amount_wei=10**17)])
        mode = SimpleNamespace(request_execution_permissions=lambda params: [{"context": "0xctx"}])
        monkeypatch.setattr(
            "clawmes.services.wallet.get_wallet_service",
            lambda: SimpleNamespace(
                state=SimpleNamespace(connected=True, address="0x" + "55" * 20, chain_id=8453),
                active_mode=mode,
            ),
        )
        out = await cmd.handle_delegate("permissions p")
        assert "Requested ERC-7715" in out

    async def test_permissions_request_error(self, monkeypatch):
        policy_storage.save_policies([Policy(name="p", decision="confirm", max_amount_wei=1)])

        def _boom(params):
            raise RuntimeError("rejected on phone")

        mode = SimpleNamespace(request_execution_permissions=_boom)
        monkeypatch.setattr(
            "clawmes.services.wallet.get_wallet_service",
            lambda: SimpleNamespace(
                state=SimpleNamespace(connected=True, address="0x" + "55" * 20, chain_id=8453),
                active_mode=mode,
            ),
        )
        out = await cmd.handle_delegate("permissions p")
        assert "request failed" in out


class TestSpecToolsHelper:
    def test_spec_tools_empty_for_no_name(self):
        assert cmd.spec_tools("") == ()

    def test_spec_tools_missing_policy(self):
        assert cmd.spec_tools("ghost") == ()

    def test_spec_tools_reads_policy(self):
        policy_storage.save_policies(
            [Policy(name="p", decision="confirm", applies_to_tools=("transfer", "nft"))]
        )
        assert cmd.spec_tools("p") == ("transfer", "nft")


class TestDurationParsing:
    def test_seconds_default(self):
        assert cmd._parse_duration(None, 42) == 42

    def test_units(self):
        assert cmd._parse_duration("2h", 0) == 7200
        assert cmd._parse_duration("1w", 0) == 604800
        assert cmd._parse_duration("30", 0) == 30  # raw seconds

    def test_bad(self):
        with pytest.raises(cmd._CreateError):
            cmd._parse_duration("banana", 0)

    def test_bad_numeric_with_unit(self):
        with pytest.raises(cmd._CreateError):
            cmd._parse_duration("xd", 0)
