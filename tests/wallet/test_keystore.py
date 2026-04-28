"""Tests for clawmes.wallet.keystore."""

from __future__ import annotations

import sys
import types

import pytest

from clawmes.wallet.keystore import (
    DKLEN,
    KEYRING_SERVICE,
    SCRYPT_N,
    SCRYPT_P,
    SCRYPT_R,
    EncryptedKeystore,
    KeystoreError,
    address_from_mnemonic,
    decrypt_mnemonic,
    delete_keystore,
    encrypt_mnemonic,
    generate_mnemonic,
    load_keystore,
    rotate_password,
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


class TestKdfCrossValidation:
    """Verify our scrypt KDF matches the Python stdlib's reference
    implementation. If the parameters or encoding ever drift, this
    fails immediately rather than silently producing keystores that
    can't be decrypted by other tools using the same scheme."""

    def test_scrypt_matches_stdlib(self):
        import hashlib

        from clawmes.wallet.keystore import _derive_key

        password = "hunter2"
        salt = b"\x42" * 32

        # Our impl (uses pycryptodome's scrypt internally)
        ours = _derive_key(password, salt)

        # Stdlib reference
        # maxmem must be set for n=2^17 r=8 — defaults to 32 MB which
        # is exactly the requirement at these parameters; bumping for
        # safety.
        reference = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=SCRYPT_N,
            r=SCRYPT_R,
            p=SCRYPT_P,
            dklen=DKLEN,
            maxmem=2**30,
        )
        assert ours == reference

    def test_scrypt_parameters_meet_owasp(self):
        # OWASP 2024 recommendation: N >= 2^17, r >= 8, p >= 1
        assert SCRYPT_N >= 2**17
        assert SCRYPT_R >= 8
        assert SCRYPT_P >= 1
        # 32-byte derived key for AES-256
        assert DKLEN == 32


class TestEncryptionInvariants:
    """Properties that MUST hold for the construction to be safe."""

    def test_unique_salt_per_encryption(self):
        # Reusing salt with the same password produces the same key,
        # which combined with reused nonce would let an attacker
        # XOR ciphertexts. Verify each encrypt() generates fresh salt.
        salts = {encrypt_mnemonic(TEST_MNEMONIC, "pw", TEST_ADDRESS).salt_hex for _ in range(20)}
        assert len(salts) == 20  # all unique

    def test_unique_nonce_per_encryption(self):
        # AES-GCM is catastrophically broken under nonce reuse with
        # the same key. With fresh salts (and thus fresh keys) reuse
        # is fine, but we still want random nonces as defense in depth.
        nonces = {encrypt_mnemonic(TEST_MNEMONIC, "pw", TEST_ADDRESS).nonce_hex for _ in range(20)}
        assert len(nonces) == 20

    def test_salt_size_32_bytes(self):
        b = encrypt_mnemonic(TEST_MNEMONIC, "pw", TEST_ADDRESS)
        assert len(bytes.fromhex(b.salt_hex)) == 32

    def test_nonce_size_12_bytes(self):
        b = encrypt_mnemonic(TEST_MNEMONIC, "pw", TEST_ADDRESS)
        assert len(bytes.fromhex(b.nonce_hex)) == 12

    def test_tag_authenticates_ciphertext(self):
        """Tampered ciphertext must fail authentication."""
        b = encrypt_mnemonic(TEST_MNEMONIC, "pw", TEST_ADDRESS)
        ct = bytearray(bytes.fromhex(b.ciphertext_hex))
        # Flip a byte in the middle of the ciphertext (not the tag)
        ct[5] ^= 0xFF
        tampered = EncryptedKeystore(
            version=b.version,
            address=b.address,
            salt_hex=b.salt_hex,
            nonce_hex=b.nonce_hex,
            ciphertext_hex=ct.hex(),
        )
        with pytest.raises(KeystoreError, match="wrong password"):
            decrypt_mnemonic(tampered, "pw")

    def test_tag_authenticates_tag_itself(self):
        """Tampered tag must fail authentication."""
        b = encrypt_mnemonic(TEST_MNEMONIC, "pw", TEST_ADDRESS)
        ct = bytearray(bytes.fromhex(b.ciphertext_hex))
        # Flip the last byte (in the tag)
        ct[-1] ^= 0xFF
        tampered = EncryptedKeystore(
            version=b.version,
            address=b.address,
            salt_hex=b.salt_hex,
            nonce_hex=b.nonce_hex,
            ciphertext_hex=ct.hex(),
        )
        with pytest.raises(KeystoreError, match="wrong password"):
            decrypt_mnemonic(tampered, "pw")

    def test_tampered_nonce_fails_authentication(self):
        b = encrypt_mnemonic(TEST_MNEMONIC, "pw", TEST_ADDRESS)
        bad_nonce = bytearray(bytes.fromhex(b.nonce_hex))
        bad_nonce[0] ^= 0xFF
        tampered = EncryptedKeystore(
            version=b.version,
            address=b.address,
            salt_hex=b.salt_hex,
            nonce_hex=bad_nonce.hex(),
            ciphertext_hex=b.ciphertext_hex,
        )
        with pytest.raises(KeystoreError, match="wrong password"):
            decrypt_mnemonic(tampered, "pw")

    def test_tampered_salt_fails_authentication(self):
        b = encrypt_mnemonic(TEST_MNEMONIC, "pw", TEST_ADDRESS)
        bad_salt = bytearray(bytes.fromhex(b.salt_hex))
        bad_salt[0] ^= 0xFF
        tampered = EncryptedKeystore(
            version=b.version,
            address=b.address,
            salt_hex=bad_salt.hex(),
            nonce_hex=b.nonce_hex,
            ciphertext_hex=b.ciphertext_hex,
        )
        # Different salt → different key → MAC fails
        with pytest.raises(KeystoreError, match="wrong password"):
            decrypt_mnemonic(tampered, "pw")

    def test_round_trip_short_mnemonic(self):
        # 12-word mnemonic — 128 bits of entropy
        twelve_word = "test " * 11 + "junk"
        b = encrypt_mnemonic(twelve_word, "pw", TEST_ADDRESS)
        assert decrypt_mnemonic(b, "pw") == twelve_word

    def test_round_trip_unicode_password(self):
        # Unicode passwords must survive UTF-8 encoding round-trip
        b = encrypt_mnemonic(TEST_MNEMONIC, "🔐 пароль 密码", TEST_ADDRESS)
        assert decrypt_mnemonic(b, "🔐 пароль 密码") == TEST_MNEMONIC


class TestRotatePassword:
    def test_basic_rotation(self):
        old = encrypt_mnemonic(TEST_MNEMONIC, "old-pw", TEST_ADDRESS)
        new = rotate_password(old, "old-pw", "new-pw")
        # New keystore decrypts with the new password
        assert decrypt_mnemonic(new, "new-pw") == TEST_MNEMONIC
        # And NOT with the old password
        with pytest.raises(KeystoreError):
            decrypt_mnemonic(new, "old-pw")
        # Original keystore still decrypts with old password
        assert decrypt_mnemonic(old, "old-pw") == TEST_MNEMONIC

    def test_rotation_uses_fresh_salt_and_nonce(self):
        old = encrypt_mnemonic(TEST_MNEMONIC, "old-pw", TEST_ADDRESS)
        new = rotate_password(old, "old-pw", "new-pw")
        assert new.salt_hex != old.salt_hex
        assert new.nonce_hex != old.nonce_hex

    def test_rotation_preserves_address(self):
        old = encrypt_mnemonic(TEST_MNEMONIC, "old-pw", TEST_ADDRESS)
        new = rotate_password(old, "old-pw", "new-pw")
        assert new.address == TEST_ADDRESS

    def test_rotation_with_wrong_old_password_raises(self):
        old = encrypt_mnemonic(TEST_MNEMONIC, "real-pw", TEST_ADDRESS)
        with pytest.raises(KeystoreError):
            rotate_password(old, "wrong-pw", "new-pw")

    def test_rotation_to_same_password_works(self):
        # No technical reason to forbid this — produces a fresh salt+nonce
        old = encrypt_mnemonic(TEST_MNEMONIC, "pw", TEST_ADDRESS)
        new = rotate_password(old, "pw", "pw")
        assert decrypt_mnemonic(new, "pw") == TEST_MNEMONIC
        assert new.salt_hex != old.salt_hex


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
