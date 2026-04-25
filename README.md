# clawmes

> Hermes Agent for crypto. The hottest open-source AI assistant can now handle real money.

Clawmes is a [Hermes Agent](https://github.com/NousResearch/hermes-agent) plugin that turns Hermes into an infinitely extensible crypto-native assistant. It's a Python rewrite of [`@clawnch/openclaw-crypto`](https://github.com/clawnchdev/openclawnch) targeting Hermes instead of OpenClaw.

48 tools. 118 commands. 76 services. Runs on Telegram, Discord, Slack, Signal, WhatsApp, iMessage, and LINE.

## Quick start

```bash
pip install hermes-agent           # if you don't already have Hermes
pip install clawmes                # PyPI
hermes clawmes init                # interactive setup wizard
hermes                             # start chatting
```

The `hermes clawmes init` wizard validates LLM keys live, writes secrets to `~/.hermes/.env`, writes config to `~/.hermes/config.yaml`, enables the plugin, copies SOUL.md if absent, and seeds the bundled skills.

### Other install methods

**GitHub (no PyPI):**
```bash
hermes plugins install clawnchdev/clawmes --enable
hermes clawmes init
```

**Editable / dev:**
```bash
git clone https://github.com/clawnchdev/clawmes
cd clawmes
pip install -e ".[dev]"
hermes plugins enable clawmes
hermes clawmes init
```

## Tools

| Category | Tools | What it does |
|---|---|---|
| **Wallet** | `clawnchconnect`, `transfer`, `permit2`, `approvals` | WalletConnect pairing, ENS transfers, token approvals, spending policies |
| **Trading** | `defi_swap`, `defi_balance`, `liquidity`, `manage_orders`, `bridge` | 6 DEX aggregators, limit/stop/trailing orders, DCA, cross-chain bridging |
| **DeFi** | `defi_lend`, `defi_stake`, `yield` | Aave V3 supply/borrow, Lido/Rocket Pool staking, Yearn V3 vaults |
| **Market data** | `defi_price`, `analytics`, `market_intel`, `cost_basis` | RSI/MACD/Bollinger bands, trending tokens, whale activity, FIFO P&L tracking |
| **Token launches** | `clawnch_launch`, `clawnch_fees` | Deploy ERC-20s on Base via Clawnch launchpad with Uniswap V4 pools |
| **Bankr** | `bankr_launch`, `bankr_automate`, `bankr_polymarket`, `bankr_leverage` | Custodial wallet, automation rules, Polymarket predictions, leveraged positions |
| **NFT & Airdrop** | `nft`, `airdrop` | ERC-721 mint/transfer/burn, airdrop eligibility checking, claim generation |
| **Security** | `privacy`, `safe` | Privacy-preserving transfers, Gnosis Safe multisig management |
| **Governance** | `governance`, `farcaster` | DAO proposal voting, Farcaster casting/search/notifications |
| **On-chain Intel** | `block_explorer`, `herd_intelligence`, `watch_activity`, `browser` | Contract source, token audits, swap monitoring, web browsing |
| **Automation** | `compound_action` | Multi-step plans with conditionals, time/price/on-chain triggers |
| **Agent** | `molten`, `clawnx`, `hummingbot`, `wayfinder` | X/Twitter posting, agent-to-agent matching, market-making, route optimization |
| **Memory** | `agent_memory`, `skill_evolve`, `session_recall` | Persistent memory, self-improvement, session context recall |

## Channels

| Channel | Status | Notes |
|---|---|---|
| Telegram | Production | Slash menu auto-registered, deep links, streaming, voice transcription via Hermes |
| Discord | Ready | Slash commands auto-register, thread bindings |
| Slack | Ready | Channels and DMs |
| Signal | Ready | Requires `signal-cli` bridge (Hermes-managed) |
| WhatsApp | Ready | Requires WhatsApp Web bridge (Hermes-managed) |
| iMessage | Ready | macOS only — Hermes' bluebubbles adapter |
| LINE | Ready | Requires LINE Messaging API |

All tools and commands work identically on every channel.

## Wallet modes

| Mode | Key custody | How it works |
|---|---|---|
| **WalletConnect** | Your phone wallet | `/connect` generates a pairing link via the bundled Node WC bridge. Every write tx goes to your phone for approval. |
| **Local key** | Local encrypted | BIP-39 mnemonic generated locally, encrypted with scrypt + AES-256-GCM, stored in macOS Keychain or encrypted file. |
| **Bankr** | Custodial | `/connect_bankr` or `BANKR_API_KEY`. Multi-chain custodial wallet. Good for automation-heavy setups + leverage + Polymarket. |

Spending policies set in natural language:

```
/policy approve transfers under 0.05 ETH on Base, max 10/hour
```

## Automation

The compound action engine lets users describe multi-step plans in natural language:

- **Time triggers** — `every day at 9am, check ETH price`
- **Price triggers** — `when ETH drops below $2000, swap 1 ETH to USDC`
- **On-chain triggers** — `when gas is under 10 gwei, execute the pending swap`
- **Conditionals** — `if my portfolio is down more than 5%, alert me`
- **Loops + parallel** — `DCA $100 into ETH every week for 12 weeks`

Plans persist to disk and survive restarts. Managed via `/plans`, `/interrupt_plan`. The plan tick loop is driven by Hermes' built-in cron daemon.

## Security

- WalletConnect mode: clawmes never holds unencrypted private keys.
- Every write tool gates through readonly check + policy evaluation + delegation execution + ledger record.
- Credential leak detection on every LLM-bound output.
- Prompt-injection-resistance guardrails in SOUL.md.
- Sequential write execution — never queues multiple txs.
- Bounded approvals — exact amounts, never unlimited.
- Outbound HTTP restricted to a curated allowlist.
- Transaction verification — always shows what a tx will do before executing.

## CLI subcommands

```
hermes clawmes init              Interactive setup wizard
hermes clawmes doctor            Diagnostics
hermes clawmes wallet            Wallet status / mode switch
hermes clawmes plans             Plan status / list / cancel
hermes clawmes policy            Policy status / set / clear
hermes clawmes persona reinstall Force-overwrite SOUL.md (with confirm)
hermes clawmes skills install    Copy bundled skills to writable user namespace
hermes clawmes update            pip install -U clawmes + bridge refresh
hermes clawmes version           Show version
hermes clawmes uninstall         Remove from plugins.enabled (state preserved)
```

## Architecture

```
hermes (the upstream CLI, hermes-agent ≥ 2026.4.x)
  └── PluginManager.discover_and_load()
        └── clawmes.register(ctx)
              ├── 48 tools     (registered via ctx.register_tool, write-gated)
              ├── 118 commands (registered via ctx.register_command)
              ├── ~7 hooks     (pre_tool_call, post_tool_call, pre_llm_call, ...)
              ├── 41 skills    (registered via ctx.register_skill, namespaced clawmes:*)
              ├── CLI subcmds  (registered via ctx.register_cli_command)
              └── 76 services  (start_all() starts background lifecycle)
                    │
                    ├── subprocess: clawmes-wc-bridge   (Node — WalletConnect v2)
                    └── subprocess: clawmes-sa-bridge   (Node — MetaMask Smart Accounts)
```

Two bundled Node sub-process bridges (`clawmes-wc-bridge`, `clawmes-sa-bridge`) handle WalletConnect v2 sign-client and MetaMask Smart Accounts SDK respectively, communicated to via JSON-line RPC over stdio. They install on first plugin load via `npm ci` against pinned `package-lock.json` files in the wheel.

## Configuration

Required in `~/.hermes/.env`:

```bash
# LLM (one of these — Hermes' standard)
ANTHROPIC_API_KEY=
OPENROUTER_API_KEY=
OPENAI_API_KEY=
NOUS_PORTAL_API_KEY=

# Channel (one of these — Hermes' standard)
TELEGRAM_BOT_TOKEN=
DISCORD_TOKEN=
SLACK_BOT_TOKEN=

# Wallet — pick one mode
WALLETCONNECT_PROJECT_ID=
CLAWMES_LOCAL_KEY_PASSWORD=
BANKR_API_KEY=
```

Optional (tools degrade gracefully without):

```bash
ALCHEMY_API_KEY=
ZEROX_API_KEY=
ONEINCH_API_KEY=
LIFI_API_KEY=
BASESCAN_API_KEY=
ETHERSCAN_API_KEY=
COINGECKO_API_KEY=
HERD_ACCESS_TOKEN=
NEYNAR_API_KEY=
RESERVOIR_API_KEY=
```

The setup wizard (`hermes clawmes init`) walks through everything interactively with live key validation.

## Development

```bash
git clone https://github.com/clawnchdev/clawmes
cd clawmes
pip install -e ".[dev]"

pytest                            # run tests
ruff check clawmes/               # lint
mypy clawmes/                     # type-check
```

### Adding a tool

```python
# clawmes/tools/my_tool.py
from clawmes.tools.registry import write_tool, register_with_ctx
from clawmes.lib.tool_result import json_result

_SCHEMA = {...}                  # OpenAI function-calling schema

@write_tool(name="my_tool", toolset="clawmes-misc", schema=_SCHEMA, description="…")
def my_tool(args, **kwargs):
    return json_result({...})

def register(ctx):
    register_with_ctx(ctx, my_tool)
```

Then import and call `register(ctx)` from `clawmes/tools/__init__.py:register_all()`.

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for full guidelines and patterns.

## Status

Early development. See [`CHANGELOG.md`](CHANGELOG.md) for milestone progress and [`HERMES_PARITY.md`](HERMES_PARITY.md) for the upstream Hermes API contract.

## Tech stack

| Component | Version |
|---|---|
| Hermes Agent | ≥ 2026.4.23 |
| Python | ≥ 3.11 |
| web3.py | ≥ 7.0 |
| viem (in Node bridges) | ≥ 2.x |

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for development setup, code style, and PR process.

## License

MIT — see [`LICENSE`](LICENSE).
