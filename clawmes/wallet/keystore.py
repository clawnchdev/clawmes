"""Local-key keystore — scrypt + AES-256-GCM + macOS Keychain.

Encrypts a BIP-39 mnemonic at rest. Storage layout:

  blob = {
    "version": 1,
    "address": "0x...",
    "salt":      "<hex>",   # 32 bytes for scrypt
    "nonce":     "<hex>",   # 12 bytes for AES-GCM
    "ciphertext": "<hex>",  # AES-256-GCM(scrypt(password, salt), nonce, mnemonic) + 16-byte tag
  }

Persistence preference:

  1. macOS Keychain via the ``keyring`` library (service=
     :data:`KEYRING_SERVICE`, account=address). Survives restarts;
     the OS encrypts the secret at rest a second time.
  2. Encrypted file fallback at
     ``${HERMES_HOME}/clawmes/wallet/keystore.bin`` (mode 0600).
     Used when keyring is unavailable (Linux without a keyring
     daemon, CI, etc.).

The mnemonic is the only secret that ever lives at rest. Once
decrypted, the eth-account derived private key is held in memory
just long enough to sign and is then dropped (Python doesn't give
us a guaranteed secure-zero, but we minimize surface).

Crypto primitives:

  * **scrypt** for password-based key derivation. Parameters chosen
    per OWASP 2024 recommendation: N = 2^17 (131,072 iterations),
    r = 8, p = 1, dkLen = 32 bytes. ~100ms derivation on 2024 CPU —
    fast enough for interactive flows, slow enough to bottleneck
    brute-force at $1M+ per password attempted.
  * **AES-256-GCM** for authenticated encryption. NIST-approved AEAD
    construction; protects both confidentiality and integrity in a
    single pass. 12-byte random nonce per encryption (no nonce reuse
    risk because we never reuse keys), 16-byte authentication tag
    appended to ciphertext.
  * **os.urandom** for both salt and nonce — uses platform CSPRNG
    (``/dev/urandom`` on Unix, ``CryptGenRandom`` on Windows).

Why custom format instead of eth-account.Account.encrypt():

  ``eth-account``'s encrypt() implements the Web3 Secret Storage spec
  for *32-byte private keys*. We need to encrypt a *variable-length
  mnemonic* (16–32 bytes of entropy + word-level padding). The Web3
  spec doesn't cover mnemonic storage — every wallet (MetaMask,
  Rabby, etc.) rolls its own mnemonic-encryption layer for the same
  reason. We use the same primitives the spec uses (scrypt + AES)
  with parameters that match or exceed the spec's recommendations.

The format is verified against an independent AES-GCM implementation
in tests (see ``tests/wallet/test_keystore.py::TestCrossValidation``)
to catch any subtle deviation from the construction described above.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from clawmes.lib.logger import logger_for
from clawmes.lib.paths import wallet_dir

_log = logger_for("wallet.keystore")

KEYRING_SERVICE = "clawmes"

# scrypt parameters
SCRYPT_N = 2**17
SCRYPT_R = 8
SCRYPT_P = 1
DKLEN = 32


class KeystoreError(RuntimeError):
    """Raised on encryption / decryption / storage failures."""


@dataclass(frozen=True)
class EncryptedKeystore:
    version: int
    address: str
    salt_hex: str
    nonce_hex: str
    ciphertext_hex: str

    def to_json(self) -> str:
        return json.dumps(
            {
                "version": self.version,
                "address": self.address,
                "salt": self.salt_hex,
                "nonce": self.nonce_hex,
                "ciphertext": self.ciphertext_hex,
            }
        )

    @classmethod
    def from_json(cls, blob: str) -> EncryptedKeystore:
        try:
            data = json.loads(blob)
        except json.JSONDecodeError as exc:
            raise KeystoreError(f"keystore blob is not JSON: {exc}") from exc
        try:
            return cls(
                version=int(data["version"]),
                address=str(data["address"]),
                salt_hex=str(data["salt"]),
                nonce_hex=str(data["nonce"]),
                ciphertext_hex=str(data["ciphertext"]),
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise KeystoreError(f"keystore blob malformed: {exc}") from exc


# --- crypto primitives ---------------------------------------------------


def _derive_key(password: str, salt: bytes) -> bytes:
    """scrypt(password, salt) → 32-byte key."""
    from Crypto.Protocol.KDF import scrypt

    return scrypt(  # type: ignore[no-any-return]
        password.encode("utf-8"),
        salt,
        DKLEN,
        N=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
    )


def encrypt_mnemonic(mnemonic: str, password: str, address: str) -> EncryptedKeystore:
    """AES-256-GCM encrypt a mnemonic with a password-derived key."""
    from Crypto.Cipher import AES

    salt = os.urandom(32)
    nonce = os.urandom(12)
    key = _derive_key(password, salt)
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    ciphertext, tag = cipher.encrypt_and_digest(mnemonic.encode("utf-8"))
    # tag is appended to ciphertext for storage; decrypt splits it back.
    return EncryptedKeystore(
        version=1,
        address=address,
        salt_hex=salt.hex(),
        nonce_hex=nonce.hex(),
        ciphertext_hex=(ciphertext + tag).hex(),
    )


def decrypt_mnemonic(keystore: EncryptedKeystore, password: str) -> str:
    """Inverse of :func:`encrypt_mnemonic`. Raises on wrong password."""
    from Crypto.Cipher import AES

    if keystore.version != 1:
        raise KeystoreError(f"unsupported keystore version {keystore.version}")
    salt = bytes.fromhex(keystore.salt_hex)
    nonce = bytes.fromhex(keystore.nonce_hex)
    blob = bytes.fromhex(keystore.ciphertext_hex)
    if len(blob) < 16:
        raise KeystoreError("ciphertext too short for AES-GCM tag")
    ciphertext, tag = blob[:-16], blob[-16:]

    key = _derive_key(password, salt)
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    try:
        plain = cipher.decrypt_and_verify(ciphertext, tag)
    except (ValueError, KeyError) as exc:
        raise KeystoreError("wrong password (or keystore corrupted)") from exc
    return plain.decode("utf-8")


def rotate_password(
    keystore: EncryptedKeystore,
    old_password: str,
    new_password: str,
) -> EncryptedKeystore:
    """Re-encrypt ``keystore`` under ``new_password``.

    Decrypts with ``old_password``, generates a fresh salt and nonce,
    and re-encrypts under ``new_password``. The plaintext mnemonic is
    held in a local variable for the minimum window — we don't try to
    securely-zero it (Python doesn't reliably support that) but we
    drop the reference as soon as the new keystore is built.

    Returns the new :class:`EncryptedKeystore`. Caller is responsible
    for persisting via :func:`save_keystore`.
    """
    mnemonic = decrypt_mnemonic(keystore, old_password)
    try:
        return encrypt_mnemonic(mnemonic, new_password, keystore.address)
    finally:
        # Best-effort: remove our reference so the bytes can be GC'd.
        # Real secure-zero would require a C extension; this is the
        # closest pure-Python approximation.
        del mnemonic


# --- storage --------------------------------------------------------------


def _file_path() -> Path:
    return wallet_dir() / "keystore.bin"


def save_keystore(keystore: EncryptedKeystore) -> str:
    """Persist the keystore. Always writes the encrypted blob to file
    (so reload without knowing the address works); additionally writes
    to the OS keyring when available (extra OS-level encryption layer).

    Returns ``"keyring+file"`` if both succeeded, ``"file"`` if only
    the file write succeeded.
    """
    blob = keystore.to_json()

    # Always write file first — it's the canonical source for reload.
    path = _file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(blob, encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    _log.info("keystore saved to file %s for %s", path, keystore.address)

    # Then keyring as a defense-in-depth layer.
    try:
        import keyring

        keyring.set_password(KEYRING_SERVICE, keystore.address, blob)
        _log.info("keystore also saved to keyring for %s", keystore.address)
        return "keyring+file"
    except Exception as exc:  # noqa: BLE001 — any keyring backend can fail
        _log.warning("keyring unavailable (%s); keystore is on file only", exc)
        return "file"


def load_keystore(address: str | None = None) -> EncryptedKeystore | None:
    """Load the keystore.

    Pass ``address`` to read a specific keychain entry; without it
    we read the file (which is the canonical source — keyring is
    a defense-in-depth layer over the same blob).
    """
    if address is not None:
        try:
            import keyring

            blob = keyring.get_password(KEYRING_SERVICE, address)
            if blob:
                return EncryptedKeystore.from_json(blob)
        except Exception:  # noqa: BLE001
            _log.exception("keyring read failed; trying file")

    path = _file_path()
    if not path.exists():
        return None
    try:
        return EncryptedKeystore.from_json(path.read_text(encoding="utf-8"))
    except KeystoreError:
        _log.exception("keystore file at %s is corrupt", path)
        return None


def delete_keystore(address: str) -> None:
    """Remove keyring + file entries for ``address``."""
    try:
        import keyring

        keyring.delete_password(KEYRING_SERVICE, address)
    except Exception:  # noqa: BLE001
        _log.debug("keyring delete failed or no entry for %s", address)

    path = _file_path()
    if path.exists():
        try:
            path.unlink()
        except OSError:
            _log.exception("file keystore unlink failed")


# --- mnemonic + address generation ---------------------------------------


def generate_mnemonic(*, strength_bits: int = 256) -> str:
    """Generate a fresh BIP-39 mnemonic.

    256 bits → 24 words; 128 bits → 12 words. Default 24 (more entropy
    is better; the extra 12 words cost the user nothing if the mnemonic
    lives encrypted in the keystore).
    """
    from mnemonic import Mnemonic

    return Mnemonic("english").generate(strength=strength_bits)


def address_from_mnemonic(mnemonic: str, *, account_index: int = 0) -> tuple[str, str]:
    """Derive (address, private_key) for the standard Ethereum BIP-44 path
    ``m/44'/60'/0'/0/<account_index>``.

    Returns hex-prefixed strings. The private key is the secret;
    callers should drop the reference as soon as signing is done.
    """
    from eth_account import Account

    Account.enable_unaudited_hdwallet_features()
    path = f"m/44'/60'/0'/0/{account_index}"
    acct = Account.from_mnemonic(mnemonic, account_path=path)
    return acct.address, acct.key.hex()
