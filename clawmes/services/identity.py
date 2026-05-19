"""Agent-identity service — ed25519 keypair + did:key encoding.

Gives clawmes' agent a verifiable cryptographic identity that is
independent of the connected wallet. This is the same identity model
that gitlawb and most decentralized agent protocols use: a single
ed25519 keypair, the public key encoded as a ``did:key`` identifier
per the W3C DID spec.

Why a separate identity from the wallet:

  * The wallet (Ethereum address) is for signing on-chain
    transactions — high-value operations.
  * The DID is for signing protocol messages (PRs, issue comments,
    MCP calls, capability delegations) — low-value but high-frequency.
  * Mixing the two would put the wallet's signing key on the hot
    path of every protocol request, which is exactly the surface
    we don't want exposed to prompt-injection.

v1 scope is **in-memory only**. ``/identity create`` generates a
fresh keypair on every restart by design — the keypair has no
persistence layer yet. Two follow-up paths once persistence is
desirable:

  * Encrypted file (mirror the wallet keystore pattern).
  * Deterministic derivation from the wallet mnemonic (works only
    in local-key mode, but binds identity to the wallet without a
    second secret to manage).

The ``did:key`` encoding follows the W3C DID Method Key spec:
``did:key:z<base58btc(multicodec(0xed01) || pubkey_32_bytes)>``.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from clawmes.lib.logger import logger_for
from clawmes.services._base import Service

_log = logger_for("services.identity")

# Multicodec prefix for the ed25519 public-key type — two bytes.
# Documented at https://github.com/multiformats/multicodec/blob/master/table.csv
_ED25519_MULTICODEC_PREFIX = b"\xed\x01"

# SubjectPublicKeyInfo DER prefix for an ed25519 public key — algorithm
# OID 1.3.101.112, BIT STRING wrapper. pycryptodome's ECC.import_key
# accepts this DER form (it doesn't accept the raw 32-byte key
# directly). The 32-byte public key follows the prefix → 44 bytes total.
_ED25519_DER_PREFIX = b"\x30\x2a\x30\x05\x06\x03\x2b\x65\x70\x03\x21\x00"

# Base58btc alphabet per RFC draft-msporny-base58.
_BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def base58btc_encode(data: bytes) -> str:
    """Encode bytes using the base58btc alphabet.

    No external dependency — this is small enough to inline. Matches
    the IPFS / DID / Bitcoin convention.
    """
    if not data:
        return ""
    n = int.from_bytes(data, "big")
    out: list[str] = []
    while n > 0:
        n, rem = divmod(n, 58)
        out.append(_BASE58_ALPHABET[rem])
    # Preserve leading zero bytes as the '1' character.
    for byte in data:
        if byte == 0:
            out.append(_BASE58_ALPHABET[0])
        else:
            break
    return "".join(reversed(out))


def encode_did_key(pubkey_bytes: bytes) -> str:
    """Build the ``did:key:z…`` identifier for an ed25519 public key."""
    if len(pubkey_bytes) != 32:
        raise ValueError(f"ed25519 public key must be 32 bytes, got {len(pubkey_bytes)}")
    multicodec_payload = _ED25519_MULTICODEC_PREFIX + pubkey_bytes
    return f"did:key:z{base58btc_encode(multicodec_payload)}"


class IdentityService(Service):
    id = "clawmes.identity"

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._key: Any | None = None  # Crypto.PublicKey.ECC.EccKey, lazy-typed
        self._created_at: float | None = None

    def start(self) -> None:
        pass

    def stop(self) -> None:
        with self._lock:
            self._key = None
            self._created_at = None

    def has_identity(self) -> bool:
        with self._lock:
            return self._key is not None

    def generate(self) -> dict[str, Any]:
        """Generate a fresh ed25519 keypair and replace any existing one.

        Returns the public summary (did, public_key_hex, created_at).
        The private key never leaves the service in v1.
        """
        from Crypto.PublicKey import ECC

        key = ECC.generate(curve="Ed25519")
        now = time.time()
        with self._lock:
            self._key = key
            self._created_at = now
        _log.info("agent identity generated: %s", self._did_unlocked(key))
        return self.show()

    def show(self) -> dict[str, Any]:
        """Return a public summary of the current identity, or empty dict
        if no identity is set.
        """
        with self._lock:
            key = self._key
            created_at = self._created_at
        if key is None:
            return {}
        return {
            "did": self._did_unlocked(key),
            "public_key_hex": self._public_key_bytes(key).hex(),
            "created_at": created_at,
        }

    def public_key_hex(self) -> str | None:
        """Just the public-key hex (no DID encoding). ``None`` if absent."""
        with self._lock:
            key = self._key
        if key is None:
            return None
        return self._public_key_bytes(key).hex()

    def sign(self, message: bytes) -> bytes:
        """Sign ``message`` with the agent's private key.

        Raises :class:`RuntimeError` if no identity exists. Returns
        the 64-byte ed25519 signature.
        """
        from Crypto.Signature import eddsa

        with self._lock:
            key = self._key
        if key is None:
            raise RuntimeError(
                "No agent identity. Run /identity create or the "
                "agent_identity tool with action=create first."
            )
        signer = eddsa.new(key, mode="rfc8032")
        return bytes(signer.sign(message))

    @staticmethod
    def verify(public_key_hex: str, message: bytes, signature: bytes) -> bool:
        """Verify a signature against an arbitrary public key.

        Static — doesn't touch the service's current identity. Returns
        ``True`` iff the signature is valid for the message under the
        given public key. Any decode / size / shape error returns
        ``False`` (does not raise) so callers can use it as a clean
        boolean predicate.
        """
        try:
            pubkey_bytes = bytes.fromhex(public_key_hex.strip())
        except ValueError:
            return False
        if len(pubkey_bytes) != 32:
            return False
        try:
            from Crypto.PublicKey import ECC
            from Crypto.Signature import eddsa

            # pycryptodome can't import a 32-byte raw ed25519 key directly;
            # wrap it in a SubjectPublicKeyInfo DER (algorithm OID 1.3.101.112).
            der = _ED25519_DER_PREFIX + pubkey_bytes
            pub_key = ECC.import_key(der)
            verifier = eddsa.new(pub_key, mode="rfc8032")
            verifier.verify(message, signature)
            return True
        except (ValueError, TypeError):
            return False

    # --- helpers --------------------------------------------------------

    @staticmethod
    def _public_key_bytes(key: Any) -> bytes:
        return key.public_key().export_key(format="raw")

    @classmethod
    def _did_unlocked(cls, key: Any) -> str:
        return encode_did_key(cls._public_key_bytes(key))


_instance: IdentityService | None = None


def get_identity_service() -> IdentityService:
    global _instance
    if _instance is None:
        _instance = IdentityService()
    return _instance
