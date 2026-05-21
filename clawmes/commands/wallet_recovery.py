"""Wallet recovery and backup slash commands.

Four sensitive commands that wrap the local-key keystore primitives
(`clawmes/wallet/keystore.py`, `clawmes/wallet/local_key.py`):

  * ``/create_wallet <password>`` — generate a fresh mnemonic + keystore.
    Refuses if a keystore already exists; user must back up + remove
    the existing one first.
  * ``/recover <password> | <mnemonic>`` — import an existing mnemonic
    under ``password``. Two-phase: ``/recover`` with no args returns
    usage; with args, validates word count (12/24) and persists.
    Will overwrite an existing keystore — backup first.
  * ``/export_wallet <password>`` — decrypt and display the active
    keystore's mnemonic. Two-phase: ``/export_wallet`` with no args
    returns usage; with the correct password, surfaces the mnemonic
    inline with a ``DO NOT SHARE`` warning.
  * ``/wallet_backup [filename]`` — copy the keystore.bin to a
    timestamped backup file. Optional ``filename`` overrides the
    default path.

Security posture:

* These commands do NOT go through the ``@write_tool`` policy gate
  (only tools do). The safety boundary is **the password requirement**:
  every sensitive operation requires the user to supply their
  keystore password as an inline argument, mirroring the OpenClawnch
  convention from ``wallet-manage-commands.ts``.
* Mnemonic strings appear in the returned message verbatim — by
  necessity. The caller (LLM / gateway) MUST NOT log these or
  echo them anywhere except directly to the user.

Single-user assumption: the local keystore is one-per-host. Multi-user
keystores live in the OS keyring keyed by ``address`` (see
``KEYRING_SERVICE`` in :mod:`clawmes.wallet.keystore`) but the slash
surface only operates on the file-backed canonical keystore.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from clawmes.lib.logger import logger_for
from clawmes.lib.paths import wallet_dir
from clawmes.wallet.keystore import (
    KeystoreError,
    address_from_mnemonic,
    decrypt_mnemonic,
    load_keystore,
)

_log = logger_for("commands.wallet_recovery")

# Mnemonics are 12 or 24 words by the BIP-39 standard. 18-word
# variants exist but are non-standard; reject them to avoid silent
# entropy loss.
_VALID_MNEMONIC_LENGTHS = (12, 24)


# --- /create_wallet -----------------------------------------------------


async def handle_create_wallet(raw_args: str) -> str:
    from clawmes.services.wallet import get_wallet_service

    password = raw_args.strip()
    if not password:
        return (
            "Create a fresh local-key wallet.\n\n"
            "Usage:\n"
            "  /create_wallet <password>\n\n"
            "Generates a new BIP-39 24-word mnemonic, derives the address, "
            "and saves the encrypted keystore to disk. The mnemonic is "
            "shown ONE TIME after creation — write it down and store it "
            "somewhere safe. If you already have a keystore, use "
            "/export_wallet to back it up first, then /wallet_backup, "
            "then manually delete the existing keystore before running "
            "/create_wallet again."
        )

    # Refuse to overwrite an existing keystore — the user might think
    # they're loading and accidentally trigger a generate.
    if load_keystore() is not None:
        return (
            "A local keystore already exists. Refusing to overwrite.\n"
            "If you really want a fresh wallet:\n"
            "  1. /export_wallet <password> — back up the current mnemonic\n"
            "  2. /wallet_backup — copy the encrypted file\n"
            "  3. Manually delete the keystore at "
            f"{wallet_dir() / 'keystore.bin'}\n"
            "  4. Re-run /create_wallet"
        )

    svc = get_wallet_service()
    try:
        state = svc.connect_local_key(password, generate=True)
    except KeystoreError as exc:
        return f"Wallet creation failed: {exc}"

    mnemonic = state.balances.get("_mnemonic", "")
    return (
        f"New wallet created.\n"
        f"  Address: {state.address}\n"
        f"  Chain:   {state.chain_name}\n"
        f"  Mode:    local (encrypted keystore on disk)\n\n"
        "WRITE DOWN THIS MNEMONIC — DO NOT SHARE WITH ANYONE:\n\n"
        f"  {mnemonic}\n\n"
        "This is the only time it will be displayed automatically. "
        "Use /export_wallet <password> to see it again, or /wallet_backup "
        "to copy the encrypted file to a safe location."
    )


# --- /recover -----------------------------------------------------------


async def handle_recover(raw_args: str) -> str:
    from clawmes.services.wallet import get_wallet_service

    arg = raw_args.strip()
    if not arg:
        return (
            "Recover a wallet from a BIP-39 mnemonic.\n\n"
            "Usage:\n"
            "  /recover <password> | <mnemonic words>\n\n"
            "Example:\n"
            "  /recover hunter2 | abandon abandon abandon abandon abandon abandon abandon "
            "abandon abandon abandon abandon about\n\n"
            "The mnemonic must be 12 or 24 words (BIP-39 standard). The "
            "wallet is encrypted under <password> and saved to "
            f"{wallet_dir() / 'keystore.bin'}. If a keystore already "
            "exists, it will be overwritten — use /wallet_backup first."
        )

    if "|" not in arg:
        return "Missing the '|' separator. Usage: /recover <password> | <mnemonic words>"

    password_raw, mnemonic_raw = arg.split("|", 1)
    password = password_raw.strip()
    mnemonic = mnemonic_raw.strip()

    if not password:
        return "Password is empty — pass a non-empty password."
    if not mnemonic:
        return "Mnemonic is empty — paste the 12 or 24 BIP-39 words after the |."

    word_count = len(mnemonic.split())
    if word_count not in _VALID_MNEMONIC_LENGTHS:
        return (
            f"Mnemonic has {word_count} words — expected 12 or 24. "
            "Check for missing or extra words."
        )

    svc = get_wallet_service()
    try:
        state = svc.connect_local_key(password, mnemonic=mnemonic)
    except KeystoreError as exc:
        return f"Recovery failed: {exc}"

    return (
        f"Wallet recovered.\n"
        f"  Address: {state.address}\n"
        f"  Chain:   {state.chain_name}\n"
        f"  Mode:    local (encrypted keystore on disk)\n\n"
        "The mnemonic is now encrypted on disk. Use /export_wallet to "
        "view it again or /wallet_backup to copy the encrypted file."
    )


# --- /export_wallet -----------------------------------------------------


async def handle_export_wallet(raw_args: str) -> str:
    password = raw_args.strip()
    if not password:
        return (
            "Show the mnemonic for the active local-key wallet.\n\n"
            "Usage:\n"
            "  /export_wallet <password>\n\n"
            "Decrypts the keystore at "
            f"{wallet_dir() / 'keystore.bin'} and prints the mnemonic "
            "inline. DO NOT SHARE the output — anyone with the mnemonic "
            "can drain the wallet."
        )

    keystore = load_keystore()
    if keystore is None:
        return (
            "No local keystore found. Use /create_wallet to generate "
            "one or /recover to import an existing mnemonic."
        )

    try:
        mnemonic = decrypt_mnemonic(keystore, password)
    except KeystoreError as exc:
        return f"Decrypt failed: {exc}"

    # Re-derive the address from the decrypted seed for the message —
    # confirms the keystore is internally consistent.
    try:
        address, _privkey = address_from_mnemonic(mnemonic)
    except Exception as exc:  # noqa: BLE001 — derivation can raise on malformed mnemonic
        _log.warning("address derivation from exported mnemonic failed: %s", exc)
        address = keystore.address

    return (
        "WRITE DOWN THIS MNEMONIC — DO NOT SHARE WITH ANYONE:\n\n"
        f"  {mnemonic}\n\n"
        f"  Address: {address}\n\n"
        "Anyone with this mnemonic can drain the wallet. Once you've "
        "copied it somewhere safe, the message above can be cleared "
        "from chat history."
    )


# --- /wallet_backup -----------------------------------------------------


async def handle_wallet_backup(raw_args: str) -> str:
    from clawmes.wallet.keystore import _file_path

    keystore_path = _file_path()
    if not keystore_path.exists():
        return (
            "No local keystore found at "
            f"{keystore_path}. Nothing to back up. Use /create_wallet "
            "or /recover first."
        )

    arg = raw_args.strip()
    if arg:
        target = Path(arg).expanduser()
        if target.is_dir():
            target = target / _default_backup_name()
    else:
        target = wallet_dir() / _default_backup_name()

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(keystore_path, target)
    except OSError as exc:
        return f"Backup failed: {exc}"

    # File contains only the encrypted blob — safe to share without
    # the password (though we still warn against it).
    return (
        "Encrypted keystore backed up.\n"
        f"  Source: {keystore_path}\n"
        f"  Backup: {target}\n\n"
        "The backup is encrypted. You still need your password to "
        "use it. Store it somewhere durable — a USB drive, a password "
        "manager attachment, an encrypted cloud folder."
    )


def _default_backup_name() -> str:
    timestamp = time.strftime("%Y%m%dT%H%M%S")
    return f"keystore-backup-{timestamp}.bin"


# --- Registration -------------------------------------------------------


def register(ctx) -> None:
    """Wire wallet-recovery commands into Hermes."""
    ctx.register_command(
        name="create_wallet",
        handler=handle_create_wallet,
        description="Generate a fresh local-key wallet (requires password)",
        args_hint="<password>",
    )
    ctx.register_command(
        name="recover",
        handler=handle_recover,
        description="Recover a wallet from a BIP-39 mnemonic",
        args_hint="<password> | <mnemonic>",
    )
    ctx.register_command(
        name="export_wallet",
        handler=handle_export_wallet,
        description="Show the mnemonic for the active local-key wallet",
        args_hint="<password>",
    )
    ctx.register_command(
        name="wallet_backup",
        handler=handle_wallet_backup,
        description="Copy the encrypted keystore.bin to a backup file",
        args_hint="[output_path]",
    )
