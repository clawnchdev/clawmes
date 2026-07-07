"""Tests for clawmes.delegation.agent_key.

The OS keyring is monkeypatched to an in-memory dict so tests are
deterministic on any platform (CI has no macOS Keychain).
"""

from __future__ import annotations

import sys
import types

import pytest
from eth_account import Account

from clawmes.delegation import agent_key as ak
from clawmes.delegation.agent_key import AgentKeyError, AgentKeyStore, get_agent_key_store


class _FakeKeyring:
    """Minimal in-memory keyring stand-in."""

    def __init__(self, *, fail: bool = False) -> None:
        self._store: dict[tuple[str, str], str] = {}
        self._fail = fail

    def set_password(self, service, name, value):
        if self._fail:
            raise RuntimeError("no backend")
        self._store[(service, name)] = value

    def get_password(self, service, name):
        if self._fail:
            raise RuntimeError("no backend")
        return self._store.get((service, name))


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("CLAWMES_AGENT_PASSPHRASE", raising=False)
    monkeypatch.setattr(ak, "_instance", None)


def _install_keyring(monkeypatch, keyring_obj):
    module = types.ModuleType("keyring")
    module.set_password = keyring_obj.set_password
    module.get_password = keyring_obj.get_password
    monkeypatch.setitem(sys.modules, "keyring", module)


class TestCreate:
    def test_create_with_keyring(self, monkeypatch):
        kr = _FakeKeyring()
        _install_keyring(monkeypatch, kr)
        store = AgentKeyStore()
        info = store.create()
        assert info.address.startswith("0x")
        assert "keyring" in info.storage
        assert store.exists()

    def test_create_with_file_only(self, monkeypatch):
        _install_keyring(monkeypatch, _FakeKeyring(fail=True))
        store = AgentKeyStore()
        info = store.create(passphrase="passphrase-1234")
        assert info.storage == "file"

    def test_create_requires_keyring_or_passphrase(self, monkeypatch):
        _install_keyring(monkeypatch, _FakeKeyring(fail=True))
        store = AgentKeyStore()
        with pytest.raises(AgentKeyError, match="cannot persist"):
            store.create()

    def test_create_env_passphrase(self, monkeypatch):
        _install_keyring(monkeypatch, _FakeKeyring(fail=True))
        monkeypatch.setenv("CLAWMES_AGENT_PASSPHRASE", "env-passphrase-1")
        store = AgentKeyStore()
        assert store.create().storage == "file"

    def test_create_idempotent(self, monkeypatch):
        _install_keyring(monkeypatch, _FakeKeyring())
        store = AgentKeyStore()
        first = store.create()
        second = store.create()
        assert first.address == second.address

    def test_create_keyring_and_file(self, monkeypatch):
        # Both keyring and a passphrase available → "keyring+file".
        _install_keyring(monkeypatch, _FakeKeyring())
        store = AgentKeyStore()
        info = store.create(passphrase="passphrase-1234")
        assert info.storage == "keyring+file"


class TestLoad:
    def test_load_from_cache(self, monkeypatch):
        _install_keyring(monkeypatch, _FakeKeyring())
        store = AgentKeyStore()
        info = store.create()
        key = store.load_private_key()
        assert Account.from_key(key).address == info.address

    def test_load_from_keyring_after_reset(self, monkeypatch):
        kr = _FakeKeyring()
        _install_keyring(monkeypatch, kr)
        store = AgentKeyStore()
        info = store.create()
        store.reset()
        key = store.load_private_key()
        assert Account.from_key(key).address == info.address

    def test_load_from_file_after_reset(self, monkeypatch):
        _install_keyring(monkeypatch, _FakeKeyring(fail=True))
        store = AgentKeyStore()
        info = store.create(passphrase="passphrase-1234")
        store.reset()
        key = store.load_private_key(passphrase="passphrase-1234")
        assert Account.from_key(key).address == info.address

    def test_load_wrong_passphrase(self, monkeypatch):
        _install_keyring(monkeypatch, _FakeKeyring(fail=True))
        store = AgentKeyStore()
        store.create(passphrase="passphrase-1234")
        store.reset()
        with pytest.raises(AgentKeyError, match="wrong agent passphrase"):
            store.load_private_key(passphrase="wrong-passphrase")

    def test_load_no_key_exists(self, monkeypatch):
        _install_keyring(monkeypatch, _FakeKeyring())
        store = AgentKeyStore()
        with pytest.raises(AgentKeyError, match="no agent key"):
            store.load_private_key()

    def test_load_locked_without_passphrase(self, monkeypatch):
        _install_keyring(monkeypatch, _FakeKeyring(fail=True))
        store = AgentKeyStore()
        store.create(passphrase="passphrase-1234")
        store.reset()
        with pytest.raises(AgentKeyError, match="locked"):
            store.load_private_key()


class TestInfoAndAddress:
    def test_info_from_disk(self, monkeypatch):
        _install_keyring(monkeypatch, _FakeKeyring())
        store = AgentKeyStore()
        info = store.create()
        store.reset()
        assert store.info().address == info.address
        assert store.address() == info.address

    def test_info_none_when_absent(self, monkeypatch):
        _install_keyring(monkeypatch, _FakeKeyring())
        assert AgentKeyStore().info() is None
        assert AgentKeyStore().address() is None

    def test_info_corrupt_meta(self, monkeypatch):
        _install_keyring(monkeypatch, _FakeKeyring())
        store = AgentKeyStore()
        store.create()
        store.reset()
        ak._meta_path().write_text("not json", encoding="utf-8")
        assert store.info() is None


class TestCryptoErrors:
    def test_unsupported_version(self, monkeypatch):
        _install_keyring(monkeypatch, _FakeKeyring(fail=True))
        store = AgentKeyStore()
        store.create(passphrase="passphrase-1234")
        # Corrupt the version in the stored keystore.
        import json

        path = ak._keystore_path()
        blob = json.loads(path.read_text())
        blob["version"] = 2
        path.write_text(json.dumps(blob), encoding="utf-8")
        store.reset()
        with pytest.raises(AgentKeyError, match="unsupported"):
            store.load_private_key(passphrase="passphrase-1234")

    def test_short_ciphertext(self, monkeypatch):
        _install_keyring(monkeypatch, _FakeKeyring(fail=True))
        store = AgentKeyStore()
        store.create(passphrase="passphrase-1234")
        import json

        path = ak._keystore_path()
        blob = json.loads(path.read_text())
        blob["ciphertext"] = "00"  # too short for a GCM tag
        path.write_text(json.dumps(blob), encoding="utf-8")
        store.reset()
        with pytest.raises(AgentKeyError, match="too short"):
            store.load_private_key(passphrase="passphrase-1234")

    def test_unreadable_keystore_file(self, monkeypatch):
        _install_keyring(monkeypatch, _FakeKeyring(fail=True))
        store = AgentKeyStore()
        store.create(passphrase="passphrase-1234")
        ak._keystore_path().write_text("not json at all", encoding="utf-8")
        store.reset()
        with pytest.raises(AgentKeyError, match="unreadable"):
            store.load_private_key(passphrase="passphrase-1234")


class TestSingleton:
    def test_singleton(self):
        assert get_agent_key_store() is get_agent_key_store()
