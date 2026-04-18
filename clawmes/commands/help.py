"""``/help`` and ``/help <category>`` commands.

Renders a category-aware command list. The category map is the same one
used by ``/help <category>`` in openclawnch and matches the toolset
groupings in PRD §8.13.
"""

from __future__ import annotations

_CATEGORIES: dict[str, list[str]] = {
    "trading": [
        "/swap <amount> <token_in> to <token_out>",
        "/limit <amount> <token_in> to <token_out> at <price>",
        "/dca <amount> <token> every <period>",
        "/bridge <amount> <token> to <chain>",
    ],
    "defi": [
        "/lend <amount> <token>",
        "/borrow <amount> <token>",
        "/stake <amount> <token>",
        "/yield <amount> <token>",
        "/liquidity ...",
    ],
    "wallet": [
        "/connect",
        "/disconnect",
        "/connect_bankr",
        "/connect_local",
        "/wallet",
        "/balance",
        "/mode <walletconnect|local|bankr>",
        "/chain <id>",
    ],
    "portfolio": [
        "/balance",
        "/cost",
        "/tx",
        "/tx_search <query>",
        "/tx_export <range>",
    ],
    "tools": [
        "/setup",
        "/doctor",
        "/version",
        "/changelog",
    ],
    "agents": [
        "/agents",
        "/spawn <task>",
        "/handoff <agent>",
        "/match",
    ],
    "bankr": [
        "/connect_bankr",
        "/topup",
        "/autotopup <amount>",
        "/bankr_status",
        "/bankr_balance",
    ],
}


async def handle_help(raw_args: str) -> str:
    args = raw_args.strip().lower()
    if not args:
        return _full_help()
    if args in _CATEGORIES:
        return _category_help(args)
    return f"Unknown category: {args!r}\n\n{_list_categories()}"


def _full_help() -> str:
    parts = ["Clawmes commands. Use `/help <category>` for details.", ""]
    parts.append(_list_categories())
    parts.append("")
    parts.append("Or run `/setup` for setup, `/doctor` for diagnostics.")
    return "\n".join(parts)


def _category_help(category: str) -> str:
    cmds = _CATEGORIES[category]
    body = "\n".join(f"  {c}" for c in cmds)
    return f"`{category}` commands:\n{body}"


def _list_categories() -> str:
    return "Categories: " + ", ".join(sorted(_CATEGORIES.keys()))


def register(ctx) -> None:
    ctx.register_command(
        name="help",
        handler=handle_help,
        description="Show clawmes commands. Optional category argument.",
        args_hint="[category]",
    )
