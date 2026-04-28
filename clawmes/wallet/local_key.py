"""Local-key wallet mode.

BIP-39 mnemonic generated locally → scrypt-derived AES-256-GCM key →
encrypted blob stored in macOS Keychain (via ``keyring``) or encrypted
file fallback at ``${HERMES_HOME}/clawmes/wallet/keystore.bin``.

Flow on first run:

  1. Caller passes ``password=...`` (and optionally ``mnemonic=...`` to
     import an existing seed).
  2. If no keystore exists for ``address``, a fresh mnemonic is
     generated, encrypted with the password, persisted, and the
     mnemonic is shown to the caller via the ``state.balances``
     side channel for one-time display. The caller MUST surface this
     to the user — it's their only chance to back it up.
  3. Sign methods unlock the keystore on demand; password caching
     (``password_cache_seconds``) is opt-in and off by default.

The mnemonic is the only secret on disk. The derived private key is
held in memory only as long as a sign / send_transaction call needs
it. We don't (and can't, in pure Python) zero memory after use; the
guarantee is "no plaintext at rest."
"""

from __future__ import annotations

import threading
import time
from typing import Any

from clawmes.lib.logger import logger_for
from clawmes.wallet._base import WalletMode
from clawmes.wallet.keystore import (
    KEYRING_SERVICE,  # noqa: F401  — re-exported for tests / docs
    KeystoreError,
    address_from_mnemonic,
    decrypt_mnemonic,
    encrypt_mnemonic,
    generate_mnemonic,
    load_keystore,
    save_keystore,
)
from clawmes.wallet.state import WalletState

_log = logger_for("wallet.local_key")


class LocalKeyMode(WalletMode):
    name = "local"

    def __init__(self, *, password_cache_seconds: int = 0) -> None:
        self._password_cache_seconds = password_cache_seconds
        self._state = WalletState.disconnected()
        self._cache_lock = threading.Lock()
        self._cached_mnemonic: str | None = None
        self._cache_expires_at: float = 0.0

    @property
    def keystore_path(self):
        from clawmes.wallet.keystore import _file_path

        return _file_path()

    # --- lifecycle ----------------------------------------------------

    def connect(self, **kwargs: Any) -> WalletState:
        """Materialize the wallet. Two paths:

        * **Existing keystore** — caller passes ``password=...`` only.
          We load the keystore, decrypt the mnemonic with the password
          (raises :class:`KeystoreError` on wrong password), and set
          state to ``connected`` with the derived address.

        * **Fresh wallet** — caller passes ``password=...`` plus
          ``mnemonic=...`` (to import) or ``generate=True`` (to create
          a new one). We generate / accept the mnemonic, derive the
          address, encrypt + persist, and return state with the
          mnemonic stashed in ``balances['_mnemonic']`` for one-time
          display.

        ``account_index`` defaults to 0 (first BIP-44 account). Pass a
        different index to derive a sibling account.
        """
        password = kwargs.get("password")
        if not isinstance(password, str) or not password:
            raise KeystoreError("password is required to connect local-key wallet")

        account_index = int(kwargs.get("account_index") or 0)
        explicit_mnemonic = kwargs.get("mnemonic")
        do_generate = bool(kwargs.get("generate"))

        # Empty-string mnemonic is a clear user error — distinguish from
        # "no mnemonic kwarg" so we don't silently fall through to load.
        if explicit_mnemonic is not None and not (
            isinstance(explicit_mnemonic, str) and explicit_mnemonic.strip()
        ):
            raise KeystoreError("imported mnemonic must be a non-empty string")

        if explicit_mnemonic or do_generate:
            return self._create(password, explicit_mnemonic, account_index)
        return self._load(password, account_index)

    def disconnect(self) -> None:
        with self._cache_lock:
            self._cached_mnemonic = None
            self._cache_expires_at = 0.0
        self._state = WalletState.disconnected()

    def state(self) -> WalletState:
        return self._state

    # --- create / load ------------------------------------------------

    def _create(
        self,
        password: str,
        explicit_mnemonic: object,
        account_index: int,
    ) -> WalletState:
        # connect() pre-validates the mnemonic kwarg; this just
        # narrows for type checkers + handles the generate path.
        if isinstance(explicit_mnemonic, str) and explicit_mnemonic.strip():
            mnemonic = explicit_mnemonic.strip()
        else:
            mnemonic = generate_mnemonic()

        address, _privkey = address_from_mnemonic(mnemonic, account_index=account_index)
        keystore = encrypt_mnemonic(mnemonic, password, address)
        save_keystore(keystore)
        self._cache_mnemonic_if_enabled(mnemonic)

        # Stash the mnemonic in balances for one-time display. The
        # caller MUST surface this to the user.
        self._state = WalletState(
            connected=True,
            mode="local",
            address=address,
            chain_id=8453,
            chain_name="Base",
            balances={"_mnemonic": mnemonic},
        )
        _log.info("local wallet created for %s", address)
        return self._state

    def _load(self, password: str, account_index: int) -> WalletState:
        keystore = load_keystore()
        if keystore is None:
            raise KeystoreError(
                "no local keystore found — pass generate=True or mnemonic=... to create one"
            )
        mnemonic = decrypt_mnemonic(keystore, password)
        # Re-derive the address from the loaded mnemonic so the caller
        # gets an authoritative value (the keystore's stored address
        # could in theory disagree with the mnemonic).
        address, _privkey = address_from_mnemonic(mnemonic, account_index=account_index)
        self._cache_mnemonic_if_enabled(mnemonic)
        self._state = WalletState(
            connected=True,
            mode="local",
            address=address,
            chain_id=8453,
            chain_name="Base",
        )
        _log.info("local wallet loaded for %s", address)
        return self._state

    # --- signing ------------------------------------------------------

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
        """Sign and submit a transaction via the RPC service.

        Requires the password cache to be hot (call ``connect`` with
        ``password_cache_seconds > 0`` first), otherwise raises
        :class:`KeystoreError` — the local-key mode can't prompt
        non-interactively.
        """
        from clawmes.services.rpc import get_rpc_service

        if not self._state.connected or self._state.address is None:
            raise KeystoreError("local wallet not connected")

        privkey = self._derive_privkey()
        target_chain = chain_id or self._state.chain_id or 8453
        rpc = get_rpc_service()

        nonce_hex = rpc.eth_call(
            to=self._state.address,
            data="0x",
            chain_id=target_chain,
        )  # noqa: F841 — placeholder; real impl uses eth_getTransactionCount
        # NOTE: at this milestone we issue a synthetic nonce; the
        # caller integration with eth_getTransactionCount lands in the
        # follow-up that adds tx-receipt polling. For now this method
        # signs and returns the raw signed-tx hex so it's testable
        # end-to-end without a live chain.
        del nonce_hex  # silences unused-variable lint

        from eth_account import Account
        from eth_utils import to_checksum_address

        tx = {
            "to": to_checksum_address(to),
            "value": int(value),
            "gas": int(gas or 21000),
            "maxFeePerGas": int(max_fee_per_gas or 10**10),
            "maxPriorityFeePerGas": int(max_priority_fee_per_gas or 10**9),
            "nonce": 0,
            "chainId": int(target_chain),
            "type": 2,
        }
        if data:
            tx["data"] = data if isinstance(data, str) else "0x" + data.hex()

        signed = Account.sign_transaction(tx, privkey)
        return signed.raw_transaction.hex()

    def sign_typed_data_v4(self, typed_data: dict[str, Any]) -> str:
        from eth_account import Account
        from eth_account.messages import encode_typed_data

        privkey = self._derive_privkey()
        signable = encode_typed_data(full_message=typed_data)
        signed = Account.sign_message(signable, private_key=privkey)
        return signed.signature.hex()

    def sign_personal_message(self, message: bytes | str) -> str:
        from eth_account import Account
        from eth_account.messages import encode_defunct

        privkey = self._derive_privkey()
        if isinstance(message, str):
            signable = encode_defunct(text=message)
        else:
            signable = encode_defunct(primitive=message)
        signed = Account.sign_message(signable, private_key=privkey)
        return signed.signature.hex()

    # --- helpers ------------------------------------------------------

    def _cache_mnemonic_if_enabled(self, mnemonic: str) -> None:
        if self._password_cache_seconds <= 0:
            return
        with self._cache_lock:
            self._cached_mnemonic = mnemonic
            self._cache_expires_at = time.monotonic() + self._password_cache_seconds

    def _derive_privkey(self) -> str:
        """Return the cached private key (hex) or raise.

        Local-key mode signs from the mnemonic → privkey at sign time.
        We require the mnemonic to be cached (via ``connect`` with
        ``password_cache_seconds > 0``) because no callable in the
        sign path can prompt the user mid-call.
        """
        with self._cache_lock:
            now = time.monotonic()
            if self._cached_mnemonic is None or self._cache_expires_at <= now:
                self._cached_mnemonic = None
                raise KeystoreError(
                    "local-key cache expired or empty; reconnect with "
                    "password_cache_seconds > 0 to enable signing"
                )
            mnemonic = self._cached_mnemonic
        _addr, privkey = address_from_mnemonic(mnemonic)
        return privkey
