"""Agent (delegate) key management.

The *delegate* is the agent's own EOA — separate from the user's wallet. It
signs and pays gas for ``redeemDelegations`` transactions, and (in the raw
EIP-7710 path) it is the account the delegation is granted *to*. Keeping it
separate from the user's key is the whole point: even a fully-compromised
agent can only act within the on-chain caveats the user signed.

The private key is a raw 32-byte secp256k1 key (not a mnemonic), stored
encrypted with the same primitives as the wallet keystore (scrypt +
AES-256-GCM) plus the OS keyring as a defense-in-depth layer. It is
generated on demand the first time a delegation is created.

Unlock for autonomous redemption:
  * OS keyring (no passphrase needed) — the common desktop path, or
  * ``CLAWMES_AGENT_PASSPHRASE`` env → decrypts the file, or
  * an explicit passphrase passed by a command.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from clawmes.lib.logger import logger_for
from clawmes.lib.paths import state_dir
from clawmes.wallet.keystore import (
    EncryptedKeystore,
    KeystoreError,
    _derive_key,
)

_log = logger_for("delegation.agent_key")

KEYRING_SERVICE = "clawmes-agent"
_ENV_PASSPHRASE = "CLAWMES_AGENT_PASSPHRASE"


class AgentKeyError(RuntimeError):
    """Raised on agent-key generation / storage / unlock failure."""


@dataclass(frozen=True)
class AgentKeyInfo:
    """Public metadata about the agent key (never the secret)."""

    address: str
    created_at: str
    storage: str  # "keyring+file" | "file"


def _dir() -> Path:
    return state_dir("delegations", "agent")


def _keystore_path() -> Path:
    return _dir() / "agent_key.json"


def _meta_path() -> Path:
    return _dir() / "agent_meta.json"


# ─── crypto (raw-key variant of the wallet keystore) ────────────────────


def _encrypt_key(private_key_hex: str, password: str, address: str) -> EncryptedKeystore:
    from Crypto.Cipher import AES

    salt = os.urandom(32)
    nonce = os.urandom(12)
    key = _derive_key(password, salt)
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
    plaintext = private_key_hex.removeprefix("0x").encode("utf-8")
    ciphertext, tag = cipher.encrypt_and_digest(plaintext)
    return EncryptedKeystore(
        version=1,
        address=address,
        salt_hex=salt.hex(),
        nonce_hex=nonce.hex(),
        ciphertext_hex=(ciphertext + tag).hex(),
    )


def _decrypt_key(keystore: EncryptedKeystore, password: str) -> str:
    from Crypto.Cipher import AES

    if keystore.version != 1:
        raise AgentKeyError(f"unsupported agent keystore version {keystore.version}")
    salt = bytes.fromhex(keystore.salt_hex)
    nonce = bytes.fromhex(keystore.nonce_hex)
    blob = bytes.fromhex(keystore.ciphertext_hex)
    if len(blob) < 16:
        raise AgentKeyError("agent ciphertext too short for AES-GCM tag")
    ciphertext, tag = blob[:-16], blob[-16:]
    cipher = AES.new(_derive_key(password, salt), AES.MODE_GCM, nonce=nonce)
    try:
        plain = cipher.decrypt_and_verify(ciphertext, tag)
    except (ValueError, KeyError) as exc:
        raise AgentKeyError("wrong agent passphrase (or keystore corrupted)") from exc
    return "0x" + plain.decode("utf-8")


class AgentKeyStore:
    """Generate, persist and unlock the agent delegate key."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._cached_key: str | None = None
        self._cached_info: AgentKeyInfo | None = None

    # --- lifecycle ----------------------------------------------------

    def exists(self) -> bool:
        with self._lock:
            return self._cached_info is not None or _meta_path().exists()

    def info(self) -> AgentKeyInfo | None:
        with self._lock:
            if self._cached_info is not None:
                return self._cached_info
            path = _meta_path()
            if not path.exists():
                return None
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                self._cached_info = AgentKeyInfo(
                    address=data["address"],
                    created_at=data.get("created_at", ""),
                    storage=data.get("storage", "file"),
                )
            except (OSError, ValueError, KeyError):
                _log.exception("agent meta unreadable")
                return None
            return self._cached_info

    def create(self, *, passphrase: str | None = None) -> AgentKeyInfo:
        """Generate a fresh agent key. No-op-ish if one already exists.

        Requires either the OS keyring OR a ``passphrase`` (≥8 chars) so the
        key can be persisted encrypted. Raises if neither is available.
        """
        if self.exists():
            existing = self.info()
            if existing is not None:
                return existing

        from eth_account import Account

        acct = Account.create(os.urandom(32))
        private_key_hex = acct.key.hex()
        address = acct.address
        storage = self._persist(address, private_key_hex, passphrase)

        info = AgentKeyInfo(
            address=address,
            created_at=datetime.now(tz=UTC).isoformat(),
            storage=storage,
        )
        with self._lock:
            self._cached_key = (
                private_key_hex if private_key_hex.startswith("0x") else "0x" + private_key_hex
            )
            self._cached_info = info
            _dir().mkdir(parents=True, exist_ok=True)
            _meta_path().write_text(
                json.dumps(
                    {"address": address, "created_at": info.created_at, "storage": storage},
                    indent=2,
                ),
                encoding="utf-8",
            )
            os.chmod(_meta_path(), 0o600)
        _log.info("generated agent key %s (storage=%s)", address, storage)
        return info

    def _persist(self, address: str, private_key_hex: str, passphrase: str | None) -> str:
        keyring_ok = False
        try:
            import keyring

            keyring.set_password(KEYRING_SERVICE, address, private_key_hex)
            keyring_ok = True
        except Exception as exc:  # noqa: BLE001 — any keyring backend can fail
            _log.warning("agent keyring store unavailable (%s)", exc)

        pw = passphrase or os.environ.get(_ENV_PASSPHRASE)
        file_ok = False
        if pw and len(pw) >= 8:
            keystore = _encrypt_key(private_key_hex, pw, address)
            _dir().mkdir(parents=True, exist_ok=True)
            path = _keystore_path()
            tmp = path.with_suffix(".tmp")
            tmp.write_text(keystore.to_json(), encoding="utf-8")
            os.chmod(tmp, 0o600)
            tmp.replace(path)
            file_ok = True

        if keyring_ok and file_ok:
            return "keyring+file"
        if keyring_ok:
            return "keyring"
        if file_ok:
            return "file"
        raise AgentKeyError(
            "cannot persist agent key: OS keyring unavailable and no passphrase "
            f"(≥8 chars) provided. Pass a passphrase or set {_ENV_PASSPHRASE}."
        )

    # --- unlock -------------------------------------------------------

    def load_private_key(self, *, passphrase: str | None = None) -> str:
        """Return the 0x-prefixed private key, unlocking as needed.

        Order: in-memory cache → OS keyring → encrypted file (passphrase from
        arg or ``CLAWMES_AGENT_PASSPHRASE``). Raises :class:`AgentKeyError`
        when the key can't be recovered.
        """
        with self._lock:
            if self._cached_key is not None:
                return self._cached_key

        info = self.info()
        if info is None:
            raise AgentKeyError("no agent key exists — create one first")

        # Keyring
        try:
            import keyring

            raw = keyring.get_password(KEYRING_SERVICE, info.address)
            if raw:
                key = raw if raw.startswith("0x") else "0x" + raw
                with self._lock:
                    self._cached_key = key
                return key
        except Exception as exc:  # noqa: BLE001
            _log.warning("agent keyring read failed (%s); trying file", exc)

        # Encrypted file
        pw = passphrase or os.environ.get(_ENV_PASSPHRASE)
        path = _keystore_path()
        if pw and path.exists():
            try:
                keystore = EncryptedKeystore.from_json(path.read_text(encoding="utf-8"))
            except KeystoreError as exc:
                raise AgentKeyError(f"agent keystore file unreadable: {exc}") from exc
            key = _decrypt_key(keystore, pw)
            with self._lock:
                self._cached_key = key
            return key

        raise AgentKeyError(
            "agent key is locked: OS keyring returned nothing and no passphrase "
            f"was available. Pass a passphrase or set {_ENV_PASSPHRASE}."
        )

    def address(self) -> str | None:
        info = self.info()
        return info.address if info else None

    def reset(self) -> None:
        with self._lock:
            self._cached_key = None
            self._cached_info = None


_instance: AgentKeyStore | None = None


def get_agent_key_store() -> AgentKeyStore:
    global _instance
    if _instance is None:
        _instance = AgentKeyStore()
    return _instance
