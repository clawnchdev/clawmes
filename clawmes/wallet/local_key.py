"""Local-key wallet mode.

BIP-39 mnemonic generated locally → scrypt-derived AES-256-GCM key →
encrypted blob stored in macOS Keychain (via ``keyring``) or encrypted
file fallback at ``${HERMES_HOME}/clawmes/wallet/keystore.bin``.

The mnemonic is shown **once** in chat at creation time and never
persisted in plaintext. Signing requires the password each time
(unless ``clawmes.wallet.local.password_cache_seconds`` > 0, default 0).
"""

from __future__ import annotations

from typing import Any

from clawmes.lib.logger import logger_for
from clawmes.lib.paths import wallet_dir
from clawmes.wallet._base import WalletMode
from clawmes.wallet.state import WalletState

_log = logger_for("wallet.local_key")

KEYRING_SERVICE = "clawmes"
SCRYPT_N = 2**17
SCRYPT_R = 8
SCRYPT_P = 1
DKLEN = 32  # bytes — AES-256 key


class LocalKeyMode(WalletMode):
    name = "local"

    def __init__(self, password_cache_seconds: int = 0) -> None:
        self._password_cache_seconds = password_cache_seconds
        self._state = WalletState.disconnected()

    @property
    def keystore_path(self):
        return wallet_dir() / "keystore.bin"

    def connect(self, **kwargs: Any) -> WalletState:
        # TODO(v0.1.0): if keystore exists, prompt for password and
        # decrypt. Else generate BIP-39 mnemonic, encrypt with
        # password-derived key, store, show mnemonic to user via the
        # caller's display channel, then return WalletState.
        _log.info("local key connect requested (stub)")
        return self._state

    def disconnect(self) -> None:
        # Just drop in-memory state; keystore stays on disk.
        self._state = WalletState.disconnected()

    def state(self) -> WalletState:
        return self._state

    def send_transaction(
        self,
        *,
        to: str,
        value: int,
        data: bytes | str = b"",
        chain_id: int | None = None,
        gas: int | None = None,
        max_fee_per_gas: int | None = None,
        max_priority_fee_per_gas: int | None = None,
    ) -> str:
        raise NotImplementedError(
            "Local-key signing not wired in this milestone. "
            "Forthcoming: eth_account.Account.sign_transaction + RPC submit."
        )

    def sign_typed_data_v4(self, typed_data: dict[str, Any]) -> str:
        raise NotImplementedError("Local-key sign_typed_data not wired in this milestone.")

    def sign_personal_message(self, message: bytes | str) -> str:
        raise NotImplementedError("Local-key personal_sign not wired in this milestone.")
