"""``clawmes_info`` — agent-callable bridge to clawmes's read-only command surface.

The Hermes Desktop app curates its slash-command autocomplete to a built-in
allowlist (``apps/desktop`` → ``desktop-slash-commands.ts``), so clawmes's
plugin slash commands don't appear in the menu and their output renders as a
status line rather than a chat bubble. This tool re-exposes the key
*informational* commands as a single **agent-callable tool**: the agent invokes
it from natural language ("research CLAWNCH", "what's my wallet balance"), and
the result renders as a normal, selectable tool card. Any HTML card the
underlying command generates (e.g. ``/research``) is surfaced as a preview
attachment via ``json_result(preview=...)``.

Read-only by design — every bridged op only reads. Write actions keep their own
gated tools (``defi_swap``, ``transfer``, …) and slash commands; they are
intentionally NOT reachable here.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import importlib
import re
from typing import Any

from clawmes.lib.params import read_str
from clawmes.lib.tool_result import error_result, json_result
from clawmes.tools.registry import read_tool, register_with_ctx

# op -> (module path, async handler name, one-line description for the schema)
_OPS: dict[str, tuple[str, str, str]] = {
    "wallet": (
        "clawmes.commands.wallet",
        "handle_wallet",
        "Connected wallet: address, chain, balance, policies.",
    ),
    "balance": (
        "clawmes.commands.balance",
        "handle_balance",
        "Native-token balance. args: optional chain.",
    ),
    "portfolio": (
        "clawmes.commands.balance",
        "handle_portfolio",
        "Native + common ERC-20 balances. args: optional chain.",
    ),
    "research": (
        "clawmes.commands.research",
        "handle_research",
        "Token research: price, liquidity, volume, risk flags, links. args: <token>.",
    ),
    "scan": (
        "clawmes.commands.scan",
        "handle_scan",
        "Analyze a wallet: holdings, recent activity, risk flags. args: <wallet address>.",
    ),
    "trending": (
        "clawmes.commands.trending",
        "handle_trending",
        "Top tokens by 24h volume on Base. args: optional [--clawnch|--all] [limit].",
    ),
    "leaderboard": (
        "clawmes.commands.leaderboard",
        "handle_leaderboard",
        "Top on Clawnch. args: optional tokens|launchers|burners.",
    ),
    "my_launches": (
        "clawmes.commands.my_launches",
        "handle_my_launches",
        "Tokens you've launched. args: optional [--clawnch|--all].",
    ),
}

# Pull a generated card path out of a command's text output so it renders as a
# desktop preview attachment. Matches the absolute card path written by
# ``clawmes.lib.ui_cards.write_card`` (``${HERMES_HOME}/clawmes/cards/*.html``).
_CARD_RE = re.compile(r"(/[^\s'\"]+/clawmes/cards/[^\s'\"]+\.html)")

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "op": {
            "type": "string",
            "enum": sorted(_OPS),
            "description": "Which read to run. "
            + " ".join(f"{k}: {v[2]}" for k, v in _OPS.items()),
        },
        "args": {
            "type": "string",
            "description": (
                "Optional argument string for the op — e.g. a token symbol for "
                "research (CLAWNCH), a wallet address for scan (0x…), or a chain "
                "for balance (base)."
            ),
        },
    },
    "required": ["op"],
}


def _run_coro(coro: Any) -> str:
    """Drive an async command handler to completion from a sync tool.

    Handles both invocation contexts: no running event loop (the common
    tool-call path → ``asyncio.run``) and an already-running loop (run in a
    worker thread with its own loop so we never call ``asyncio.run`` inside a
    live loop).
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(asyncio.run, coro).result()


@read_tool(
    name="clawmes_info",
    toolset="clawmes-trading",
    description=(
        "Query clawmes for crypto info from natural language: wallet status, "
        "balances, portfolio, token research, wallet scan, trending tokens, "
        "Clawnch leaderboard, and your launches. Pick `op` and optional `args` "
        "(e.g. op=research args=CLAWNCH, or op=scan args=0xWallet). Read-only — "
        "use defi_swap / transfer for actions."
    ),
    schema=_SCHEMA,
    emoji="\U0001f50e",
)
def clawmes_info(args: dict[str, Any], **_kwargs: Any) -> str:
    op = (read_str(args, "op", required=True) or "").strip().lower()
    arg_str = read_str(args, "args") or ""

    entry = _OPS.get(op)
    if entry is None:
        return error_result(
            f"Unknown op {op!r}. Choose one of: {', '.join(sorted(_OPS))}.",
            code="param_error",
        )

    module_path, handler_name, _desc = entry
    handler = getattr(importlib.import_module(module_path), handler_name)
    output = _run_coro(handler(arg_str))

    match = _CARD_RE.search(output)
    preview = match.group(1) if match else None
    return json_result(
        {"op": op, "args": arg_str, "output": output},
        summary=output,
        preview=preview,
    )


def register(ctx) -> None:
    register_with_ctx(ctx, clawmes_info)
