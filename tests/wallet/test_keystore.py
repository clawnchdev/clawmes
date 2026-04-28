"""Tests for clawmes.wallet.keystore."""

from __future__ import annotations

import sys
import types

import pytest

from clawmes.wallet.keystore import (
    KEYRING_SERVICE,
    EncryptedKeystore,
    KeystoreError,
    address_from_mnemonic,
    decrypt_mnemonic,
    delete_keystore,
    encrypt_mnemonic,
    generate_mnemonic,
    load_keystore,
    save_keystore,
)

TEST_MNEMONIC = (
    "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"
)
TEST_ADDRESS = "0x9858EfFD232B4033E47d90003D41EC34EcaEda94"


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """HERMES_HOME isolation + in-memory fake keyring."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    store: dict = {}

    class FakeKeyring:
        @staticmethod
        def set_password(service, account, value):
            store[(service, account)] = value

        @staticmethod
        def get_password(service, account):
            return store.get((service, account))

        @staticmethod
        def delete_password(service, account):
            store.pop((service, account), None)

    fake_module = types.ModuleType("keyring")
    fake_module.set_password = FakeKeyring.set_password
    fake_module.get_password = FakeKeyring.get_password
    fake_module.delete_password = FakeKeyring.delete_password
    monkeypatch.setitem(sys.modules, "keyring", fake_module)
    return store


# --- Mnemonic generation -------------------------------------------------


class TestGenerateMnemonic:
    def test_default_24_words(self):
        m = generate_mnemonic()
        assert len(m.split()) == 24

    def test_128_bits_12_words(self):
        m = generate_mnemonic(strength_bits=128)
        assert len(m.split()) == 12


# --- Address derivation --------------------------------------------------


class TestAddressFromMnemonic:
    def test_known_test_vector(self):
        # 'abandon... about' is a well-known test mnemonic; account 0
        # produces a deterministic address.
        addr, key = address_from_mnemonic(TEST_MNEMONIC)
        assert addr == TEST_ADDRESS
        # eth_account.Account.key.hex() returns no 0x prefix; the
        # length is exactly 64 hex chars (32 bytes).
        assert len(key) == 64

    def test_account_index_changes_address(self):
        addr0, _ = address_from_mnemonic(TEST_MNEMONIC, account_index=0)
        addr1, _ = address_from_mnemonic(TEST_MNEMONIC, account_index=1)
        assert addr0 != addr1


# --- Encrypt / decrypt round-trip ----------------------------------------


class TestRoundTrip:
    def test_basic(self):
        ks_blob = encrypt_mnemonic(TEST_MNEMONIC, "hunter2", TEST_ADDRESS)
        recovered = decrypt_mnemonic(ks_blob, "hunter2")
        assert recovered == TEST_MNEMONIC

    def test_blob_carries_address(self):
        b = encrypt_mnemonic(TEST_MNEMONIC, "pw", TEST_ADDRESS)
        assert b.address == TEST_ADDRESS
        assert b.version == 1

    def test_wrong_password_raises(self):
        b = encrypt_mnemonic(TEST_MNEMONIC, "right", TEST_ADDRESS)
        with pytest.raises(KeystoreError, match="wrong password"):
            decrypt_mnemonic(b, "wrong")

    def test_unsupported_version(self):
        b = EncryptedKeystore(
            version=99,
            address=TEST_ADDRESS,
            salt_hex="ab" * 32,
            nonce_hex="cd" * 12,
            ciphertext_hex="ef" * 50,
        )
        with pytest.raises(KeystoreError, match="unsupported keystore version"):
            decrypt_mnemonic(b, "anything")

    def test_truncated_ciphertext(self):
        b = EncryptedKeystore(
            version=1,
            address=TEST_ADDRESS,
            salt_hex="ab" * 32,
            nonce_hex="cd" * 12,
            ciphertext_hex="ef" * 4,  # too short for tag
        )
        with pytest.raises(KeystoreError, match="too short"):
            decrypt_mnemonic(b, "anything")


# --- JSON serialization --------------------------------------------------


class TestJsonRoundTrip:
    def test_serialize_deserialize(self):
        original = encrypt_mnemonic(TEST_MNEMONIC, "pw", TEST_ADDRESS)
        blob = original.to_json()
        recovered = EncryptedKeystore.from_json(blob)
        assert recovered == original

    def test_invalid_json(self):
        with pytest.raises(KeystoreError, match="not JSON"):
            EncryptedKeystore.from_json("not-json-at-all")

    def test_missing_field(self):
        import json

        bad = json.dumps({"version": 1, "address": "x"})  # missing salt etc.
        with pytest.raises(KeystoreError, match="malformed"):
            EncryptedKeystore.from_json(bad)

    def test_wrong_field_type(self):
        import json

        bad = json.dumps(
            {
                "version": "not-int",
                "address": "x",
                "salt": "ab",
                "nonce": "cd",
                "ciphertext": "ef",
            }
        )
        with pytest.raises(KeystoreError, match="malformed"):
            EncryptedKeystore.from_json(bad)


# --- Save / load ---------------------------------------------------------


class TestSaveLoad:
    def test_save_writes_file_and_keyring(self, tmp_path, _isolate):
        b = encrypt_mnemonic(TEST_MNEMONIC, "pw", TEST_ADDRESS)
        result = save_keystore(b)
        assert result == "keyring+file"
        # File exists
        assert (tmp_path / "clawmes" / "wallet" / "keystore.bin").exists()
        # Keyring has the entry
        assert (KEYRING_SERVICE, TEST_ADDRESS) in _isolate

    def test_save_keyring_failure_still_writes_file(self, tmp_path, monkeypatch):
        # Patch keyring.set_password to raise
        fake = types.ModuleType("keyring")

        def boom(*a, **kw):
            raise RuntimeError("simulated keyring failure")

        fake.set_password = boom
        fake.get_password = lambda *a, **kw: None
        fake.delete_password = lambda *a, **kw: None
        monkeypatch.setitem(sys.modules, "keyring", fake)

        b = encrypt_mnemonic(TEST_MNEMONIC, "pw", TEST_ADDRESS)
        result = save_keystore(b)
        assert result == "file"
        assert (tmp_path / "clawmes" / "wallet" / "keystore.bin").exists()

    def test_load_missing_returns_none(self):
        assert load_keystore() is None

    def test_load_by_address_uses_keyring(self, _isolate):
        b = encrypt_mnemonic(TEST_MNEMONIC, "pw", TEST_ADDRESS)
        save_keystore(b)
        loaded = load_keystore(address=TEST_ADDRESS)
        assert loaded is not None
        assert loaded.address == TEST_ADDRESS

    def test_load_keyring_failure_falls_through_to_file(self, monkeypatch):
        b = encrypt_mnemonic(TEST_MNEMONIC, "pw", TEST_ADDRESS)
        save_keystore(b)

        # Now break keyring read
        fake = types.ModuleType("keyring")
        fake.set_password = lambda *a, **kw: None

        def boom(*a, **kw):
            raise RuntimeError("simulated")

        fake.get_password = boom
        fake.delete_password = lambda *a, **kw: None
        monkeypatch.setitem(sys.modules, "keyring", fake)

        loaded = load_keystore(address=TEST_ADDRESS)
        assert loaded is not None
        assert loaded.address == TEST_ADDRESS

    def test_load_corrupt_file_returns_none(self, tmp_path):
        path = tmp_path / "clawmes" / "wallet" / "keystore.bin"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not-json", encoding="utf-8")
        assert load_keystore() is None


class TestDeleteKeystore:
    def test_removes_keyring_and_file(self, tmp_path, _isolate):
        b = encrypt_mnemonic(TEST_MNEMONIC, "pw", TEST_ADDRESS)
        save_keystore(b)

        delete_keystore(TEST_ADDRESS)
        path = tmp_path / "clawmes" / "wallet" / "keystore.bin"
        assert not path.exists()
        assert (KEYRING_SERVICE, TEST_ADDRESS) not in _isolate

    def test_delete_when_missing(self):
        # No-op when nothing's there — must not raise
        delete_keystore("0x" + "a" * 40)

    def test_delete_keyring_failure_still_removes_file(self, tmp_path, monkeypatch):
        b = encrypt_mnemonic(TEST_MNEMONIC, "pw", TEST_ADDRESS)
        save_keystore(b)

        fake = types.ModuleType("keyring")
        fake.set_password = lambda *a, **kw: None
        fake.get_password = lambda *a, **kw: None

        def boom(*a, **kw):
            raise RuntimeError("simulated")

        fake.delete_password = boom
        monkeypatch.setitem(sys.modules, "keyring", fake)

        delete_keystore(TEST_ADDRESS)
        path = tmp_path / "clawmes" / "wallet" / "keystore.bin"
        assert not path.exists()

    def test_delete_file_unlink_failure_swallowed(self, tmp_path, monkeypatch, _isolate):
        b = encrypt_mnemonic(TEST_MNEMONIC, "pw", TEST_ADDRESS)
        save_keystore(b)

        # Patch Path.unlink to raise
        from pathlib import Path

        original_unlink = Path.unlink

        def boom(self, *a, **kw):
            if self.name == "keystore.bin":
                raise OSError("simulated unlink failure")
            return original_unlink(self, *a, **kw)

        monkeypatch.setattr(Path, "unlink", boom)
        # Must not raise
        delete_keystore(TEST_ADDRESS)
