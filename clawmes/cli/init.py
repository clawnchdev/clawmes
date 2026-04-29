"""``hermes clawmes init`` — interactive setup wizard.

Three-step flow (channel + LLM are Hermes' concern, not ours):

  1. Wallet mode — walletconnect | local | bankr | skip
  2. Per-mode setup (project ID / password+mnemonic / API key)
  3. Optional API keys for the most-used third-party integrations
     (0x, LiFi, Etherscan family, Reservoir, Tally).

Persists everything to ``~/.hermes/.env`` in upsert mode — existing
non-clawmes keys are preserved, only the keys we set are
overwritten.

Modes:

  * default                     — interactive, asks all questions
  * ``--reconfigure``           — re-asks even when keys already set
  * ``--skip-wallet``           — only the optional keys section
  * ``--check``                 — dry-run, prints what would change
                                  but writes nothing
  * ``--non-interactive``       — reads ``CLAWMES_INIT_*`` env vars,
                                  never prompts. For CI / automation.
"""

from __future__ import annotations

import argparse
import getpass
import os
from pathlib import Path

from clawmes.lib.paths import display_path, hermes_home

_BANNER = """
╔══════════════════════════════════════╗
║          Welcome to clawmes          ║
╚══════════════════════════════════════╝

Hermes Agent for crypto. Setup follows.
""".strip()


# Optional API keys offered in step 3. Each row: (env, label, signup hint).
_OPTIONAL_KEYS: list[tuple[str, str, str]] = [
    ("ZEROX_API_KEY", "0x DEX aggregator", "https://0x.org"),
    ("LIFI_API_KEY", "LiFi cross-chain bridge", "https://li.fi"),
    ("BASESCAN_API_KEY", "Basescan block explorer", "https://basescan.org/myapikey"),
    ("ETHERSCAN_API_KEY", "Etherscan block explorer", "https://etherscan.io/myapikey"),
    ("RESERVOIR_API_KEY", "Reservoir NFT API", "https://reservoir.tools"),
    ("TALLY_API_KEY", "Tally governance", "https://www.tally.xyz/api"),
]


def run(args: argparse.Namespace) -> int:
    """Entry point for ``hermes clawmes init``."""
    print(_BANNER)
    print()
    if args.non_interactive:
        return _run_non_interactive(args)
    return _run_interactive(args)


# --- interactive path ---------------------------------------------------


def _run_interactive(args: argparse.Namespace) -> int:
    env_path = hermes_home() / ".env"
    existing = _read_env(env_path)

    new_values: dict[str, str] = {}

    if not args.skip_wallet:
        mode = _prompt_wallet_mode(existing, reconfigure=args.reconfigure)
        new_values.update(_setup_wallet_mode(mode, existing, args))
    else:
        print("Skipping wallet setup (--skip-wallet).")
        print()

    new_values.update(_prompt_optional_keys(existing, reconfigure=args.reconfigure))

    if not new_values:
        print("Nothing to write.")
        return 0

    return _persist_or_dry_run(env_path, existing, new_values, dry_run=args.check)


def _prompt_wallet_mode(existing: dict[str, str], *, reconfigure: bool) -> str:
    """Ask which wallet mode to use, defaulting to whatever is configured."""
    current_hint = ""
    if existing.get("WALLETCONNECT_PROJECT_ID"):
        current_hint = " (currently: walletconnect)"
    elif existing.get("BANKR_API_KEY"):
        current_hint = " (currently: bankr)"

    if existing.get("WALLETCONNECT_PROJECT_ID") and not reconfigure:
        print(f"Wallet already configured{current_hint}. Use --reconfigure to change.")
        print()
        return "skip"

    print(f"[1/3] Wallet mode{current_hint}")
    print("  walletconnect — pair with a phone wallet (recommended)")
    print("  local         — generate or import a mnemonic on this machine")
    print("  bankr         — connect a Bankr custodial wallet")
    print("  skip          — set up later via /connect")
    print()
    while True:
        choice = input("Choose mode [walletconnect/local/bankr/skip]: ").strip().lower()
        if choice in ("walletconnect", "wc"):
            return "walletconnect"
        if choice in ("local", "l"):
            return "local"
        if choice in ("bankr", "b"):
            return "bankr"
        if choice in ("skip", "s", ""):
            return "skip"
        print(f"  Unknown: {choice!r}. Choose one of: walletconnect, local, bankr, skip.")


def _setup_wallet_mode(
    mode: str, existing: dict[str, str], args: argparse.Namespace
) -> dict[str, str]:
    if mode == "walletconnect":
        return _setup_walletconnect(existing)
    if mode == "local":
        return _setup_local(existing, dry_run=args.check)
    if mode == "bankr":
        return _setup_bankr(existing)
    return {}


def _setup_walletconnect(existing: dict[str, str]) -> dict[str, str]:
    print()
    print("  WalletConnect setup")
    print("  Get a project ID at https://cloud.walletconnect.com (free)")
    current = existing.get("WALLETCONNECT_PROJECT_ID", "")
    if current:
        print(f"  Currently set to: {_redact(current)}")
    pid = input("  WALLETCONNECT_PROJECT_ID: ").strip()
    print()
    if not pid:
        return {}
    return {"WALLETCONNECT_PROJECT_ID": pid}


def _setup_local(existing: dict[str, str], *, dry_run: bool) -> dict[str, str]:
    print()
    print("  Local-key setup")
    print("  Your mnemonic will be encrypted (scrypt + AES-256-GCM) and")
    print("  stored at $HERMES_HOME/clawmes/wallet/keystore.bin.")
    print()

    while True:
        password = getpass.getpass("  Choose a password (min 8 chars): ")
        if len(password) < 8:
            print("  Password must be at least 8 characters.")
            continue
        confirm = getpass.getpass("  Confirm password: ")
        if password != confirm:
            print("  Passwords don't match. Try again.")
            continue
        break

    have_mnemonic = input("  Import an existing mnemonic? [y/N]: ").strip().lower() in ("y", "yes")
    mnemonic: str | None = None
    if have_mnemonic:
        mnemonic = input("  Enter mnemonic (12 or 24 words): ").strip()

    if dry_run:
        print(
            "  [--check] Would create encrypted keystore $HERMES_HOME/clawmes/wallet/keystore.bin"
        )
        return {}

    # Defer import — keystore module pulls in eth_keys / mnemonic libs
    # that we don't need for the WC / Bankr branches.
    from clawmes.wallet.keystore import (
        address_from_mnemonic,
        encrypt_mnemonic,
        generate_mnemonic,
        save_keystore,
    )

    if mnemonic is None:
        mnemonic = generate_mnemonic()
        print()
        print("  ╔══════════════════════════════════════════════════╗")
        print("  ║              SAVE THIS MNEMONIC NOW              ║")
        print("  ║   It will be shown ONCE. Loss = loss of funds.   ║")
        print("  ╚══════════════════════════════════════════════════╝")
        print()
        for i, word in enumerate(mnemonic.split(), start=1):
            print(f"    {i:>2}. {word}")
        print()
        input("  Press Enter once you've written the mnemonic down: ")

    address, _ = address_from_mnemonic(mnemonic)
    keystore = encrypt_mnemonic(mnemonic, password, address)
    save_keystore(keystore)
    print(f"  Keystore saved. Address: {address}")
    print()
    # We don't write the password to .env — that defeats the encryption.
    return {}


def _setup_bankr(existing: dict[str, str]) -> dict[str, str]:
    print()
    print("  Bankr setup")
    print("  Sign up at https://bankr.bot for an API key.")
    current = existing.get("BANKR_API_KEY", "")
    if current:
        print(f"  Currently set to: {_redact(current)}")
    key = getpass.getpass("  BANKR_API_KEY (input hidden): ").strip()
    print()
    if not key:
        return {}
    return {"BANKR_API_KEY": key}


def _prompt_optional_keys(existing: dict[str, str], *, reconfigure: bool) -> dict[str, str]:
    print("[3/3] Optional API keys")
    print("  Press Enter to skip any individual key.")
    print()
    out: dict[str, str] = {}
    for env, label, hint in _OPTIONAL_KEYS:
        current = existing.get(env, "")
        if current and not reconfigure:
            continue
        suffix = f"  (currently: {_redact(current)})" if current else f"  ({hint})"
        value = input(f"  {label} [{env}]{suffix}: ").strip()
        if value:
            out[env] = value
    print()
    return out


# --- non-interactive path -----------------------------------------------


def _run_non_interactive(args: argparse.Namespace) -> int:
    env_path = hermes_home() / ".env"
    existing = _read_env(env_path)

    new_values: dict[str, str] = {}

    mode = os.environ.get("CLAWMES_INIT_WALLET_MODE", "skip").lower()
    if mode == "walletconnect":
        pid = os.environ.get("CLAWMES_INIT_WALLETCONNECT_PROJECT_ID", "").strip()
        if not pid:
            print("Error: CLAWMES_INIT_WALLETCONNECT_PROJECT_ID required for walletconnect mode.")
            return 1
        new_values["WALLETCONNECT_PROJECT_ID"] = pid
    elif mode == "bankr":
        key = os.environ.get("CLAWMES_INIT_BANKR_API_KEY", "").strip()
        if not key:
            print("Error: CLAWMES_INIT_BANKR_API_KEY required for bankr mode.")
            return 1
        new_values["BANKR_API_KEY"] = key
    elif mode == "local":
        # Skip in --non-interactive: keystore creation needs a stable
        # password input flow that we'd rather not synthesize.
        print("local-key mode requires interactive password entry; skipping.")
    elif mode != "skip":
        print(f"Error: unknown CLAWMES_INIT_WALLET_MODE: {mode!r}")
        return 1

    # CLAWMES_INIT_KEYS = "K1=v1;K2=v2"
    raw = os.environ.get("CLAWMES_INIT_KEYS", "")
    for pair in raw.split(";"):
        pair = pair.strip()
        if not pair or "=" not in pair:
            continue
        k, v = pair.split("=", 1)
        new_values[k.strip()] = v.strip()

    if not new_values:
        print("Nothing to set.")
        return 0
    return _persist_or_dry_run(env_path, existing, new_values, dry_run=args.check)


# --- helpers -------------------------------------------------------------


def _read_env(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _persist_or_dry_run(
    env_path: Path,
    existing: dict[str, str],
    new_values: dict[str, str],
    *,
    dry_run: bool,
) -> int:
    print("Summary:")
    for k, v in sorted(new_values.items()):
        verb = "would set" if dry_run else "set"
        print(f"  {verb}: {k} = {_redact(v)}")
    print()

    if dry_run:
        print(f"  --check dry-run; {display_path(env_path)} unchanged.")
        return 0

    merged = {**existing, **new_values}
    _write_env(env_path, merged)
    print(f"  Wrote {display_path(env_path)} ({len(merged)} keys).")
    print()
    print("Next:")
    print("  hermes clawmes doctor   — verify setup")
    print("  hermes                  — start chatting")
    return 0


def _write_env(path: Path, values: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{k}={v}" for k, v in sorted(values.items())]
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(path)


def _redact(value: str) -> str:
    """Show only the last 4 chars of a secret-like value for confirmation."""
    if len(value) <= 8:
        return "•" * len(value)
    return "•" * (len(value) - 4) + value[-4:]
