"""Tests for clawmes.delegation.store."""

from __future__ import annotations

import json

import pytest

from clawmes.delegation.store import (
    DelegationStore,
    delegations_dir,
    get_delegation_store,
    record_from_dict,
    record_to_dict,
)
from clawmes.delegation.types import (
    ROOT_AUTHORITY,
    Caveat,
    DelegationRecord,
    SignedDelegation,
)


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    import clawmes.delegation.store as store_mod

    monkeypatch.setattr(store_mod, "_instance", None)


def _record(record_id: str = "pol-1", salt: int = 42) -> DelegationRecord:
    signed = SignedDelegation(
        delegate="0x" + "22" * 20,
        delegator="0x" + "33" * 20,
        authority=ROOT_AUTHORITY,
        caveats=(Caveat(enforcer="0x" + "44" * 20, terms="0x64", args="0x"),),
        salt=salt,
        signature="0x" + "aa" * 65,
    )
    return DelegationRecord(
        id=record_id, chain_id=8453, delegation=signed, policy_name="pol", tools=("transfer",)
    )


class TestSerialization:
    def test_roundtrip(self):
        rec = _record()
        data = record_to_dict(rec)
        back = record_from_dict(data)
        assert back.delegation.salt == rec.delegation.salt
        assert back.delegation.caveats[0].terms == "0x64"
        assert back.tools == ("transfer",)

    def test_salt_stored_as_hex(self):
        data = record_to_dict(_record(salt=255))
        assert data["delegation"]["salt"] == "0xff"

    def test_decode_int_salt(self):
        # Backward-tolerant: accept an int salt too.
        data = record_to_dict(_record())
        data["delegation"]["salt"] = 42
        assert record_from_dict(data).delegation.salt == 42


class TestStore:
    def test_save_and_load(self):
        store = DelegationStore()
        rec = _record()
        store.save(rec)
        assert rec.created_at  # stamped on save
        loaded = store.load("pol-1")
        assert loaded is not None
        assert loaded.delegation.signature == rec.delegation.signature

    def test_load_missing(self):
        assert DelegationStore().load("nope") is None

    def test_load_from_disk_after_cache_reset(self):
        store = DelegationStore()
        store.save(_record())
        store.reset()
        assert store.load("pol-1") is not None

    def test_has(self):
        store = DelegationStore()
        assert not store.has("pol-1")
        store.save(_record())
        assert store.has("pol-1")
        store.reset()
        assert store.has("pol-1")  # on disk

    def test_delete(self):
        store = DelegationStore()
        store.save(_record())
        assert store.delete("pol-1") is True
        assert store.load("pol-1") is None
        assert store.delete("pol-1") is False

    def test_delete_oserror_returns_false(self, monkeypatch):
        store = DelegationStore()
        store.save(_record())

        def _boom(self):
            raise OSError("locked")

        monkeypatch.setattr("pathlib.Path.unlink", _boom)
        assert store.delete("pol-1") is False

    def test_list_records(self):
        store = DelegationStore()
        store.save(_record("a"))
        store.save(_record("b"))
        ids = {r.id for r in store.list_records()}
        assert ids == {"a", "b"}

    def test_list_includes_cache_only(self):
        store = DelegationStore()
        store.save(_record("a"))
        # Inject a cache-only record (no file) to hit the merge branch.
        store._cache["ghost"] = _record("ghost")
        (delegations_dir() / "ghost.json").unlink(missing_ok=True)
        ids = {r.id for r in store.list_records()}
        assert "ghost" in ids and "a" in ids

    def test_safe_id_sanitizes(self):
        store = DelegationStore()
        rec = _record("weird/../id!")
        store.save(rec)
        # Reload through a fresh store (disk) — sanitized filename resolves.
        store.reset()
        assert store.load("weird/../id!") is not None


class TestCorruption:
    def test_load_quarantines_bad_file(self, tmp_path):
        store = DelegationStore()
        path = delegations_dir() / "bad.json"
        path.write_text("{ not json", encoding="utf-8")
        assert store.load("bad") is None
        # Original renamed to <stem>.corrupt.* (with_suffix replaces .json)
        assert not path.exists()
        assert list(delegations_dir().glob("bad.corrupt.*"))

    def test_list_skips_bad_file(self):
        store = DelegationStore()
        store.save(_record("good"))
        (delegations_dir() / "bad.json").write_text("nope", encoding="utf-8")
        ids = {r.id for r in store.list_records()}
        assert ids == {"good"}

    def test_missing_required_key_quarantined(self):
        store = DelegationStore()
        path = delegations_dir() / "partial.json"
        path.write_text(json.dumps({"id": "partial"}), encoding="utf-8")
        assert store.load("partial") is None

    def test_quarantine_rename_failure_tolerated(self, monkeypatch):
        store = DelegationStore()
        path = delegations_dir() / "bad.json"
        path.write_text("{ not json", encoding="utf-8")

        def _boom(self, target):
            raise OSError("cannot rename")

        monkeypatch.setattr("pathlib.Path.rename", _boom)
        # Quarantine fails but load still returns None without raising.
        assert store.load("bad") is None


class TestSingleton:
    def test_singleton_identity(self):
        assert get_delegation_store() is get_delegation_store()
