"""MCP server that exposes a curated set of clawmes read tools.

Architecture:

  * The official ``mcp`` Python SDK (Anthropic) provides the stdio
    transport + JSON-RPC plumbing. We just register handlers for
    ``tools/list`` and ``tools/call``.
  * Each exposed clawmes tool gets wrapped into the MCP shape: name,
    description, input JSON schema, and a sync handler.
  * Tools execute the underlying clawmes function — same registry as
    the Hermes Agent path — and return the JSON envelope as MCP text
    content.

The tool selection is intentionally read-only for v1: every exposed
tool either reads on-chain data, queries external APIs, or surfaces
clawnch index records. None requires a connected wallet on the
clawmes side. Writes (swap / deploy / transfer) need wallet state
that doesn't transfer cleanly into an MCP context — those land in a
follow-up that delegates signing to Base MCP via ``send_calls``.

Adding a new tool to the exposed set: append its module + factory to
``_TOOL_FACTORIES``. Each factory returns the decorated clawmes
function (the one with the ``_clawmes_meta`` attribute set by
``@read_tool``).
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import TYPE_CHECKING, Any, Callable

from clawmes import __version__

if TYPE_CHECKING:  # pragma: no cover — type-check-only imports
    from mcp.server.lowlevel import Server
    from mcp.types import TextContent, Tool


def _import_defi_price() -> Callable[..., str]:
    from clawmes.tools.defi_price import defi_price

    return defi_price


def _import_defi_balance() -> Callable[..., str]:
    from clawmes.tools.defi_balance import defi_balance

    return defi_balance


def _import_market_intel() -> Callable[..., str]:
    from clawmes.tools.market_intel import market_intel

    return market_intel


def _import_clawnch_launch() -> Callable[..., str]:
    from clawmes.tools.clawnch_launch import clawnch_launch

    return clawnch_launch


def _import_clawnch_fees() -> Callable[..., str]:
    from clawmes.tools.clawnch_fees import clawnch_fees

    return clawnch_fees


def _import_bv7x_oracle() -> Callable[..., str]:
    from clawmes.tools.bv7x_oracle import bv7x_oracle

    return bv7x_oracle


def _import_bv7x_market() -> Callable[..., str]:
    from clawmes.tools.bv7x_market import bv7x_market

    return bv7x_market


def _import_nft() -> Callable[..., str]:
    from clawmes.tools.nft import nft

    return nft


def _import_block_explorer() -> Callable[..., str]:
    from clawmes.tools.block_explorer import block_explorer

    return block_explorer


def _import_cost_basis() -> Callable[..., str]:
    from clawmes.tools.cost_basis import cost_basis

    return cost_basis


#: Lazy importers — keep startup fast (no full plugin discovery just
#: to ship the MCP server). Each entry maps the MCP-side name to a
#: zero-arg factory that returns the decorated clawmes function.
_TOOL_FACTORIES: dict[str, Callable[[], Callable[..., str]]] = {
    "defi_price": _import_defi_price,
    "defi_balance": _import_defi_balance,
    "market_intel": _import_market_intel,
    "clawnch_launch": _import_clawnch_launch,
    "clawnch_fees": _import_clawnch_fees,
    "bv7x_oracle": _import_bv7x_oracle,
    "bv7x_market": _import_bv7x_market,
    "nft": _import_nft,
    "block_explorer": _import_block_explorer,
    "cost_basis": _import_cost_basis,
}


def _build_tool_list() -> list["Tool"]:
    """Reflect each exposed clawmes tool into an MCP ``Tool`` definition."""
    from mcp.types import Tool

    tools: list[Tool] = []
    for name, factory in _TOOL_FACTORIES.items():
        fn = factory()
        meta = getattr(fn, "_clawmes_meta", None)
        if meta is None:
            # Defensive — every @read_tool / @write_tool sets this.
            # Skip rather than crash if a clawmes refactor breaks the
            # invariant; the server stays useful for the rest of the set.
            continue
        tools.append(
            Tool(
                name=name,
                description=meta.get("description") or "",
                inputSchema=meta.get("schema") or {"type": "object", "properties": {}},
            )
        )
    return tools


def _call_tool(name: str, arguments: dict[str, Any] | None) -> str:
    """Dispatch an MCP ``tools/call`` to the underlying clawmes function."""
    factory = _TOOL_FACTORIES.get(name)
    if factory is None:
        return json.dumps(
            {
                "isError": True,
                "content": [{"type": "text", "text": f"Unknown clawmes tool: {name}"}],
            }
        )
    fn = factory()
    args_dict: dict[str, Any] = dict(arguments or {})
    try:
        return fn(args_dict)
    except Exception as exc:  # noqa: BLE001 — surface to MCP client
        return json.dumps(
            {
                "isError": True,
                "content": [
                    {"type": "text", "text": f"Tool {name} raised {type(exc).__name__}: {exc}"}
                ],
            }
        )


def _wrap_call_tool_result(result: str) -> list["TextContent"]:
    """Wrap a clawmes tool's JSON-string output as MCP TextContent.

    Split out from the decorator-wrapped closure inside ``build_server``
    so tests can verify the wrapping shape directly without going through
    the MCP SDK's request-dispatch plumbing.
    """
    from mcp.types import TextContent

    return [TextContent(type="text", text=result)]


def build_server() -> "Server":
    """Build the MCP Server object with clawmes tool handlers registered.

    Split out from :func:`main` so tests can drive the server in-process
    without running the stdio loop.
    """
    from mcp.server.lowlevel import Server
    from mcp.types import TextContent, Tool

    server: Server = Server(name="clawmes", version=__version__)

    @server.list_tools()
    async def _list_tools() -> list[Tool]:
        return _build_tool_list()

    @server.call_tool()
    async def _call(  # pragma: no cover — exercised via stdio integration smoke test
        name: str, arguments: dict[str, Any] | None = None
    ) -> list[TextContent]:
        return _wrap_call_tool_result(_call_tool(name, arguments))

    return server


async def _run() -> None:
    import mcp.server.stdio

    server = build_server()
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def main() -> None:
    """CLI entrypoint — ``clawmes-mcp`` from pyproject.toml [scripts]."""
    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":  # pragma: no cover — script entry
    main()
