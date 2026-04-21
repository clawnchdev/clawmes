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
    return (
        "WalletConnect pairing not yet implemented at this milestone. "
        "The Node WC bridge ships in a forthcoming commit."
    )


async def handle_disconnect(raw_args: str) -> str:
    state = get_wallet_state()
    if not state.connected:
        return "No active wallet session."
    return "Disconnect not yet implemented at this milestone."


async def handle_mode(raw_args: str) -> str:
    requested = raw_args.strip().lower()
    if not requested:
        state = get_wallet_state()
        return f"Current wallet mode: {state.mode or '(not configured)'}"
    if requested not in ("walletconnect", "local", "bankr"):
        return f"Unknown mode {requested!r}. Choose one of: walletconnect, local, bankr."
    return (
        f"Switching to {requested!r} mode is not yet implemented at this "
        "milestone. Run `hermes clawmes init` to reconfigure."
    )


async def handle_chain(raw_args: str) -> str:
    arg = raw_args.strip()
    if not arg:
        state = get_wallet_state()
        return f"Current chain: {state.chain_name or '(none)'} (id {state.chain_id})"
    return "Chain switching not yet implemented at this milestone."


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
