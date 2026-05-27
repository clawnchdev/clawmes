"""Balance / portfolio slash commands.

Two thin wrappers over the ``defi_balance`` tool's existing actions:

  * ``/balance [chain]`` — native-token balance on the current (or
    specified) chain. Wraps ``defi_balance.native``.
  * ``/portfolio [chain]`` — native + curated common-token list
    (USDC, WETH, USDT, DAI) on the chain. Wraps
    ``defi_balance.summary``.

Both grab the active wallet address from :mod:`clawmes.services.wallet`
so the user doesn't have to retype it. If no wallet is connected,
both return an actionable error pointing at ``/connect``.

These are pure surface deltas — every byte of behavior already exists
in the ``defi_balance`` tool. The slash commands exist so users don't
have to ask the LLM to run an explicit tool call for a read this
routine.
"""

from __future__ import annotations

import json

from clawmes.lib.addr import short
from clawmes.services.wallet import get_wallet_state


def _resolve_chain(raw_args: str, current_chain_id: int | None) -> str | None:
    """Resolve the chain argument for either command.

    Returns the string clawmes' chain helper accepts (an int-as-str or
    a name like ``ethereum``). Falls back to the wallet's current chain
    id when no arg is given. Returns ``None`` if neither is available —
    caller must surface an error.
    """
    arg = raw_args.strip()
    if arg:
        return arg
    if current_chain_id is not None:
        return str(current_chain_id)
    return None


async def handle_balance(raw_args: str) -> str:
    state = get_wallet_state()
    if not state.connected or not state.address:
        return "No wallet connected. Run /connect or /connect_local first."

    chain = _resolve_chain(raw_args, state.chain_id)
    if chain is None:
        return "No chain specified and no active chain set. Pass a chain id."

    from clawmes.tools.defi_balance import defi_balance

    raw = defi_balance(
        {
            "action": "native",
            "address": state.address,
            "chain": chain,
        }
    )
    payload = json.loads(raw)
    if payload.get("isError"):
        return payload.get("content", [{}])[0].get("text", "balance query failed")

    details = payload.get("details") or {}
    chain_label = details.get("chain") or chain
    pretty = details.get("native_balance") or details.get("native_balance_wei", "?")
    return f"Native balance for {short(state.address)} on {chain_label}: {pretty}"


async def handle_portfolio(raw_args: str, *, sender_id: str = "default") -> str:
    """``/portfolio`` — native + ERC-20 balances by default; P&L on request.

    Subcommands:
      * (none)        live balance summary via ``defi_balance.summary``
      * ``pnl``       overall P&L from the cost-basis ledger
      * ``realized``  realized gains only (closed positions)
      * ``unrealized`` unrealized gains (open positions, marked at last price)
      * ``export``    full lot-by-lot ledger dump
      * ``<chain>``   any non-subcommand token is treated as a chain hint
    """
    arg = (raw_args or "").strip()
    sub = arg.split()[0].lower() if arg else ""
    if sub in {"pnl", "realized", "unrealized", "export"}:
        return _render_pnl(sub, sender_id)
    return await _render_balance_summary(arg)


async def _render_balance_summary(raw_args: str) -> str:
    """The original /portfolio behavior — live balances via defi_balance."""
    state = get_wallet_state()
    if not state.connected or not state.address:
        return "No wallet connected. Run /connect or /connect_local first."

    chain = _resolve_chain(raw_args, state.chain_id)
    if chain is None:
        return "No chain specified and no active chain set. Pass a chain id."

    from clawmes.tools.defi_balance import defi_balance

    raw = defi_balance(
        {
            "action": "summary",
            "address": state.address,
            "chain": chain,
        }
    )
    payload = json.loads(raw)
    if payload.get("isError"):
        return payload.get("content", [{}])[0].get("text", "portfolio query failed")

    content = payload.get("content") or []
    if content and content[0].get("text"):
        text = content[0]["text"]
        return text + "\n\nP&L views: /portfolio pnl | realized | unrealized | export"
    details = payload.get("details") or {}
    return f"Portfolio for {short(state.address)} on {chain}: {details!r}"


# Map user-facing subcommand → cost_basis action.
_PNL_ACTION_MAP = {
    "pnl": "summary",
    "realized": "realized",
    "unrealized": "unrealized",
    "export": "export",
}


def _render_pnl(sub: str, sender_id: str) -> str:
    """Delegate to the ``cost_basis`` tool for a P&L view.

    The tool already renders chat-friendly text in ``content[0]["text"]``;
    this command just routes the user choice through the mapping table
    and unwraps the envelope.
    """
    from clawmes.tools.cost_basis import cost_basis

    action = _PNL_ACTION_MAP[sub]  # safe — sub is validated by the caller
    raw = cost_basis({"action": action, "user_id": sender_id})
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return f"P&L query returned bad response: {raw}"
    if payload.get("isError"):
        return payload.get("content", [{}])[0].get("text", "P&L query failed")
    content = payload.get("content") or []
    if content and content[0].get("text"):
        return content[0]["text"]
    return f"P&L ({sub}): empty result."


def register(ctx) -> None:
    """Wire balance and portfolio commands into Hermes."""
    ctx.register_command(
        name="balance",
        handler=handle_balance,
        description="Show native-token balance for the connected wallet",
        args_hint="[chain]",
    )
    ctx.register_command(
        name="portfolio",
        handler=handle_portfolio,
        description="Show native + common ERC-20 balances on the active chain",
        args_hint="[chain]",
    )
