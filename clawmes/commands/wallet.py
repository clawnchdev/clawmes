"""Wallet commands: ``/wallet``, ``/connect``, ``/disconnect``, ``/mode``, ``/chain``, ``/address``."""

from __future__ import annotations

from clawmes.lib.addr import short
from clawmes.services.wallet import get_wallet_state


async def handle_wallet(raw_args: str) -> str:
    state = get_wallet_state()
    if not state.connected:
        return (
            "No wallet connected.\n"
            "  /connect          — pair via WalletConnect (recommended)\n"
            "  /connect_local    — generate or import a local key\n"
            "  /connect_bankr    — pair Bankr custodial wallet"
        )
    return "\n".join(
        [
            f"Address:  {state.address}  ({short(state.address or '')})",
            f"Chain:    {state.chain_name} (id {state.chain_id})",
            f"Mode:     {state.mode}",
            f"Balance:  {state.balance_summary()}",
            f"Policies: {state.policy_summary()}",
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
        name="connect_bankr",
        handler=handle_connect_bankr,
        description="Connect a Bankr custodial wallet",
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
