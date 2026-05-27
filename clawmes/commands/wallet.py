"""Wallet commands: ``/wallet``, ``/connect``, ``/disconnect``, ``/mode``, ``/chain``, ``/address``.

v0.10.0 adds wallet tagging subcommands on top of ``/wallet``:

  * ``/wallet tag <name>``       — bookmark the active wallet under ``<name>``
  * ``/wallet tags``             — list saved tags
  * ``/wallet untag <name>``     — remove a saved tag
  * ``/wallet show <name>``      — print the address/chain/mode saved under ``<name>``

Tags are pure metadata — they let users keep notes like
``trading``, ``treasury``, ``onramp`` mapped to specific addresses so
you can flip context without re-typing. Switching the active wallet
still goes through the existing ``/connect*`` / ``/disconnect`` /
``/mode`` flow; tags are bookmarks, not session swaps.

Storage: ``${HERMES_HOME}/clawmes/wallet/tags.json``. Per-process —
not synced across machines (intentional; tag namespace is small).
"""

from __future__ import annotations

import json
from pathlib import Path

from clawmes.lib.addr import short
from clawmes.services.wallet import get_wallet_state


def _tags_path() -> Path:
    from clawmes.lib.paths import wallet_dir

    return wallet_dir() / "tags.json"


def _load_tags() -> dict[str, dict[str, str]]:
    """Return ``{tag_name: {address, chain_id, chain_name, mode}}``."""
    path = _tags_path()
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, dict)}


def _save_tags(tags: dict[str, dict[str, str]]) -> None:
    path = _tags_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(tags, f, indent=2, sort_keys=True)


async def handle_wallet(raw_args: str) -> str:
    arg = (raw_args or "").strip()
    if arg:
        parts = arg.split()
        sub = parts[0].lower()
        rest = parts[1:]
        if sub == "tag":
            return _cmd_tag(rest)
        if sub in ("tags", "list_tags"):
            return _cmd_tags()
        if sub == "untag":
            return _cmd_untag(rest)
        if sub == "show":
            return _cmd_show(rest)
        # Fall through: unknown subcommand → show wallet status with hint.

    state = get_wallet_state()
    if not state.connected:
        return (
            "No wallet connected.\n"
            "  /connect          — pair via WalletConnect (recommended)\n"
            "  /connect_local    — generate or import a local key\n"
            "  /connect_bankr    — pair Bankr custodial wallet\n"
            "\n"
            "Tag subcommands (work even without a connected wallet):\n"
            "  /wallet tags                  — list saved tags\n"
            "  /wallet show <name>           — show one tag's address"
        )
    return "\n".join(
        [
            f"Address:  {state.address}  ({short(state.address or '')})",
            f"Chain:    {state.chain_name} (id {state.chain_id})",
            f"Mode:     {state.mode}",
            f"Balance:  {state.balance_summary()}",
            f"Policies: {state.policy_summary()}",
            "",
            "Tag this wallet: /wallet tag <name>     (saves address + mode for later)",
            "Saved tags:      /wallet tags",
        ]
    )


def _cmd_tag(parts: list[str]) -> str:
    if not parts:
        return "Usage: /wallet tag <name>"
    name = parts[0]
    state = get_wallet_state()
    if not state.connected or not state.address:
        return "No wallet connected. Connect one first, then tag it."
    tags = _load_tags()
    tags[name] = {
        "address": state.address,
        "chain_id": str(state.chain_id) if state.chain_id is not None else "",
        "chain_name": state.chain_name or "",
        "mode": state.mode or "",
    }
    _save_tags(tags)
    return (
        f"Tagged active wallet as '{name}':\n"
        f"  Address: {state.address}\n"
        f"  Chain:   {state.chain_name} (id {state.chain_id})\n"
        f"  Mode:    {state.mode}\n"
        "\n"
        "Use /wallet show <name> to recall this later, or /wallet tags to list all."
    )


def _cmd_tags() -> str:
    tags = _load_tags()
    if not tags:
        return "No wallet tags saved. Tag the active wallet with /wallet tag <name>."
    lines = [f"Wallet tags ({len(tags)}):", ""]
    for name in sorted(tags):
        meta = tags[name]
        lines.append(
            f"  {name:<15s}"
            f"  {short(meta.get('address', ''))}"
            f"  on {meta.get('chain_name') or '?'}"
            f"  ({meta.get('mode') or '?'})"
        )
    return "\n".join(lines)


def _cmd_untag(parts: list[str]) -> str:
    if not parts:
        return "Usage: /wallet untag <name>"
    name = parts[0]
    tags = _load_tags()
    if name not in tags:
        return f"No tag named {name!r}."
    del tags[name]
    _save_tags(tags)
    return f"Removed tag '{name}'."


def _cmd_show(parts: list[str]) -> str:
    if not parts:
        return "Usage: /wallet show <name>"
    name = parts[0]
    tags = _load_tags()
    meta = tags.get(name)
    if meta is None:
        return f"No tag named {name!r}. /wallet tags to list."
    return "\n".join(
        [
            f"Tag '{name}':",
            f"  Address: {meta.get('address', '?')}",
            f"  Chain:   {meta.get('chain_name') or '?'} (id {meta.get('chain_id') or '?'})",
            f"  Mode:    {meta.get('mode') or '?'}",
            "",
            f"Reconnect: /connect_{meta.get('mode') or '<mode>'}  "
            "(use the matching connect command for the saved mode)",
        ]
    )


async def handle_connect(raw_args: str) -> str:
    from clawmes.bridges.process import BridgeError
    from clawmes.services.wallet import WalletConfigError, get_wallet_service

    svc = get_wallet_service()
    try:
        state = svc.connect_walletconnect()
    except WalletConfigError as exc:
        return f"WalletConnect setup error: {exc}"
    except BridgeError as exc:
        if exc.code == "config_error":
            return (
                "WalletConnect requires WALLETCONNECT_PROJECT_ID. "
                "Get one free at https://cloud.walletconnect.com and "
                "set it in ~/.hermes/.env."
            )
        return f"WalletConnect bridge error: {exc}"

    uri = state.balances.get("_pair_uri", "")
    if not uri:
        return "WalletConnect pairing started but no URI was returned."
    return (
        "Scan this QR / open the link on your phone wallet to pair:\n\n"
        f"```\n{uri}\n```\n\n"
        "Once you approve in your wallet, your address and chain will "
        "appear automatically. Run /wallet to confirm."
    )


async def handle_connect_local(raw_args: str) -> str:
    from clawmes.services.wallet import get_wallet_service
    from clawmes.wallet.keystore import KeystoreError

    password = raw_args.strip()
    if not password:
        return (
            "Connect a local-key wallet (existing keystore).\n\n"
            "Usage:\n"
            "  /connect_local <password>\n\n"
            "If you don't have a keystore yet, use /create_wallet to generate "
            "one or /recover to import an existing mnemonic. The local-key "
            "mode keeps the encrypted seed on disk; signing happens locally."
        )

    svc = get_wallet_service()
    try:
        state = svc.connect_local_key(password)
    except KeystoreError as exc:
        return f"Local wallet load failed: {exc}"
    return (
        f"Local wallet connected.\n"
        f"  Address: {state.address}\n"
        f"  Chain:   {state.chain_name}\n"
        f"  Mode:    local (encrypted keystore on disk)"
    )


async def handle_connect_bankr(raw_args: str) -> str:
    from clawmes.services.bankr_service import BankrError
    from clawmes.services.wallet import get_wallet_service

    svc = get_wallet_service()
    try:
        state = svc.connect_bankr()
    except BankrError as exc:
        if exc.code == "no_credentials":
            return (
                "Bankr wallet requires BANKR_API_KEY. Sign up at "
                "https://bankr.bot and set the key in ~/.hermes/.env."
            )
        return f"Bankr connect failed: {exc.message}"

    return (
        f"Bankr wallet connected.\n"
        f"  Address: {state.address}\n"
        f"  Chain:   {state.chain_name}\n"
        f"  Mode:    bankr (custodial)"
    )


async def handle_connect_base(raw_args: str) -> str:
    """``/connect_base [code]`` — connect a Coinbase Smart Wallet / Base Account.

    Two-step flow:

      1. ``/connect_base`` (no args) — clawmes returns an OAuth
         authorize URL. User visits it in a browser, completes
         Coinbase OAuth, and copies the ``code`` query param from
         the redirect URL.
      2. ``/connect_base <code>`` — clawmes exchanges the code for
         access + refresh tokens and finalizes the connection.

    Every tx + signature thereafter triggers a stored-request approval
    in the user's Base App, same UX as Base MCP.
    """
    from clawmes.services.base_account import BaseAccountError
    from clawmes.services.wallet import get_wallet_service

    svc = get_wallet_service()
    code = raw_args.strip()

    if not code:
        try:
            _state, auth_url = svc.connect_base_account()
        except BaseAccountError as exc:
            if exc.code == "not_configured":
                return (
                    "Base Account connect requires CLAWMES_BASE_ACCOUNT_CLIENT_ID. "
                    "Register a Coinbase Developer Platform OAuth client at "
                    "https://portal.cdp.coinbase.com and export the client id "
                    "before /connect_base."
                )
            return f"Base Account connect failed: {exc.message}"
        return (
            "Base Account OAuth started. Open this URL in a browser:\n"
            f"\n  {auth_url}\n\n"
            "After approving, copy the `code` query param from the redirect URL "
            "and run:\n"
            "  /connect_base <code>"
        )

    try:
        state = svc.complete_base_account(code)
    except BaseAccountError as exc:
        return f"Base Account code exchange failed ({exc.code}): {exc.message}"
    return (
        f"Base Account connected.\n"
        f"  Address: {state.address}\n"
        f"  Chain:   {state.chain_name}\n"
        f"  Mode:    base_account (Coinbase Smart Wallet)\n"
        "\n"
        "Tx + signature requests will prompt for approval in your Base App."
    )


async def handle_disconnect(raw_args: str) -> str:
    from clawmes.services.wallet import get_wallet_service

    svc = get_wallet_service()
    previous = svc.disconnect()
    if not previous.connected:
        return "No active wallet session."

    addr_short = short(previous.address or "")
    chain = previous.chain_name or f"chain {previous.chain_id}"
    mode_label = {
        "walletconnect": "WalletConnect session",
        "local": "local-key session",
        "bankr": "Bankr session",
        "base_account": "Base Account session",
    }.get(previous.mode or "", "wallet session")
    return f"Disconnected {mode_label} ({addr_short} on {chain})."


async def handle_mode(raw_args: str) -> str:
    requested = raw_args.strip().lower()
    state = get_wallet_state()
    if not requested:
        return f"Current wallet mode: {state.mode or '(not configured)'}"
    if requested not in ("walletconnect", "local", "bankr"):
        return f"Unknown mode {requested!r}. Choose one of: walletconnect, local, bankr."
    if state.mode == requested:
        return f"Already in {requested!r} mode."

    connect_cmd = {
        "walletconnect": "/connect",
        "local": "/connect_local",
        "bankr": "/connect_bankr",
    }[requested]
    return (
        f"Switching to {requested!r} mode requires reconnecting. "
        f"Run `/disconnect` then `{connect_cmd}` to switch."
    )


async def handle_chain(raw_args: str) -> str:
    arg = raw_args.strip()
    if not arg:
        state = get_wallet_state()
        return f"Current chain: {state.chain_name or '(none)'} (id {state.chain_id})"

    from clawmes.services.wallet import WalletConfigError, get_wallet_service

    # Allow either an integer id ("1") or a name ("ethereum").
    target: int | str
    try:
        target = int(arg)
    except ValueError:
        target = arg

    svc = get_wallet_service()
    try:
        new_state = svc.switch_chain(target)
    except WalletConfigError as exc:
        return f"Cannot switch chain: {exc}"
    except Exception as exc:  # noqa: BLE001 — mode-specific errors surfaced verbatim
        return f"Chain switch failed: {exc}"

    return (
        f"Switched to {new_state.chain_name} (id {new_state.chain_id}). "
        f"Address: {new_state.address}"
    )


async def handle_address(raw_args: str) -> str:
    state = get_wallet_state()
    if not state.connected:
        return "No wallet connected."
    return f"{state.address}"


def register(ctx) -> None:
    ctx.register_command(
        name="wallet",
        handler=handle_wallet,
        description="Show connected wallet address, chain, balance, and active spending policies",
    )
    ctx.register_command(
        name="connect",
        handler=handle_connect,
        description="Pair a wallet via WalletConnect v2",
    )
    ctx.register_command(
        name="connect_local",
        handler=handle_connect_local,
        description="Load an existing local-key keystore (requires password)",
        args_hint="<password>",
    )
    ctx.register_command(
        name="connect_bankr",
        handler=handle_connect_bankr,
        description="Connect a Bankr custodial wallet",
    )
    ctx.register_command(
        name="connect_base",
        handler=handle_connect_base,
        description="Connect a Coinbase Smart Wallet / Base Account via OAuth",
        args_hint="[code]",
    )
    ctx.register_command(
        name="disconnect",
        handler=handle_disconnect,
        description="Drop the active WalletConnect session",
    )
    ctx.register_command(
        name="mode",
        handler=handle_mode,
        description="Switch wallet mode (walletconnect | local | bankr)",
        args_hint="[mode]",
    )
    ctx.register_command(
        name="chain",
        handler=handle_chain,
        description="Show or switch the active chain",
        args_hint="[chain_id]",
    )
    ctx.register_command(
        name="address",
        handler=handle_address,
        description="Show the connected wallet address",
    )
