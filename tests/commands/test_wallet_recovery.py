"""Tests for /create_wallet, /recover, /export_wallet, /wallet_backup."""

from __future__ import annotations

import pytest

from clawmes.commands import wallet_recovery as wr
from clawmes.services import wallet as wallet_svc
from clawmes.wallet.keystore import (
    KeystoreError,
    address_from_mnemonic,
    encrypt_mnemonic,
    save_keystore,
)

# A valid BIP-39 mnemonic — the well-known abandon×11 + about test
# vector. Safe to commit; never holds real funds.
_TEST_MNEMONIC_12 = (
    "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about"
)
_TEST_MNEMONIC_24 = (
    "abandon abandon abandon abandon abandon abandon "
    "abandon abandon abandon abandon abandon abandon "
    "abandon abandon abandon abandon abandon abandon "
    "abandon abandon abandon abandon abandon art"
)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(wallet_svc, "_instance", None)


def _seed_keystore(password: str = "hunter2", mnemonic: str = _TEST_MNEMONIC_12) -> str:
    """Persist an encrypted keystore on disk; return the derived address."""
    address, _privkey = address_from_mnemonic(mnemonic)
    ks = encrypt_mnemonic(mnemonic, password, address)
    save_keystore(ks)
    return address


# --- /create_wallet -----------------------------------------------------


class TestCreateWallet:
    async def test_usage_message(self):
        out = await wr.handle_create_wallet("")
        assert "Usage:" in out
        assert "/create_wallet <password>" in out

    async def test_refuses_when_keystore_exists(self):
        _seed_keystore()
        out = await wr.handle_create_wallet("new-password")
        assert "Refusing to overwrite" in out
        assert "/export_wallet" in out

    async def test_success(self):
        out = await wr.handle_create_wallet("solid-pw-789")
        assert "New wallet created" in out
        assert "Address:" in out
        # The mnemonic line — 24 words by default
        words = [ln for ln in out.splitlines() if len(ln.split()) == 24]
        assert words, "Expected a 24-word mnemonic line in the output"
        assert "DO NOT SHARE" in out

    async def test_keystore_error_during_create(self, monkeypatch):
        from clawmes.services.wallet import get_wallet_service

        svc = get_wallet_service()

        def _raise(password, **kw):
            raise KeystoreError("derivation failed")

        monkeypatch.setattr(svc, "connect_local_key", _raise)
        out = await wr.handle_create_wallet("password")
        assert "Wallet creation failed" in out
        assert "derivation failed" in out


# --- /recover ----------------------------------------------------------


class TestRecover:
    async def test_usage_message(self):
        out = await wr.handle_recover("")
        assert "Usage:" in out
        assert "/recover <password> | <mnemonic" in out

    async def test_missing_pipe(self):
        out = await wr.handle_recover("just-a-password")
        assert "Missing the '|' separator" in out

    async def test_empty_password(self):
        out = await wr.handle_recover(f" | {_TEST_MNEMONIC_12}")
        assert "Password is empty" in out

    async def test_empty_mnemonic(self):
        out = await wr.handle_recover("hunter2 | ")
        assert "Mnemonic is empty" in out

    async def test_bad_word_count(self):
        out = await wr.handle_recover("hunter2 | abandon abandon abandon")
        assert "3 words" in out
        assert "12 or 24" in out

    async def test_success_12_words(self):
        out = await wr.handle_recover(f"hunter2 | {_TEST_MNEMONIC_12}")
        assert "Wallet recovered" in out
        assert "Address:" in out

    async def test_success_24_words(self):
        out = await wr.handle_recover(f"hunter2 | {_TEST_MNEMONIC_24}")
        assert "Wallet recovered" in out

    async def test_keystore_error_during_recover(self, monkeypatch):
        from clawmes.services.wallet import get_wallet_service

        svc = get_wallet_service()

        def _raise(password, **kw):
            raise KeystoreError("seed validation failed")

        monkeypatch.setattr(svc, "connect_local_key", _raise)
        out = await wr.handle_recover(f"hunter2 | {_TEST_MNEMONIC_12}")
        assert "Recovery failed" in out
        assert "seed validation failed" in out


# --- /export_wallet ----------------------------------------------------


class TestExportWallet:
    async def test_usage_message(self):
        out = await wr.handle_export_wallet("")
        assert "Usage:" in out
        assert "/export_wallet <password>" in out

    async def test_no_keystore(self):
        out = await wr.handle_export_wallet("hunter2")
        assert "No local keystore found" in out
        assert "/create_wallet" in out

    async def test_wrong_password(self):
        _seed_keystore(password="hunter2")
        out = await wr.handle_export_wallet("wrong-pw")
        assert "Decrypt failed" in out

    async def test_success_returns_mnemonic(self):
        _seed_keystore(password="hunter2", mnemonic=_TEST_MNEMONIC_12)
        out = await wr.handle_export_wallet("hunter2")
        assert "DO NOT SHARE" in out
        assert _TEST_MNEMONIC_12 in out
        assert "Address:" in out

    async def test_address_derivation_failure_falls_back(self, monkeypatch):
        _seed_keystore(password="hunter2", mnemonic=_TEST_MNEMONIC_12)

        # Force address_from_mnemonic to blow up to cover the fallback
        # branch where we use the stored keystore.address.
        monkeypatch.setattr(
            wr,
            "address_from_mnemonic",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("derivation broken")),
        )
        out = await wr.handle_export_wallet("hunter2")
        # Even with derivation broken, the mnemonic still surfaces and we
        # fall back to the keystore-stored address.
        assert _TEST_MNEMONIC_12 in out
        assert "Address:" in out


# --- /wallet_backup ----------------------------------------------------


class TestWalletBackup:
    async def test_no_keystore(self, tmp_path):
        out = await wr.handle_wallet_backup("")
        assert "No local keystore found" in out
        assert "/create_wallet" in out

    async def test_default_path(self):
        from clawmes.lib.paths import wallet_dir

        _seed_keystore()
        out = await wr.handle_wallet_backup("")
        assert "Encrypted keystore backed up" in out
        # The default-named file should exist
        backups = [
            p
            for p in wallet_dir().iterdir()
            if p.name.startswith("keystore-backup-") and p.name.endswith(".bin")
        ]
        assert len(backups) == 1

    async def test_explicit_file_path(self, tmp_path):
        _seed_keystore()
        custom = tmp_path / "custom-backup.bin"
        out = await wr.handle_wallet_backup(str(custom))
        assert "Encrypted keystore backed up" in out
        assert custom.exists()
        assert str(custom) in out

    async def test_explicit_dir_path(self, tmp_path):
        _seed_keystore()
        target_dir = tmp_path / "backup_dir"
        target_dir.mkdir()
        out = await wr.handle_wallet_backup(str(target_dir))
        assert "Encrypted keystore backed up" in out
        # Should have created a default-named file inside the dir.
        files = list(target_dir.iterdir())
        assert len(files) == 1
        assert files[0].name.startswith("keystore-backup-")

    async def test_copy_failure_returns_error(self, monkeypatch):
        _seed_keystore()

        def _explode(src, dst):
            raise OSError("disk full")

        monkeypatch.setattr(wr.shutil, "copy2", _explode)
        out = await wr.handle_wallet_backup("")
        assert "Backup failed" in out
        assert "disk full" in out

    async def test_default_backup_name_format(self):
        # Cover the helper directly.
        name = wr._default_backup_name()
        assert name.startswith("keystore-backup-")
        assert name.endswith(".bin")
        # Timestamp segment is 15 characters: YYYYMMDDTHHMMSS
        mid = name.removeprefix("keystore-backup-").removesuffix(".bin")
        assert len(mid) == 15
        assert mid[8] == "T"


# --- registration ------------------------------------------------------


class TestRegister:
    def test_registers_four_commands(self):
        captured = []

        class FakeCtx:
            def register_command(self, **kw):
                captured.append(kw["name"])

        wr.register(FakeCtx())
        assert set(captured) == {
            "create_wallet",
            "recover",
            "export_wallet",
            "wallet_backup",
        }
