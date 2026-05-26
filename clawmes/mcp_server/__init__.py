"""``clawmes-mcp`` — exposes clawmes read tools as an MCP server.

Distribution surface in addition to the Hermes Agent plugin:

  * Chat platforms (Telegram / Discord / Slack via Hermes) — the
    historical clawmes surface, full read + write tools available.
  * **Desktop AI clients** (Claude Desktop, Cursor, ChatGPT, any other
    MCP-compatible client) — install ``clawmes-mcp`` as an MCP server
    to get clawmes' read tools available as MCP tools.

Why read-only first: write tools (swap, deploy, transfer) need a
connected wallet, and clawmes' wallet modes (WalletConnect, local-key,
Bankr, Base Account) all assume a stateful clawmes session managing
the wallet. MCP clients are stateless from the wallet perspective —
they delegate signing to a sibling MCP server like Base MCP.

For writes through MCP, users should:

  1. Install ``clawmes-mcp`` for read / discovery
  2. Install Base MCP for signing
  3. Use clawmes-mcp to gather context, then Base MCP's
     ``send_calls`` to execute

A future iteration will expose write tools that return unsigned
calldata + delegate to Base MCP's ``send_calls`` for execution.

Optional install:

  .. code-block:: bash

     pip install clawmes[mcp]

Run via stdio (the standard MCP transport):

  .. code-block:: bash

     clawmes-mcp

Configure Claude Desktop's ``~/Library/Application Support/Claude/claude_desktop_config.json``:

  .. code-block:: json

     {
       "mcpServers": {
         "clawmes": { "command": "clawmes-mcp" }
       }
     }
"""

from __future__ import annotations

from clawmes.mcp_server.server import main

__all__ = ["main"]
