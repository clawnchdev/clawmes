# clawmes

[![CI](https://github.com/clawnchdev/clawmes/actions/workflows/ci.yml/badge.svg)](https://github.com/clawnchdev/clawmes/actions/workflows/ci.yml)
[![Coverage](https://img.shields.io/badge/coverage-100%25-brightgreen)](https://github.com/clawnchdev/clawmes/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue)](https://github.com/clawnchdev/clawmes/blob/main/pyproject.toml)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

> Hermes Agent for crypto.

> [!WARNING]
> **Pre-alpha. Do not use real funds.** No real-network validation has happened yet, and no third-party security audit has been done. The signing paths are tested with mocks but unverified against live mainnet conditions. Use small testnet amounts only until v1.0. See [SECURITY.md](SECURITY.md) for the full threat model and recovery checklist.

Clawmes is a [Hermes Agent](https://github.com/NousResearch/hermes-agent) plugin. Wallets, DEX trading, lending and staking, governance, on-chain automation. Python rewrite of [`@clawnch/openclaw-crypto`](https://github.com/clawnchdev/openclawnch) targeting Hermes.

45 tools. 27 commands. 14 services. 11 hooks. Runs on Telegram, Discord, Slack, Signal, WhatsApp, iMessage, and LINE.

## Quick start

clawmes is not yet on PyPI; install from GitHub:

```bash
# 1. Install Hermes Agent (see https://github.com/NousResearch/hermes-agent)
# 2. Install clawmes as a Hermes plugin:
hermes plugins install clawnchdev/clawmes --enable
hermes clawmes init                # interactive setup wizard
hermes                             # start chatting
```

The `hermes clawmes init` wizard prompts for wallet mode (WalletConnect / local / Bankr), per-mode setup (project ID / password+mnemonic / API key), and optional API keys for the most-used integrations. It writes everything to `~/.hermes/.env` in upsert mode (existing keys preserved).

### Editable / dev install

```bash
git clone https://github.com/clawnchdev/clawmes
cd clawmes
pip install -e ".[dev]"
hermes plugins enable clawmes
hermes clawmes init
```

### PyPI publishing

The package is not yet published. v0.1.0 is GitHub-only. PyPI publication will land once the bridges-integration job has run a real-network smoke test and a third-party audit signs off the signing paths. Track [#1](https://github.com/clawnchdev/clawmes/issues) for the publish milestone.

## Tools

| Category | Tools | What it does |
|---|---|---|
| **Wallet** (4) | `clawnchconnect`, `transfer`, `permit2`, `approvals` | WalletConnect pairing, ENS transfers, Permit2 signed approvals, ERC-20 allowance management |
| **Trading** (8) | `defi_swap`, `defi_balance`, `defi_lend`, `defi_stake`, `defi_price`, `liquidity`, `manage_orders`, `bridge` | 0x DEX aggregator, Aave V3 lending, Lido/Rocket Pool staking, Uniswap V3 LP positions, limit/stop/trailing/DCA orders, LiFi cross-chain bridging |
| **Yield/Analytics** (4) | `yield`, `analytics`, `market_intel`, `cost_basis` | DeFiLlama yields, RSI/MACD/Bollinger TA, trending tokens via CoinGecko, FIFO P&L from local ledger |
| **Launches** (6) | `clawnch_launch`, `clawnch_fees`, `bankr_launch`, `bankr_automate`, `bankr_polymarket`, `bankr_leverage` | Token deploys on Base via Clawnch launchpad, Bankr-side automation rules, Polymarket predictions, Avantis perp leverage |
| **Ownership** (4) | `nft`, `airdrop`, `privacy`, `safe` | Reservoir NFT API, OZ Merkle distributor airdrop claims, Lobster privacy pools, Gnosis Safe multisig |
| **Governance** (2) | `governance`, `farcaster` | Snapshot + Tally proposals/voting, Neynar Farcaster cast/search/feed |
| **On-chain Intel** (4) | `block_explorer`, `herd_intelligence`, `watch_activity`, `browser` | Etherscan family, Herd whale tracking, persistent watch list, headless Playwright browsing |
| **Automation** (1) | `compound_action` | Multi-step plans (DCA, conditional triggers, loops) via plan scheduler |
| **Agent ops** (4) | `molten`, `clawnx`, `hummingbot`, `wayfinder` | X/Twitter posting, agent-to-agent matching, local Hummingbot market-making gateway, multi-step route optimization |
| **Memory** (3) | `agent_memory`, `skill_evolve`, `session_recall` | Hermes-backed persistent memory, agentic skill self-improvement, past-session search |
| **Misc** (5) | `giza`, `nookplot`, `paysponge`, `lobster_cash`, `_user_tools` | zkML inference, Farcaster analytics, fiat ramp, privacy pools, custom-tool dispatcher |

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
              ├── 45 tools     (registered via ctx.register_tool, write-gated)
              ├── 27 commands  (registered via ctx.register_command)
              ├── 11 hooks     (pre_tool_call, post_tool_call, pre_llm_call, ...)
              ├── 27 skills    (registered via ctx.register_skill, namespaced clawmes:*)
              ├── CLI subcmds  (registered via ctx.register_cli_command)
              └── 14 services  (start_all() starts background lifecycle)
                    │
                    ├── subprocess: clawmes-wc-bridge   (Node — WalletConnect v2)
                    └── subprocess: clawmes-sa-bridge   (Node — MetaMask Smart Accounts; planned)
```

Two bundled Node sub-process bridges (`clawmes-wc-bridge`, `clawmes-sa-bridge`) handle WalletConnect v2 sign-client and MetaMask Smart Accounts SDK respectively, talking JSON-line RPC over stdio. They install on first plugin load via `npm ci` against pinned `package-lock.json` files in the wheel.

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

Optional (per-tool — features degrade gracefully without their key):

```bash
# RPC + explorers
ALCHEMY_API_KEY=
BASESCAN_API_KEY=
ETHERSCAN_API_KEY=
ARBISCAN_API_KEY=
OPTIMISM_ETHERSCAN_API_KEY=
POLYGONSCAN_API_KEY=
CLAWMES_RPC_<chain_id>=  # override per-chain RPC URL

# DEX / bridge aggregators
ZEROX_API_KEY=
LIFI_API_KEY=

# Market data + analytics
COINGECKO_API_KEY=
HERD_ACCESS_TOKEN=

# Social
NEYNAR_API_KEY=
NEYNAR_SIGNER_UUID=
NOOKPLOT_API_KEY=

# NFT
RESERVOIR_API_KEY=

# Governance
TALLY_API_KEY=

# Specialized
GIZA_API_KEY=         # zkML inference
PAYSPONGE_API_KEY=    # fiat on/off-ramp
LOBSTER_API_KEY=      # privacy pools
MOLTEN_API_KEY=       # X (Twitter) integration
CLAWNX_API_KEY=       # agent-to-agent network
HUMMINGBOT_API_KEY=   # market-making gateway (also: HUMMINGBOT_GATEWAY_URL)
WAYFINDER_API_KEY=    # route optimization

# Token launches (override default contract addresses)
CLAWNCH_LAUNCHPAD_ADDRESS=
```

The setup wizard (`hermes clawmes init`) walks through the most-used keys interactively with live validation.

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
