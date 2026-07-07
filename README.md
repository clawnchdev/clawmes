# clawmes

[![CI](https://github.com/clawnchdev/clawmes/actions/workflows/ci.yml/badge.svg)](https://github.com/clawnchdev/clawmes/actions/workflows/ci.yml)
[![Coverage](https://img.shields.io/badge/coverage-100%25-brightgreen)](https://github.com/clawnchdev/clawmes/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue)](https://github.com/clawnchdev/clawmes/blob/main/pyproject.toml)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

> Hermes Agent for crypto.

> [!WARNING]
> **Pre-alpha. Do not use real funds.** No real-network validation has happened yet, and no third-party security audit has been done. The signing paths are tested with mocks but unverified against live mainnet conditions. Use small testnet amounts only until v1.0. See [SECURITY.md](SECURITY.md) for the full threat model and recovery checklist.

Clawmes is a [Hermes Agent](https://github.com/NousResearch/hermes-agent) plugin. Wallets, DEX trading, lending and staking, governance, on-chain automation. Python rewrite of [`@clawnch/openclaw-crypto`](https://github.com/clawnchdev/openclawnch) targeting Hermes.

52 tools. 97 commands. 29 services. 11 hooks. Runs on Telegram, Discord, Slack, Signal, WhatsApp, iMessage, and LINE.

## Quick start

```bash
# 1. Install Hermes Agent (see https://github.com/NousResearch/hermes-agent)
# 2. Install clawmes from PyPI:
pip install clawmes
hermes plugins enable clawmes
hermes clawmes init                # interactive setup wizard
hermes                             # start chatting
```

The `hermes clawmes init` wizard prompts for wallet mode (WalletConnect / local / Bankr), per-mode setup (project ID / password+mnemonic / API key), and optional API keys for the most-used integrations. It writes everything to `~/.hermes/.env` in upsert mode (existing keys preserved).

### Alternative: install direct from GitHub

```bash
hermes plugins install clawnchdev/clawmes --enable
hermes clawmes init
```

This skips PyPI and pulls from the latest `main`. Use it if you want a specific commit or pre-release.

### Editable / dev install

```bash
git clone https://github.com/clawnchdev/clawmes
cd clawmes
pip install -e ".[dev]"
hermes plugins enable clawmes
hermes clawmes init
```

Releases publish to PyPI automatically via [Trusted Publishing](https://docs.pypi.org/trusted-publishers/) on every `v*` tag push (no tokens, OIDC verified). See `.github/workflows/release.yml`.

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
| **Safety & identity** (2) | `policy_manage`, `agent_identity` | LLM-callable policy CRUD with propose→confirm flow, ed25519 keypair + did:key for verifiable agent identity |
| **Agent economy** (5) | `bv7x`, `bv7x_oracle`, `bv7x_market`, `a2a_call`, `eas_attestation` | BV-7X autonomous BTC oracle (signals + on-chain attestations + premium endpoints), generic A2A JSON-RPC client, EAS attestation reader on Base |
| **Misc** (5) | `giza`, `nookplot`, `paysponge`, `lobster_cash`, `_user_tools` | zkML inference, Farcaster analytics, fiat ramp, privacy pools, custom-tool dispatcher |

## Commands

100 slash commands across 17 categories. Commands run synchronously without invoking the LLM, so they're cheap and predictable.

| Category | Commands |
|---|---|
| **Wallet** (9) | `/wallet` `/connect` `/connect_local` `/connect_bankr` `/connect_base` `/disconnect` `/mode` `/chain` `/address` |
| **Wallet recovery** (4) | `/create_wallet` `/recover` `/export_wallet` `/wallet_backup` |
| **Policy & safety** (5) | `/policy` `/policy_clear` `/safemode` `/dangermode` `/audit` |
| **Delegation (EIP-7710/7715)** (3) | `/delegate` (`create` `revoke` `revoke-all` `status` `chains` `agent` `upgrade` `permissions`) `/delegations` `/revoke` — compile a policy into an on-chain delegation the agent redeems within cryptographically-enforced caveats |
| **Transactions** (4) | `/tx` `/tx_search` `/tx_export` `/pending` |
| **Plans & triggers** (10) | `/plans` `/plan` `/plan_logs` `/interrupt_plan` `/pause_plan` `/resume_plan` `/triggers` `/watch` `/unwatch` `/cron` |
| **Onboarding** (19) | `/welcome`, 5 personas (`/professional` `/degen` `/chill` `/technical` `/mentor`), 10 capability toggles (`/cap_wallet` `/cap_prices` `/cap_portfolio` `/cap_trading` `/cap_liquidity` `/cap_launchpad` `/cap_bridge` `/cap_routing` `/cap_clawnx` `/cap_hummingbot`), `/skip` `/back` `/reonboard` |
| **Balance & portfolio** (2) | `/balance` `/portfolio` |
| **Trading & discovery** (14) | `/buy` `/trending` `/my_launches` `/burn` `/onramp` `/leaderboard` `/claim` `/dca` `/copy` `/agent` `/alerts` `/limit_order` `/sniper` `/strategy` — quote-then-confirm swaps via 0x, hot tokens on Base, list your launches, burn $CLAWNCH for Clanker vault %, Coinbase Onramp deep link, top tokens/launchers/burners, sweep accumulated LP fees, dollar-cost averaging, copy-trade a wallet's buys, NL plan compiler, price + wallet activity alerts, limit buys + take-profit sells, auto-buy newly-launched tokens (Clawmes Unlimited), preset strategy templates (Clawmes Unlimited) |
| **Self-evolution** (3) | `/evolve` `/stable` `/evolution` — gates `agent_memory` and `skill_evolve` write actions; OFF by default |
| **Endpoint allowlist** (3) | `/allowlist` `/allow` `/disallow` — session-scoped host allowlist + 100-entry block audit ring |
| **Discoverability** (5) | `/skills` `/persona` `/chains` `/tools_list` `/safety_status` |
| **Info** (5) | `/history` `/clear_history` `/version` `/about` `/uptime` |
| **Agent identity** (1) | `/identity` — show/generate ed25519 keypair + did:key |
| **BV-7X** (2) | `/bv7x` `/btc` |
| **Launch (Clawnch)** (2) | `/launch` `/register_agent` — deploy a token from chat via the Clawnch launchpad |
| **Meta** (2) | `/doctor` `/help` |

A `pre_llm_call` hook injects the last 5 slash-command calls into LLM context, so the agent stops re-asking things you just answered via slash (`/balance` → "what's my balance?" → agent uses the cached result rather than re-fetching).

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
| **Local key** | Local encrypted | BIP-39 mnemonic generated locally, encrypted with scrypt + AES-256-GCM, stored in macOS Keychain or an encrypted file. Manage with `/create_wallet`, `/connect_local`, `/recover`, `/export_wallet`, `/wallet_backup`. |
| **Bankr** | Custodial | `/connect_bankr` or `BANKR_API_KEY`. Multi-chain custodial wallet. Good for automation-heavy setups + leverage + Polymarket. |

Spending policies set in natural language:

```
/policy approve transfers under 0.05 ETH on Base, max 10/hour
```

For programmatic policy management (the LLM can propose, you confirm), use the `policy_manage` tool — propose→confirm flow with `confirm_store` enforcement, plus list/get/disable/enable/delete/evaluate(dry-run)/usage/categories actions.

### On-chain delegation (EIP-7710 / EIP-7715)

Policies above are enforced by the app before a tx is sent. For **tamper-proof, on-chain** enforcement — so the agent can act autonomously but *cannot* exceed limits even if its logic is compromised — compile a policy into a delegation:

```
/delegate create my-policy --expiry 7d
/delegate create --per-call 0.1 --cap 1 --period daily --targets 0xRouter…
```

Two keys are involved: your wallet is the **delegator** (holds the funds, signs the delegation once via EIP-712); a locally-generated **agent key** is the **delegate** (redeems on-chain and pays gas). Limits become on-chain *caveats* — per-call value caps (`ValueLteEnforcer`), period spend budgets (`NativeToken`/`ERC20PeriodTransferEnforcer`), call limits (`LimitedCallsEnforcer`), target allowlists (`AllowedTargetsEnforcer`), absolute expiry (`TimestampEnforcer`) — checked by the MetaMask Delegation Framework's `redeemDelegations` on every action.

Once a delegation covers a write tool, stage 3 of the write gate redeems it automatically and **skips** the tool's own send path; if a caveat refuses the action the gate fails closed. On-chain enforcement requires the delegator to be a smart account — a local-key EOA can be upgraded in place with `/delegate upgrade` (EIP-7702). `/delegations` lists them, `/revoke <id>` disables one on-chain. MetaMask smart-account users can instead request ERC-7715 permissions with `/delegate permissions <policy>`.

Supported on Ethereum, Base (default), Arbitrum, Optimism, Polygon, Linea, and the Sepolia / Base Sepolia testnets (set `CLAWMES_RPC_<chainId>` for chains without a bundled default RPC). Implemented in pure Python — no Node subprocess — with ABI/EIP-712 output verified byte-for-byte against viem.

## Automation

The compound action engine lets users describe multi-step plans in natural language:

- **Time triggers** — `every day at 9am, check ETH price`
- **Price triggers** — `when ETH drops below $2000, swap 1 ETH to USDC`
- **On-chain triggers** — `when gas is under 10 gwei, execute the pending swap`
- **Conditionals** — `if my portfolio is down more than 5%, alert me`
- **Loops + parallel** — `DCA $100 into ETH every week for 12 weeks`

Plans persist to disk and survive restarts. Managed via `/plans`, `/interrupt_plan`. The plan tick loop is driven by Hermes' built-in cron daemon.

## Ecosystem integrations

Clawmes wires the following partner / ecosystem projects directly into the tool + slash-command surface:

- **[BV-7X](https://bv7x.ai)** — autonomous BTC signal oracle launched on the Clawnch launchpad two days after the launchpad went live. Daily on-chain predictions via EAS attestations on Base; ~60% live accuracy; real Polymarket wager bot. Clawmes wires the full public + auth-gated surface:
  - Slash: `/btc` (price + Fear & Greed + ETF flow in one line), `/bv7x` (track record + market regime + agent identity).
  - Tools: `bv7x` (agent + A2A + commerce), `bv7x_oracle` (signals + on-chain attestations + premium endpoints), `bv7x_market` (BTC market data).
  - Skill bundle: `clawmes:bv7x` documents the LLM-facing surface.
  - Premium endpoints (`oracle`, `oracle_premium`, `copy_trade_next`, `copy_trade_history`) require `BV7X_API_KEY` — hold ≥500M `$BV7X` and complete the wallet-verify at <https://bv7x.ai/terminal#developer>.

- **[gitlawb OpenGateway](https://gitlawb.com/opengateway)** — OpenAI-compatible LLM inference gateway. Routes Xiaomi MiMo, GMI Cloud, and more behind one endpoint, server-side secrets. Two integration modes: (1) point Hermes itself at OpenGateway via `hermes model` (config-only, no clawmes code change) — every LLM call the agent makes routes through it; (2) clawmes tools can call OpenGateway directly via `OpenGatewayService.chat_completion()` for targeted subtasks outside the main agent loop.

- **[EAS](https://attest.org) (Ethereum Attestation Service)** — generic on-chain attestation primitive on Base. The `eas_attestation` tool reads any attestation from the canonical EAS singleton (`0x4200…0021`) — BV-7X predictions are one example, but the tool also works for trust-score certificates, KYC results, and any other EAS-using protocol. Configurable `chain_id` + `eas_address` for other L2s.

- **A2A protocol** — generic agent-to-agent JSON-RPC 2.0 client (`a2a_call` tool). `discover` fetches a peer's AgentCard at `/.well-known/agent-card.json`; `send_task` posts JSON-RPC tasks. Works against any A2A-speaking peer; tested against BV-7X.

## Launch a token from chat

Clawmes wires the Clawnch launchpad end-to-end so any user can deploy a token from a chat message. As of v0.4.0, the default is **non-custodial** — your wallet signs the deploy tx directly, no API key needed.

**Every launch requires a verified burn of ≥1,000,000 $CLAWNCH** from the launching wallet to the dead address (within 24h of the deploy). Deploys without one are rejected with `burn_required`. The same burn claims the Clanker vault allocation (1M = 1%, up to 10M = 10%), so the minimum is never wasted.

```
/connect_local                               # or /connect (WalletConnect) / /connect_bankr

/launch name MyCoin
/launch symbol MC
/launch description The next big thing       # optional
/launch image https://i.imgur.com/mycoin.png # optional
/launch twitter mycoin                       # optional — also: /launch x
/launch website mycoin.xyz                   # optional
/launch telegram mycoinchat                  # optional
/launch farcaster mycoin                     # optional
/launch discord https://discord.gg/abc       # optional
/launch burn 1000000                         # required — signs + submits the 1M CLAWNCH burn
/launch confirm
# clawmes calls https://www.clawn.ch/api/prepare/deploy for unsigned
# Clanker factory calldata, then your wallet signs and submits.
# 80% fees to you, 20% platform fee preserved in the rewards array.
# Returns tx hash + token address.
```

Social handles get normalized (`mycoin` → `https://x.com/mycoin`); full URLs pass through unchanged. They're persisted to `tokenParams.metadata.socialMediaUrls` on the launchpad side and may render as badges on the launch detail page.

The burn can also be done standalone — any time before the launch (within 24h):

```
/burn 1000000                   # standalone burn: required minimum; grants 1% vault
/burn 10000000                  # max 10M CLAWNCH = 10% vault (Clanker maximum)
/burn last                      # show the last burn tx hash you signed via clawmes

# Then attach it to the draft:
/launch burn 0x<tx_hash>        # paste the hash (also works for external burns)
/launch confirm                 # deploy with the burn verified + vault applied
```

Curve: 1k tokens allocated per 1 CLAWNCH burned (1M → 1%, 10M → 10%). Vault tokens are subject to the Clanker 7-day lockup. See [`api/lib/burn.ts`](https://github.com/clawnchdev/clawnch/blob/main/api/lib/burn.ts) for the exact verification rules (sender = launching wallet, recipient = dead address, ≥1M, <24h old). Confirming without a verified burn returns `burn_required` with the exact requirements; an invalid hash returns `invalid_burn`.

### Custodial deploys (legacy path)

For users who can't pay deploy gas themselves, or who want server-paid gas with the existing agent-registration tracking:

```
/register_agent MyAgent | An agent that launches tokens
# clawmes posts a challenge, your wallet signs, clawn.ch returns an apiKey.
# Save it as CLAWNCH_API_KEY in ~/.hermes/.env.

/launch ... (same drafting steps, including /launch burn — the 1M burn is required here too)
/launch confirm --custodial
# Clawnch's deployer wallet submits the Clanker tx server-side;
# your wallet only signs the captcha. Subject to the 24h cooldown.
```

Bypass the cooldown by sending `0.005 ETH` to the Clawnch bypass recipient on Base, then `/launch bypass <tx_hash>` + `/launch confirm --custodial`. The bypass covers the cooldown only — it does not replace the mandatory burn.

### Export-only mode (for Base MCP, Claude Desktop, Cursor, ChatGPT)

clawmes can also emit the unsigned calldata for you to paste into another agent surface — useful if you want to launch via Base MCP's `send_calls` from Claude Desktop or any other MCP-compatible client:

```
/launch name MyCoin
/launch symbol MC
/launch burn 0x<tx_hash>        # the export path needs the verified burn too
/launch export
# Prints a JSON {chain: "base", calls: [{to, data, value}]} block
# ready to paste into Base MCP's send_calls.
```

### Launch alerts

```
/launch alerts                          # subscribe link + filter docs
/launch alerts base-mcp                 # client-side filter docs for base-mcp source
```

All Clawnch deploys post to the public `@ClawnchAlerts` Telegram channel — custodial ones from the server, non-custodial / base-mcp ones from the on-chain indexer cron. Filter client-side using Telegram's built-in keyword highlighting.

### How both deploy paths preserve fees

Whether custodial or non-custodial, every deploy hard-codes Clawnch's 20% platform fee into the Clanker `rewards.recipients` array (the user gets 80%). The non-custodial path verifies this server-side before returning calldata and refuses to return anything if the platform fee recipient env var is misconfigured — fail-closed on fee economics. See [`clawmes:clawnch-launch` skill](clawmes/skills/clawnch-launch/SKILL.md) for the LLM-facing surface and `clawnch_launch` / `clawnch_fees` tools for the same path under the LLM.

## Trading + discovery from chat

After launching (or any time): three commands cover the basic loop of finding tokens and buying them.

```
/trending                          # top 10 tokens on Base by 24h volume (DexScreener)
/trending --clawnch                # restrict to launchpad-deployed tokens
/trending 25                       # bump the limit

/buy MNEME 0.01                    # quote: resolves symbol via DexScreener
/buy MNEME 0.01 --clawnch          # restrict resolution to Clawnch-launched tokens
/buy 0x3FcD…7b07 0.01              # address bypasses symbol resolution
/buy confirm                       # signs the Permit2 + submits via 0x

/my_launches                       # tokens this agent deployed via Clawnch (default)
/my_launches --all                 # any ERC-20 the connected wallet ever created on Base
```

`/buy` uses the existing `defi_swap` tool under the hood — 0x aggregator with Permit2 (single signature, no separate `approve` tx). The quote step always renders the resolved address before signing, so you can verify you're buying the right token. `--all` resolution returns the highest-volume Base pair for the symbol; `--clawnch` additionally verifies the address against the Clawnch launches table.

### Leaderboards, fee claiming, dollar-cost averaging (v0.6.0)

```
/leaderboard                       # top 10 Clawnch tokens by 24h volume
/leaderboard launchers             # top 10 agents by recent launch count
/leaderboard burners               # top $CLAWNCH burners (stub — points at on-chain data)
/leaderboard launchers 25          # bump the limit (capped at 25)

/claim                             # preview your launches + claimable hint
/claim MNEME                       # collect LP fees for one token via Clanker locker
/claim 0x3FcD…7b07                 # full address also works
/claim all                         # sweep every launch you own — one tx per token

/dca add 0xa1F7…747be 0.001 1h     # buy 0.001 ETH of CLAWNCH every hour
/dca add 0x… 0.01 4h --slippage 50 --daily-cap 0.05 --max-total 1 --max-failures 5
                                   # full safeguard set on a single schedule
/dca list                          # show your schedules + next run time
/dca edit dca_abc 0xabc eth_amount 0.02
                                   # change one field (token, eth_amount, interval,
                                   # slippage_bps, daily_cap_eth, max_eth_total,
                                   # max_consecutive_failures)
/dca skip dca_abc123               # advance next run, no execution this round
/dca dry-run dca_abc123            # preview swap output, no submission
/dca status                        # global summary + service health
/dca pause dca_abc123              # suspend without removing
/dca resume dca_abc123             # re-arm
/dca cancel dca_abc123             # delete
/dca history dca_abc123            # past executions for a schedule
/dca tick                          # manually execute due schedules (testing/debug)
```

- `/leaderboard` hits `/api/tokens?sort=volume` + `/api/launches` and aggregates client-side. No new server-side endpoints required.
- `/claim` calls Clanker v4's `collectRewards(address)` on the LP Locker Fee Conversion contract (`0x63D2DfEA…14d93496`). The locker pays out to every recipient slot per token in one shot — caller pays gas, the slot allocations get distributed to the rewardRecipients on that position. Each tx emits a `ClaimedRewards` event with per-recipient `(amount0, amount1)` arrays.
- `/dca` schedules persist in `${HERMES_HOME}/clawmes/dca/schedules.json`. Interval grammar: `1m`, `30m`, `1h`, `4h`, `1d`, `1w` (1m floor, 1y ceiling). The `DcaSchedulerService` ticks automatically on the registry cadence — no manual cron wiring needed. Safeguards (slippage, daily-cap, max-total, max-failures) attach per-schedule; the `max-failures` counter auto-pauses runaway schedules so a broken swap path can't burn gas in a loop. `/dca tick` is preserved as a synchronous testing entrypoint.

### Copy-trading (v0.7.0)

```
/copy add 0xWhale… 0.001            # follow the whale's buys, copy at 0.001 ETH
/copy add 0x… 0.005 --slippage 200 --daily-cap 0.05 --max-total 1
                                    # full safeguard set on one follow
/copy add 0x… 0.001 --blocklist 0xspam,0xspam2
                                    # ignore known airdrop contracts
/copy list                          # show your follows
/copy edit copy_abc eth_per_copy 0.002
                                    # change one field (eth_per_copy, slippage_bps,
                                    # daily_cap_eth, max_eth_total, max_consecutive_failures,
                                    # blocklist)
/copy pause copy_abc                # stop watching without losing state
/copy resume copy_abc               # re-arm
/copy cancel copy_abc               # remove
/copy status                        # global summary + service health
/copy history copy_abc              # last 25 copies for one follow
/copy tick                          # manual poll (testing)
```

The `CopyTraderService` polls Basescan's `account.tokentx` for every active follow on each registry tick (~60s). New ERC-20 transfers into the watched wallet trigger a copy buy via `defi_swap` at the configured fixed ETH amount. Same safeguard surface as `/dca` v2 (slippage / daily cap / total cap / max consecutive failures) plus a per-follow blocklist for known airdrop / spam contracts. Per-tick cap of 20 copies keeps a runaway airdrop streak from spawning hundreds of buys at once.

State persists in `${HERMES_HOME}/clawmes/copy/follows.json`. The watcher seeds `last_seen_block` from the current chain head (minus a 10-block lookback for new follows) so the first tick doesn't replay 100 days of history.

### Natural-language plan compiler (v0.8.0)

```
/agent DCA 0.001 ETH of CLAWNCH every 1h
/agent buy 0.01 ETH of CLAWNCH then claim my fees
/agent follow 0xWhale… at 0.001 eth
/agent burn 1,000,000 CLAWNCH
/agent show           # re-print the parsed plan
/agent confirm        # materialize: runs each /command
/agent cancel         # discard
/agent examples       # full list of supported phrasings
```

`/agent` is a regex-based intent parser — not an LLM. Common trading phrasings ("DCA X of Y every Z", "buy X of Y", "follow N at M eth", "claim my fees", "burn N CLAWNCH", "top tokens", "show my launches", "balance") compile into a sequence of clawmes slash commands. Nothing materializes until you say `/agent confirm` — the draft lives per-sender in memory.

Multi-step prompts join with `then` (bare commas would split numbers like `1,000,000`). Each step routes through the matching `handle_*` function, so the underlying command's safeguards (`/dca` v2 caps, `/buy` quote-then-confirm, etc.) still apply at execution time.

### Alerts + token gating (v0.9.0)

```
/alerts add price CLAWNCH above 0.00002     # fire when price crosses
/alerts add wallet 0xWhale…                 # fire on any new ERC-20 receipt
/alerts list                                # all your alerts + last fire
/alerts edit alert_abc threshold_usd 0.0001 # change a price threshold
/alerts pause alert_abc                     # suspend
/alerts history alert_abc                   # past fires
/alerts status                              # global summary + service health
```

`/alerts` is notification-only — no transactions submitted, no wallet required. Price alerts auto-deactivate after firing (so a crossing doesn't repeatedly notify on every subsequent tick). Wallet alerts stay active and re-fire on each new tx because `last_seen_block` advances past the seen receipt. `AlertsSchedulerService` polls on the registry cadence (~60s).

**Token gating.** Power features in `/dca`, `/copy`, `/agent`, and `/alerts` unlock at the `HOLDER` tier — any wallet holding 10,000,000+ $CLAWNCH (~$105 at session-time price). Free tier still gets each feature with these caps:
- 1 active `/dca` schedule (HOLDER: unlimited + safeguard flags)
- 1 active `/copy` follow (HOLDER: unlimited)
- 3 active `/alerts` (HOLDER: unlimited)
- `/agent` single-step prompts (HOLDER: multi-step with `then` chains)

The gate reads your active wallet's $CLAWNCH balance via `eth_call`, caches the result for 60s, and never crashes a command on RPC error (transient failures fall back to free tier). Implementation in `clawmes/services/token_gate.py`.

### Clawmes Unlimited (v0.11.0)

A third tier above HOLDER for the most aggressive features. Hold **100,000,000+ $CLAWNCH** (~$1,050 at session-time price) to unlock:

```
/sniper add 0.005 --max-buys 5 --source clawmes --symbol-filter DOG|CAT
                            # auto-buy new Clawnch launches matching filters
/sniper add 0.01 --max-age 60 --slippage 200
                            # snipe any launch < 60s old at 2% slippage
/sniper list / pause / resume / cancel / edit / history / status

/agent --ai sweep my LP rewards then buy some clawnch
                            # LLM fallback when the regex parser can't match
                            # (regex runs first; LLM only sees what didn't parse)
```

- **`/sniper`** watches `/api/launches` on the registry tick and fires `defi_swap` buys for any newly-detected launch matching the configured filters. Filters compose: `--source` attribution, `--symbol-filter` regex, `--max-mcap` skip threshold, `--max-age` recency window (default 10min — older launches are presumed already sniped). Auto-exhausts after `--max-buys` (default 10) so a runaway launchpad can't drain your wallet. `SniperSchedulerService` drives the loop.
- **`/agent --ai`** layers on top of the existing intent matcher. The regex parser runs first; the LLM only sees segments that didn't match. Rewrites are re-validated through the same `_parse_one` path so the LLM can't smuggle invented commands. Powered by OpenGateway; falls back gracefully when OpenGateway errors.

### Paid-tier expansions (v0.12.0)

Four feature additions across existing commands. Each lifts a specific paid-tier use case.

```
/copy add 0xWhale… 0.001 --invert            # HOLDER — also mirror sells
/copy add 0xLead… 0.001 --multi 0xCo1,0xCo2  # UNLIMITED — follow multiple wallets
/copy add 0xWhale… 0.001 --invert --multi 0xCo1,0xCo2
                                              # both at once: track a cohort
                                              # of whales, copy buys + sells

/alerts add price CLAWNCH above 0.0001 --webhook https://hook.example.com/alert
                                              # HOLDER — POST a JSON payload
                                              # to your dashboard on fire

/sniper add 0.005 --auto-sell 100:50         # UNLIMITED — take-profit at +100%,
                                              # stop-loss at -50%, full lifecycle
                                              # automation per snipe
```

- **`/copy --invert`** captures the "whale exiting" signal. When the watched wallet sends an ERC-20 OUT (to a DEX router), we check our balance and submit a corresponding sell. Combined with the default buy-on-receive behavior you get full mirror trading.
- **`/copy --multi`** lets one follow record track multiple wallets. Polling iterates over every watched address each tick. Useful for cohort strategies: "watch these three whales; copy any buy from any of them."
- **`/alerts --webhook <url>`** delivers a JSON payload on fire with `alert_id`, `sender_id`, `type`, `fired_at`, `detail`, and the full fire metadata. Failures never block the alert tick — they're recorded on the fire history.
- **`/sniper --auto-sell <gain>:<loss>`** finishes the snipe lifecycle. After each successful snipe, the config anchors a watch to the buy-time USD price. Subsequent ticks compare current price against the gain/loss thresholds; when either fires, we sell our entire balance back to ETH. The watch transitions to `filled` on success or `close_failed` if the sell fails.

### Trader power-pack (v0.10.0)

```
/portfolio                  # live balances (unchanged) + P&L hint
/portfolio pnl              # overall realized + unrealized P&L
/portfolio realized         # closed positions only
/portfolio unrealized       # open positions at last price
/portfolio export           # full lot-by-lot ledger

/limit_order add buy CLAWNCH 0.01 below 0.00001
                            # buy 0.01 ETH of CLAWNCH when price drops
/limit_order add sell 0xToken… 1000000 above 0.00005
                            # take-profit sell at the threshold
/limit_order list           # all your orders + last attempt
/limit_order pause lim_abc  # suspend
/limit_order resume lim_abc # re-arm (paused orders only)
/limit_order cancel lim_abc # remove
/limit_order history lim_abc # past attempts (auto-fail after max-attempts)

/wallet tag trading         # bookmark the active wallet under "trading"
/wallet tags                # list all saved tags
/wallet show trading        # print address/chain/mode for one tag
/wallet untag trading       # remove

/copy add 0xWhale… 0.001 --pct 50
                            # copy each buy at 50% of target's outgoing ETH,
                            # capped at 0.001 ETH (HOLDER tier)
```

- `/portfolio` P&L views route to the existing `cost_basis` tool — FIFO matching, USD-marked unrealized positions, exportable lot ledger.
- `/limit_order` runs on the same scheduler pattern as `/dca` + `/copy`. State machine: `active → filled` on swap success, `active → failed` after `max_attempts` exhausted, `active → paused/cancelled` on manual mutation. Terminal states are sticky.
- `/wallet` tags are metadata-only bookmarks at `${HERMES_HOME}/clawmes/wallet/tags.json`. They don't replace the active session — switching wallets still goes through the existing `/connect*` flow.
- `/copy --pct N` reads the target tx's outgoing ETH via Basescan's `eth_getTransactionByHash`, scales to `N%`, caps at `eth_per_copy`. Falls back to fixed sizing when target tx had no ETH value (token-token swap) or when the lookup fails.

## Base ecosystem integration

clawmes is built around the Base network. v0.5.0 deepens that integration across four surfaces:

### Coinbase Smart Wallet via `/connect_base`

Connect your existing Base Account (the wallet you use in Base App) to clawmes via OAuth — same flow Base MCP uses:

```
/connect_base                       # emits an OAuth URL
# visit the URL, approve in Coinbase, copy the `code` query param from the redirect
/connect_base <code>                # finalizes the connection
```

Every tx + signature thereafter triggers an approval in your Base App, same UX as WalletConnect. No mnemonic to manage. Configure via `CLAWMES_BASE_ACCOUNT_CLIENT_ID` after registering an OAuth client on [Coinbase Developer Platform](https://portal.cdp.coinbase.com).

### `clawmes-mcp` — use clawmes from desktop AI

clawmes ships as a Model Context Protocol server in addition to the Hermes Agent plugin, so Claude Desktop / Cursor / ChatGPT / any MCP-compatible client can use its read tools directly:

```
pip install clawmes[mcp]
clawmes-mcp                         # runs the stdio MCP server
```

Configure Claude Desktop's `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{ "mcpServers": { "clawmes": { "command": "clawmes-mcp" } } }
```

Read tools exposed in v0.5.0: `defi_price`, `defi_balance`, `market_intel`, `clawnch_launch` (info), `clawnch_fees`, `bv7x_oracle`, `bv7x_market`, `nft`, `block_explorer`, `cost_basis`. Write tools (swap / deploy / transfer) require a connected wallet that doesn't transfer cleanly into the MCP context — those land in a follow-up that delegates signing to Base MCP via `send_calls`.

### Builder rewards on every clawmes swap + deploy

Every clawmes-initiated transaction on Base mainnet automatically appends Coinbase's `BASE_BUILDER_CODE` suffix to the calldata, attributing the on-chain activity to Clawnch's builder ID. The EVM ignores trailing calldata beyond the ABI-decoded args, so it's a harmless ~28-byte suffix that earns builder rewards proportional to the volume the plugin drives.

### Coinbase Onramp + Basenames + Base App deep links

```
/onramp 50                          # Coinbase Onramp link, $50 of ETH to your wallet
/buy jesse.base.eth 0.01            # Basenames resolve natively against Base L2 ENS
/launch confirm                     # success output includes a Base App deep link
```

`CLAWMES_COINBASE_ONRAMP_APP_ID` for production Onramp config; without it the command emits a generic Coinbase landing URL. Basename resolution is automatic — no flag needed. Base App URL pattern is overridable via `CLAWMES_BASE_APP_TOKEN_URL` if Coinbase's URL scheme shifts.

## Security

- WalletConnect mode: clawmes never holds unencrypted private keys.
- Every write tool gates through readonly check + policy evaluation + on-chain delegation (EIP-7710, redeemed within signed caveats; fails closed on refusal) + ledger record.
- Credential leak detection on every LLM-bound output.
- Prompt-injection-resistance guardrails in SOUL.md.
- Sequential write execution — never queues multiple txs.
- Bounded approvals — exact amounts, never unlimited.
- Outbound HTTP restricted to a curated default allowlist, with optional runtime session-scoped additions via `/allow <host>` (`/allowlist` shows defaults + user-added + last 100 blocked attempts).
- Transaction verification — always shows what a tx will do before executing.
- **Self-modification gate**: the agent's own memory (`agent_memory`) and skill files (`skill_evolve`) are write-gated through an explicit "evolution mode" — OFF by default. `/evolve` enables, `/stable` disables, `/evolution` shows status. Closes the prompt-injection drift vector where an attacker could rewrite the agent's own context.
- **Verifiable agent identity**: each agent has an ed25519 keypair + `did:key` encoding, independent from the wallet. Use the wallet key for on-chain transactions; use the DID key for protocol messages (MCP calls, capability delegations, attestation signatures). `/identity` to show/generate.

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
              ├── 52 tools     (registered via ctx.register_tool, write-gated)
              ├── 75 commands  (registered via ctx.register_command)
              ├── 11 hooks     (pre_tool_call, post_tool_call, pre_llm_call, ...)
              ├── 29 skills    (registered via ctx.register_skill, namespaced clawmes:*)
              ├── CLI subcmds  (registered via ctx.register_cli_command)
              └── 22 services  (start_all() starts background lifecycle)
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

# LLM inference gateway (gitlawb opengateway — OpenAI-compatible)
OPENGATEWAY_API_KEY=  # ogw_live_… — recommended; service runs unauthenticated without it
                      # during the gitlawb partnership window (auth optional today)
OPENGATEWAY_MODEL=    # optional default model id sent when callers omit model=

# Venice AI (privacy-first, OpenAI-compatible — https://docs.venice.ai/models/overview)
VENICE_API_KEY=       # required — Venice answers unauthenticated calls with HTTP 402 (x402)
VENICE_MODEL=         # optional default model id (catalog: GET https://api.venice.ai/api/v1/models)

# Inference provider selection for clawmes' own tools (research summary, /agent --ai).
# venice | opengateway. If unset: Venice when VENICE_API_KEY is present, else OpenGateway.
CLAWMES_LLM_PROVIDER=

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

# Clawnch launchpad (token deploys via /launch + /register_agent)
CLAWNCH_API_KEY=      # issued by /register_agent or POST /api/agents/register on clawn.ch

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

### Using OpenGateway as your LLM provider

Clawmes ships under a partnership with [gitlawb OpenGateway](https://gitlawb.com/opengateway) — an OpenAI-compatible inference gateway that routes a single endpoint across model providers (Xiaomi MiMo, GMI Cloud, more). There are two integration modes; you can use either or both.

**Mode 1 — Route the whole Hermes stack through OpenGateway (recommended for most users).**

Hermes already supports any OpenAI-compatible endpoint as a "custom" provider. Point it at OpenGateway via `hermes model`:

```
$ hermes model
# Pick: "custom endpoint" (or equivalent in your Hermes version)
# Base URL:  https://opengateway.gitlawb.com/v1
# API key:   ogw_live_…   (sign in at https://gitlawb.com/opengateway/dashboard to generate one)
# Model:     mimo-v2.5-pro (or any model OpenGateway routes; check https://opengateway.gitlawb.com/health)
```

This is config-only — no clawmes code change required. Every LLM call the agent makes (conversation, tool routing, summarization, everything Hermes drives) goes through OpenGateway, with secrets staying server-side. During the gitlawb partnership window the gateway accepts unauthenticated traffic, so you can try it without a key — but auth will become required, so generate one early.

**Mode 2 — Targeted LLM calls from clawmes tools (advanced).**

Clawmes also ships `OpenGatewayService` (`clawmes/services/opengateway.py`) — a service that gives any clawmes tool a first-class LLM client for subtasks outside the main agent loop (intent classification, swap-parameter extraction, governance-proposal summarization, etc). Tools opt in by calling:

```python
from clawmes.services.opengateway import get_opengateway_service

result = get_opengateway_service().chat_completion(
    messages=[{"role": "user", "content": "..."}],
    model="mimo-v2.5-pro",
)
content = result["choices"][0]["message"]["content"]
```

This is independent from Mode 1 — Mode 2 calls go to OpenGateway regardless of which provider Hermes itself uses. Configure via the `OPENGATEWAY_API_KEY` / `OPENGATEWAY_MODEL` env vars above.

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
