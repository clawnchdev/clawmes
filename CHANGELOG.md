# Changelog

All notable changes to clawmes are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

## 0.9.0 — 2026-05-27

### Added — token-gated power features + `/alerts`

Two new surfaces that together push more $CLAWNCH demand to clawmes
power users while expanding what the agent can react to.

- **Token gating** (`clawmes/services/token_gate.py`) — power features
  unlock based on $CLAWNCH balance. Two tiers:
  - `FREE` (no balance required) — `/buy`, `/trending`, `/balance`,
    `/leaderboard`, `/claim`, `/onramp`, `/launch`, `/burn`,
    `/agent` single-step. Capped versions of recurring features: 1
    active `/dca`, 1 active `/copy`, 3 active `/alerts`, no
    safeguard flags on `/dca`.
  - `HOLDER` — any wallet holding **10,000+ $CLAWNCH** (~$0.10).
    Unlocks unlimited `/dca` schedules + safeguard flags,
    unlimited `/copy` follows, `/agent` multi-step prompts,
    unlimited `/alerts`.
  - Implementation: lazy balance read via `eth_call`, 60-second
    cache to avoid hammering RPC. Gate helpers
    (`check_tier_or_error`, `check_cap_or_error`) return a
    human-readable error string when blocked, with the exact
    balance shortfall + how-to-buy hints. No wallet connected →
    treated as free tier; the gate never crashes a command.

- **`/alerts`** — price + wallet-activity alerts. Notification-only;
  no transactions submitted.
  - `/alerts add price <token> <above|below> <usd>` — fire when token
    price crosses the threshold. Polls `defi_price` on the registry
    tick. Auto-deactivates after firing so we don't re-notify.
  - `/alerts add wallet <address>` — fire on any new ERC-20 receipt
    to the watched wallet. Same Basescan poller as `/copy`. Stays
    active so each new tx fires.
  - `/alerts list` / `pause` / `resume` / `cancel` / `edit` /
    `tick` / `status` / `history` — same mutation surface as `/dca`.
  - `AlertsSchedulerService` (id `clawmes.alerts_scheduler`) ticks
    on the registry cadence (~60s). Per-alert errors caught
    internally so one bad alert can't crash the loop.

### Internal

- `clawmes/services/token_gate.py` (+ `tests/services/test_token_gate.py`).
- `clawmes/commands/alerts.py` (+ `tests/commands/test_alerts.py`).
- `clawmes/services/alerts_scheduler.py` (+
  `tests/services/test_alerts_scheduler.py`).
- Gate calls added to `/dca add`, `/copy add`, `/agent` multi-step
  parse. Free tier behaviour is the default; HOLDER tier removes the
  cap.
- `tests/conftest.py` autouse fixture patches the gate helpers to
  always-pass for the existing test suite — new tests in
  `tests/commands/test_token_gating.py` and the corresponding new-
  command test files specifically exercise the rejection branches.
- 3660 tests pass at 100% coverage (was 3513). README counts: 86 →
  87 commands, 25 → 27 services.

## 0.8.0 — 2026-05-27

### Added — `/agent` natural-language plan compiler

`/agent <prompt>` parses common trading-intent phrasings into a
sequence of clawmes slash commands. Nothing materializes until the
user explicitly says `/agent confirm` — the parsed plan lives per-
sender in memory and is re-printable via `/agent show`.

Architecture is deliberately minimal: regex-based intent parsing
covers the high-frequency surface area, and any unparsed segment is
called out by name so users see exactly what we missed (rather than
silently truncating their prompt). An LLM-backed fallback can land as
``/agent --ai <prompt>`` after we have telemetry on what people
actually ask for.

- **Surface:**
  - `/agent <prompt>` — parse, store as draft, show plan.
  - `/agent show` — re-print current draft.
  - `/agent confirm` — execute every step in the draft, then clear.
  - `/agent cancel` — discard draft.
  - `/agent examples` — list supported phrasings.

- **Recognized intents:**
  - DCA: `DCA <amount> ETH of <token> every <interval>` and the
    equivalent `buy ... every ...` phrasing.
  - One-shot buy: `buy <amount> ETH of <token>`.
  - Copy-trading: `copy <wallet>` / `follow <wallet> [at <amount> eth]`.
  - LP fee claim: `claim my fees` / `claim all` / `claim <token>`.
  - Burn: `burn <amount> [CLAWNCH]` (supports `1,000,000` or `1_000_000`).
  - Leaderboard: `leaderboard`, `top tokens`, `top launchers`.
  - Discovery: `show my launches`, `launches`, `balance`,
    `what's my balance`.
  - Multi-step: join with `then` (e.g.
    `DCA 0.001 ETH of CLAWNCH every 1h then claim my fees`).

- **Safety:**
  - Bare commas are NOT segment separators (they appear inside
    numbers like `1,000,000`). Use `then` for multi-step prompts.
  - Drafts are in-memory only — a parse that goes stale across a
    restart re-prompts rather than executing against state the user
    can't see.
  - `_dispatch_step` calls the matching `handle_*` function for each
    step, so each command's own safeguards (e.g. `/dca` v2 caps,
    `/buy` quote-then-confirm) apply downstream.

### Internal

- `clawmes/commands/agent_plan.py` (~330 lines). New file rather
  than extending the existing `agent.py` (which is `/register_agent`
  — different command, conflicting name otherwise).
- 3513 tests pass at 100% coverage (was 3455). README command count:
  85 → 86.

## 0.7.0 — 2026-05-27

### Added — `/copy` copy-trading

Watch a target wallet's ERC-20 receipts and mirror their buys at a
configurable fixed ETH amount. Same safeguard surface as `/dca` v2
(slippage / daily cap / total cap / max consecutive failures), plus a
per-follow blocklist so airdrops and obvious spam don't trigger
copies.

- **Surface:**
  - `/copy add <wallet> <eth_per_copy>` — follow a wallet's buys.
    Flags: `--slippage <bps>`, `--daily-cap <eth>`, `--max-total <eth>`,
    `--max-failures <n>`, `--blocklist <0xa,0xb,…>`.
  - `/copy list` — show your follows + last seen block + copy count.
  - `/copy pause <id>` / `resume <id>` / `cancel <id>` / `edit <id>
    <field> <value>` — same mutation surface as `/dca`.
  - `/copy tick` — manual poll-and-execute (cron-driven in production).
  - `/copy status` — global summary + service health.
  - `/copy history <id>` — last 25 copies for one follow.

- **Watcher service** (`CopyTraderService`, id
  `clawmes.copy_trader`) — ticking=True. Each tick polls Basescan's
  `account.tokentx` endpoint for every active follow since the last
  seen block, filters incoming transfers, drops blocklisted contracts,
  and submits a copy buy at the configured amount. Per-tick cap of 20
  to prevent runaway airdrop spam from spawning hundreds of buys.

- **Safeguards:**
  - Order: total cap → daily cap → wallet → swap, identical to
    `/dca` v2 so the same failure-state vocabulary applies.
  - Auto-pause after N consecutive failures (default 3).
  - All Basescan + RPC + swap errors are caught per-follow inside the
    runner; the service tick adds one more catch so one bad follow
    cannot crash the cron loop.

### Internal

- `clawmes/commands/copy.py` (~620 lines), `clawmes/services/
  copy_trader.py` (~80 lines).
- 3455 tests pass at 100% coverage (was 3318).
- README service count: 24 → 25. Commands: 84 → 85. Categories
  unchanged (16).

## 0.6.1 — 2026-05-27

### Added — `/dca` v2: auto-scheduler, safeguards, new subcommands

`/dca` graduates from a manual cron-curio to a real "set it and
forget it" feature.

- **Auto-scheduler** — new `DcaSchedulerService` (id
  `clawmes.dca_scheduler`) ticks on the registry cadence (every ~60s
  by default, driven by Hermes cron) and dispatches every due
  schedule. The manual `/dca tick` subcommand is preserved for
  testing and edge-case use. Per-schedule failures are caught
  internally so one bad token cannot stall the loop.

- **Safeguards** — every schedule gets four new optional caps:
  - `--slippage <bps>` (default 100 = 1%) — passed through to
    `defi_swap` on each execution.
  - `--daily-cap <eth>` — skips the run if executing it would push
    24h spend over the cap. Old runs (>24h ago) and non-`ok` runs are
    excluded from the window.
  - `--max-total <eth>` — auto-pauses the schedule once lifetime
    spend hits the cap.
  - `--max-failures <n>` (default 3) — auto-pauses after N
    consecutive failures (`error`, `no_wallet`, `daily_capped`, or
    `total_capped`) so a misconfigured schedule can't drain gas.

- **New subcommands:**
  - `/dca edit <id> <field> <value>` — change any field on an
    existing schedule. Editable fields: `token`, `eth_amount`,
    `interval`, `slippage_bps`, `daily_cap_eth`, `max_eth_total`,
    `max_consecutive_failures`. Caps accept `none` to clear.
  - `/dca skip <id>` — bump `next_run_epoch` by the interval without
    executing. Useful when you know a run would fail (e.g., wallet
    deliberately offline) and want to keep the cadence.
  - `/dca dry-run <id>` — quote the swap via `defi_swap` (action
    `quote`) without submitting. Returns the expected buy amount.
  - `/dca status` — global summary across all senders, plus the
    scheduler service's tick + run counters.

### Internal

- `_execute` refactored to `_execute_sync` (no actual `await` needed
  — wallet + swap calls were already sync). The async `/dca tick`
  command now delegates to `_run_due_with_lines()` which both surfaces
  return.
- 3318 tests pass at 100% coverage (was 3231).
- `clawmes/services/dca_scheduler.py` added (`DcaSchedulerService`).
- README service count: 23 → 24.

## 0.6.0 — 2026-05-27

### Added — leaderboards, fee claiming, dollar-cost averaging

Three new commands that surface what's already on-chain and automate
the parts of trading that benefit from a loop.

- **`/leaderboard`** — three views in one command. `tokens` (default)
  ranks Clawnch index tokens by 24h volume with live price, market
  cap, and 24h change. `launchers` aggregates `/api/launches` client-
  side by `agentName` × `source` to surface who's actually deploying.
  `burners` is a stub today — points at the public burn address +
  Basescan filter URL until the on-chain aggregator endpoint ships.
  Read-only, no wallet needed.

- **`/claim`** — sweep accumulated LP fees on tokens you launched.
  Hits Clanker v4's `collectRewards(address)` on the LP Locker Fee
  Conversion contract (`0x63D2DfEA64b3433F4071A98665bcD7Ca14d93496`).
  Three modes: `/claim` (preview your launches), `/claim <address-or-
  symbol>` (claim one), `/claim all` (sweep every launch, one tx per
  token). The locker pays out to all reward recipients per token in
  one shot — calling claim costs you gas but also pulls fees forward
  for every co-recipient on the position. No view function exists for
  pre-simulating amounts; verify on-chain via the `ClaimedRewards`
  event on each receipt.

- **`/dca`** — dollar-cost averaging on any Base token. Schedule
  recurring ETH-funded buys at intervals from 1 minute to 1 year.
  Subcommands: `add`, `list`, `pause`, `resume`, `cancel`, `tick`,
  `history`. State persists in `${HERMES_HOME}/clawmes/dca/
  schedules.json` so schedules survive restarts. `/dca tick` is cron-
  safe and idempotent: only runs schedules whose `next_run_epoch` has
  passed, advances each run regardless of outcome (transient failures
  don't cascade), records the result on the schedule. Swap execution
  reuses the `/buy` `defi_swap` pipeline.

### Internal

- 3231 tests pass at 100% coverage (was 3074).
- `clawmes/lib/abi.py` already exposed every selector helper needed
  by `/claim`; no encoder additions required. Selector for `collect
  Rewards(address)` pinned as `0x5763dbd0` in `commands/claim.py`.
- `_record` helper pattern (best-effort `command_history` write that
  swallows exceptions) extracted consistently across all three new
  commands so the recording layer can never break a user-visible
  surface.

## 0.5.0 — 2026-05-26

### Added — deep Base ecosystem integration

A second batch of Base-specific surfaces. Most are small additive
features; the big ones are Base Account wallet mode and shipping
clawmes itself as an MCP server for desktop AI clients.

- **Base Account wallet mode (`/connect_base`)** — fourth wallet mode
  alongside WalletConnect / local-key / Bankr. Uses Coinbase's OAuth
  2.1 + stored-request flow (same as Base MCP) so every Base App user
  can connect their existing Coinbase Smart Wallet to clawmes without
  needing to import a mnemonic or set up WalletConnect. Tx + signature
  requests trigger an approval prompt in the user's Base App. Configure
  via `CLAWMES_BASE_ACCOUNT_CLIENT_ID` after registering an OAuth client
  on Coinbase Developer Platform.

- **`clawmes-mcp` — clawmes as an MCP server** — desktop AI clients
  (Claude Desktop, Cursor, ChatGPT, any MCP-compatible client) can
  install clawmes directly and use its tools without going through
  Hermes. Read-only subset for v1: `defi_price`, `defi_balance`,
  `market_intel`, `clawnch_launch` (info), `clawnch_fees`,
  `bv7x_oracle`, `bv7x_market`, `nft`, `block_explorer`, `cost_basis`.
  Install with `pip install clawmes[mcp]`, run with `clawmes-mcp`,
  configure in Claude Desktop's `claude_desktop_config.json` under
  `mcpServers`.

- **Coinbase builder code on every Base transaction** — clawmes appends
  Coinbase's `BASE_BUILDER_CODE` suffix to swap, deploy, and burn
  calldata on Base mainnet so the plugin earns builder rewards on
  every on-chain action it drives. The clawnch backend already does
  this server-side; this commit closes the gap for client-side txs.

- **Basenames (.base.eth) resolution** — `/buy`, `/transfer`, `/send`
  and any other command that takes an address now resolves
  `jesse.base.eth`-style names natively against the Base L2 ENS
  registry. Non-`.base.eth` ENS names continue to resolve against
  Ethereum mainnet's canonical registry.

- **`/launch check`** — pre-flight validation. Calls `/api/prepare/deploy`
  to validate every param (name, symbol, burn tx) without committing
  and shows the user what they'd get on confirm — most useful when
  a `burn_tx_hash` is in the draft so the vault % gets verified
  before paying gas.

- **Base App deep links** — `/launch confirm` success messages now
  include a `Base App: https://base.app/?token=…` link that opens
  the new token in the user's Base App alongside the existing
  Basescan + DexScreener URLs. URL pattern overridable via env.

- **`/onramp [usd_amount]`** — generate a Coinbase Onramp link
  pre-filled with the connected wallet's address. Removes the
  "need ETH first" friction for new users. Configure via
  `CLAWMES_COINBASE_ONRAMP_APP_ID` (Coinbase Developer Platform
  app id); falls back to the generic landing page without it.

- **x402 payment-required helpers (`clawmes.lib.x402`)** — minimal
  client-side detection of HTTP 402 challenges per the x402 spec
  (https://www.x402.org). Provides `is_x402_response`,
  `parse_challenge`, and `format_challenge`. Foundation for future
  paid-endpoint integrations; not wired into tools yet.

### Changed

- **`/launch confirm` non-custodial deploy** now also appends the
  Coinbase builder code suffix to the prepared Clanker calldata
  before submitting via the wallet. Custodial path was already
  handled server-side.
- **`/burn` + `/launch burn`** ERC-20 transfers append the builder
  code suffix on Base. CLAWNCH burn-to-vault txs earn builder
  rewards too.
- **`active_mode`** on `WalletService` is consistently used as a
  property (no parens) across all callers. Three call sites
  (`/launch`'s non-custodial confirm, `/launch burn`, `/burn`)
  were incorrectly calling it as a method; now fixed.

### Tests

- 137 new tests covering the new lib helpers, services, wallet
  mode, commands, and MCP server. Full suite 3073 passing at
  100% coverage. Ruff clean.

### Notes

- The `mcp` Python package is an optional dependency (`pip install
  clawmes[mcp]`); the core install is unchanged. Without the extra
  installed, the `clawmes-mcp` script entry will fail at runtime with
  an import error pointing at the install command.
- Base Account, Coinbase Onramp, and Base App deep-link production
  endpoints can be overridden via env vars (`CLAWMES_BASE_ACCOUNT_*`,
  `CLAWMES_COINBASE_ONRAMP_*`, `CLAWMES_BASE_APP_*_URL`) — the
  defaults are best-effort against publicly-documented URLs that may
  shift as the Base App / Coinbase Developer Platform mature.

## 0.4.0 — 2026-05-26

### Added — Base ecosystem integration

clawmes now wires deeply into the Base MCP plugin work that landed
in the clawnch backend. The headline change is that `/launch` no
longer requires a Clawnch API key — when a wallet is connected, deploys
go through the new non-custodial path (`GET /api/prepare/deploy`) and
the user signs + pays gas directly.

- **Non-custodial `/launch confirm` (new default)** — when a wallet is
  connected, `/launch confirm` hits `/api/prepare/deploy` for unsigned
  Clanker factory calldata and submits it via the active wallet mode.
  No `CLAWNCH_API_KEY`, no `/register_agent` step, no 24h cooldown, no
  captcha. The 80% / 20% fee split is preserved in the rewards array
  of the prepared calldata.
- **`/launch confirm --custodial`** — explicit opt-in to the old path
  (server-paid gas, captcha challenge, API key required). Same flow as
  v0.3.0's default `/launch confirm`.
- **`/launch confirm --noncustodial`** — explicit opt-in to the new
  path even without a connected wallet (will error out with a clear
  message). Useful for scripted flows.
- **`/launch export`** — emits unsigned Clanker calldata as a
  JSON-shaped `{chain, calls}` block ready to paste into Base MCP's
  `send_calls`, Claude Desktop, Cursor, or any other agent surface
  with its own signing UX. No wallet operation on the clawmes side.
- **`/launch alerts [source]`** — points users at the public
  `@ClawnchAlerts` Telegram channel and documents how to filter the
  feed client-side. Sources: `clawmes`, `moltbook`, `4claw`,
  `clawtomaton`, `moltx`, `base-mcp`, `clawncher`.

### Added — `/burn` command

- **`/burn <amount>`** — standalone CLAWNCH burn, decoupled from
  `/launch`. Signs an ERC-20 transfer to the burn address from the
  active wallet. Range: 1,000,000 (1% vault) to 10,000,000 (10% max
  vault, Clanker limit). Returns the burn tx hash you can plug into
  `/launch burn <tx_hash>`, `/api/prepare/deploy?burnTxHash=…`, or
  any other surface that takes a burn-tx receipt.
- **`/burn last`** — shows the most recent burn tx hash submitted via
  this command, useful for piping between flows without scrolling.

### Added — `/buy` Clawnch attribution

- `/buy <token> <eth>` quotes now include a Clawnch-attribution line
  when the buy token is in the launchpad index. Format:
  `Clawnch: source <X> · agent <Y> · launched <ISO>`. Best-effort —
  lookup failures silently skip the line, no impact on the swap.

### Added — `ClawnchService.prepare_deploy()`

- New service method wrapping `GET /api/prepare/deploy`. Public
  endpoint, no auth required. Returns the envelope shape
  `{ok, data: {to, data, value, chainId}, meta: {…}}` and maps upstream
  error codes (`rate_limited`, `invalid_burn`, `invalid_from`, etc.)
  to clawmes' standard `ClawnchError` classifications. Used by both
  `/launch confirm` (non-custodial path) and `/launch export`.
- `ClawnchService._get()` gains an optional `params` kwarg so the
  query string for `/api/prepare/deploy` doesn't have to be
  constructed by hand.

### Tests

- +77 new tests across `tests/services/test_clawnch.py` (prepare_deploy
  full error-code coverage), `tests/commands/test_launch.py`
  (non-custodial path: 19 cases covering wallet states, receipt parsing,
  prepare errors, mode resolution, export, alerts), `tests/commands/test_buy.py`
  (8 attribution cases), `tests/commands/test_burn.py` (20 cases for
  the new command). Full suite 2936 passing at 100% coverage.

### Notes

- Existing custodial flow remains fully supported behind
  `/launch confirm --custodial`. The `CLAWNCH_API_KEY` environment
  variable is still honored when set. Users on v0.3.0 who relied on
  the custodial default get a one-line behavior change: passing
  `--custodial` to confirm now preserves the old path, otherwise the
  new non-custodial path runs (which is almost always what they want).

## 0.3.0 — 2026-05-26

### Added — trading, discovery, burn-to-vault

Three new top-level commands wire the basic buy / discover / list loop into chat, plus a new `/launch burn` subcommand for $CLAWNCH-backed vault allocations.

- **`/buy <token> <eth_amount> [--clawnch | --all]`** — two-step swap
  flow (quote → confirm) over the existing `defi_swap` tool. 0x
  Permit2 single-signature execution. Symbol resolution via
  DexScreener defaults to `--all` (broadest Base universe);
  `--clawnch` restricts resolution to launchpad-deployed tokens via
  `/api/launches?address=`. `0x`-prefixed addresses bypass symbol
  resolution. `/buy confirm` / `/buy cancel` / `/buy status` round
  out the per-sender draft surface.

- **`/trending [--clawnch | --all] [limit]`** — top tokens on Base by
  24h volume. Default `--all` pulls from DexScreener (broadest);
  `--clawnch` queries `/api/tokens?sort=volume&prices=1` for
  launchpad-only ranking. Limit clamped to `[1, 25]`.

- **`/my_launches [--clawnch | --all]`** — list this user's launches.
  Default `--clawnch` returns the agent's Clawnch-API launch history
  via `GET /api/agents/me`. `--all` scans the connected wallet's
  contract-creation transactions on Base via the Basescan API and
  enriches each with DexScreener market data; tokens with no DEX
  listing render as `(no DEX listing)`. Caps at 25 results.

- **`/launch burn <amount | tx_hash>`** — claim a Clanker vault
  allocation by burning $CLAWNCH. Integer amounts (e.g. `1000000`,
  with optional `_` / `,` separators) sign + submit an ERC-20
  `transfer(burn_address, amount * 1e18)` from the active wallet,
  wait for the receipt, and store the hash in the launch draft.
  Existing tx hashes (`0x` + 64 hex) are recorded verbatim. The
  backend verifies the burn (sender, recipient, amount, 24h
  pre-launch window) and applies the corresponding vault percentage
  — 1k tokens allocated per 1 CLAWNCH burned, capped at 10% (10M
  CLAWNCH). `/launch confirm` forwards the hash as `burnTxHash` to
  `/api/deploy`.

### Changed

- `ClawnchService.deploy()` and `start_deploy()` now accept
  `burn_tx_hash` alongside the existing `bypass_tx_hash`. They're
  independent — both can be supplied on the same launch.
- `get_bypass_recipient()` fallback bumped from `0.001 ETH` to
  `0.005 ETH` to match the server-side `BYPASS_FEE_WEI` default that
  shipped in the recent `clawnch` operational changes.
- New `get_burn_config()` helper on `ClawnchService` exposes the
  CLAWNCH token address, burn address, and minimum burn amount.
  Override via `CLAWNCH_TOKEN_ADDRESS` / `CLAWNCH_BURN_ADDRESS` /
  `CLAWNCH_MIN_BURN_TOKENS` env vars (staging / test).

### Added — internals

- `clawmes.lib.dexscreener` — stateless helper over the public
  DexScreener HTTP API (`/latest/dex/search` and
  `/latest/dex/tokens/<addr>`). Surfaces `search`, `find_token`,
  `top_pairs`, and a compact `format_pair_summary` used by both
  `/buy` and `/trending`. No auth, no service lifecycle — lives in
  `lib/` not `services/`.
- README expanded with a "Trading + discovery from chat" section and
  updated launch flow including the burn curve.

### Tests

- +176 new tests covering dexscreener, /buy, /trending, /my_launches,
  /launch burn, and the burn / bypass plumbing in `ClawnchService`.
  Full suite 2859 passing at 100% coverage.

## 0.2.1 — 2026-05-22

### Added — launch metadata (image + socials)

- **`/launch image <url>`** — set the token image URL.
- **`/launch twitter <handle|url>`** (alias `/launch x`) — set X / Twitter
  handle or full URL. Bare handles (`clawnchbot`, `@clawnchbot`) are
  normalized to `https://x.com/<handle>`; full URLs pass through.
- **`/launch website <url>`** — set the project website.
- **`/launch telegram <handle|url>`** — set Telegram handle or invite URL.
  Bare handles normalize to `https://t.me/<handle>`.
- **`/launch farcaster <handle|url>`** — set Farcaster handle. Bare
  handles normalize to `https://warpcast.com/<handle>`.
- **`/launch discord <url>`** — set Discord invite URL (no
  normalization; full URLs only).
- **`clawnch_launch` tool** — accepts the same set of metadata args
  (`image`, `twitter`, `website`, `telegram`, `farcaster`, `discord`)
  with the same normalization logic. LLM can pass them directly per
  the updated OpenAI schema.
- Collected metadata is serialized to `tokenParams.image` +
  `tokenParams.metadata.socialMediaUrls` per the Clawnch API contract,
  matching the format the launchpad's deploy endpoint already accepts
  (server-side handling unchanged).
- `/launch status` and `/launch` (usage) now render the `socials`
  sub-map with one line per platform for readability.

## 0.2.0 — 2026-05-21

Major release covering ten merged PRs plus the Clawnch launchpad
integration. Summary across all v0.2.0 changes:

- **+7 tools** (52 total): policy_manage, agent_identity, bv7x,
  bv7x_oracle, bv7x_market, a2a_call, eas_attestation.
  clawnch_launch + clawnch_fees rewritten against the live launchpad
  HTTP API (previously dead-ended on an imaginary on-chain contract).
- **+47 commands** (75 total): /policy + /policy_manage; /create_wallet,
  /recover, /export_wallet, /wallet_backup, /connect_local; /welcome
  + 5 personas + 10 capability toggles + /skip/back/reonboard;
  /evolve, /stable, /evolution; /allowlist, /allow, /disallow;
  /balance, /portfolio; /history, /clear_history, /version, /about,
  /uptime; /skills, /persona, /chains, /tools_list, /safety_status;
  /identity; /bv7x, /btc; /launch, /register_agent.
- **+7 services** (22 total): OnboardingService, EvolutionModeService,
  EndpointAllowlistService, CommandHistoryService, IdentityService,
  BV7XService, ClawnchService.
- **+1 hook**, **+1 skill** (clawmes:bv7x), and now
  clawmes:clawnch-launch (+1 more, 29 total).
- pre_llm_call hook now injects recent slash-command results as
  agent context (command-history integration).
- 1077 new tests; full suite 2677 passing at 100% coverage.

See individual PR entries below for detail.

### Added — Clawnch launchpad integration (launch from chat)

- **`ClawnchService`** (`clawmes/services/clawnch.py`) — HTTP client
  for the Clawnch launchpad API (`https://clawn.ch/api`). Covers agent
  registration (register / verify), the two-phase deploy flow
  (challenge → solve captcha → confirm), and read endpoints
  (`/api/agents/me`, `/api/launches`). Includes structured
  `ClawnchError` reclassification (`bad_request`, `no_credentials`,
  `rate_limited`, `not_found`, `challenge_expired`, `api_error`).
- **`/launch` slash command** (`clawmes/commands/launch.py`) — guided
  multi-turn flow for deploying a token: `/launch name <…>` →
  `/launch symbol <…>` → optional `/launch description <…>` /
  `/launch bypass <tx_hash>` → `/launch confirm`. Per-sender draft
  state (concurrent flows in a shared channel).
- **`/register_agent` slash command** (`clawmes/commands/agent.py`) —
  two-step Clawnch agent registration. Calls
  `/api/agents/register`, signs the returned challenge with the
  active wallet, calls `/api/agents/verify`, prints the issued API
  key for the user to save as `CLAWNCH_API_KEY`.
- **`clawnch_launch` tool rewritten** — was previously dead-ended
  on a missing imaginary-launchpad ABI. Now routes through
  `ClawnchService` against the live HTTP API. Two actions:
  `deploy` (params: name, symbol, description?, image?,
  bypass_tx_hash?) and `info` (params: token). Clawnch's deployer
  wallet pays gas server-side; the user's wallet only signs the
  captcha.
- **`clawnch_fees` tool rewritten** — now reads launch metadata +
  fee accrual via Clawnch's read endpoints. Two actions: `my_launches`
  (authenticated, lists the agent's launches) and `launch_info`
  (public, single-token detail). Claim-side ops deferred until the
  ClawnchFactory v2 fork ships.
- **`clawmes:clawnch-launch` skill bundle**
  (`clawmes/skills/clawnch-launch/SKILL.md`) — LLM-facing
  documentation of the deploy flow so natural-language "I want to
  launch a token" prompts route the agent to the right tools.
- **`CLAWNCH_API_KEY` env var** — added to `plugin.yaml` (both
  copies, byte-identical) as a secret, optional config. Required
  for `/launch` and `clawnch_launch` deploys; read paths work
  without it.
- **`clawn.ch` added to `clawmes/lib/http.py` allowlist** so the
  service can reach the launchpad's API.
- **Source attribution** — every clawmes-originated deploy stamps
  `source: "clawmes"` in the tokenParams body so the Clawnch
  launchpad can render a "via clawmes" badge on the public launch
  detail page.

### Added

- `policy_manage` tool (`clawmes/tools/policy_manage.py`) — LLM-callable
  surface for the already-shipped `clawmes.policy` engine. Eleven
  actions: `propose`, `confirm`, `revise`, `list`, `get`, `disable`,
  `enable`, `delete`, `evaluate` (dry-run), `usage`, `categories`.
  Propose -> confirm flow uses `confirm_store` to require explicit
  user consent before any new policy lands on disk. Disabled policies
  persist to a side-car at `${HERMES_HOME}/clawmes/policy/disabled_policies.json`
  so they can be re-enabled later without rebuilding from scratch.
  Decorated `@read_tool` (not `@write_tool`) to avoid policy-managing-itself
  recursion; mutations still persist directly to disk. Closes one of
  the OpenClawnch parity gaps without porting OC's richer policy
  schema — args that don't map to clawmes' Policy IR
  (allowlists, time-of-day windows, approval thresholds) are surfaced
  via `not_implemented` rather than silently accepted.
- 5 wallet recovery / backup slash commands plus the
  prerequisite `WalletService.connect_local_key()` service method
  (`clawmes/services/wallet.py`). Surface:
    - `/connect_local <password>` — load the existing encrypted
      keystore. Was referenced in help text since 0.1.0 but never
      registered as a command; this PR closes the gap.
    - `/create_wallet <password>` — generate a fresh BIP-39 24-word
      mnemonic + encrypted keystore. Refuses to overwrite an existing
      keystore (asks the user to back up first).
    - `/recover <password> | <mnemonic>` — two-phase mnemonic import.
      Phase 1 (no args) shows usage; phase 2 validates word count
      (12 or 24, BIP-39 standard) and persists.
    - `/export_wallet <password>` — decrypt + display the active
      keystore's mnemonic. Two-phase like `/recover`. Surfaces a
      "DO NOT SHARE" warning inline.
    - `/wallet_backup [output_path]` — copy `keystore.bin` to a
      timestamped backup file. Accepts an explicit file path or
      directory; default lands the backup next to the source.

  Sensitive operations require the password as an inline arg
  (mirrors OpenClawnch's wallet-manage-commands.ts). Slash commands
  don't go through the `@write_tool` policy gate (no clawmes
  infrastructure for that today); the password barrier is the
  safety boundary. Commit message + module docstring spell this out.
- 5 discoverability slash commands (`clawmes/commands/discovery.py`).
  Each wraps existing data with zero new dependencies and no new
  service.
    - `/skills` — list bundled clawmes skills by walking
      `clawmes/skills/*/SKILL.md` and reading the YAML-frontmatter
      `description:` line.
    - `/persona` — show the active persona (or list the 5 built-in
      personas when none is active).
    - `/chains` — list every EVM chain in `clawmes/lib/chains.CHAINS`
      with RPC-configured indicator and a default-chain marker.
    - `/tools_list` — list every clawmes tool declared in
      `plugin.yaml`'s `provides_tools` array.
    - `/safety_status` — show current `mode_service` mode (normal /
      readonly / danger) with context on what each mode means for
      write tools.
- 19 onboarding slash commands (`clawmes/commands/onboarding.py`) plus
  the backing `OnboardingService`
  (`clawmes/services/onboarding_service.py`). Surface:
    - `/welcome` — show current step, persona, and capability picks.
    - 5 persona switches — `/professional`, `/degen`, `/chill`,
      `/technical`, `/mentor`. Each delegates to `persona_service` and
      advances the onboarding step from `welcome` / `pick_persona` to
      `pick_wallet`.
    - 10 capability toggles — `/cap_wallet`, `/cap_prices`,
      `/cap_portfolio`, `/cap_trading`, `/cap_liquidity`,
      `/cap_launchpad`, `/cap_bridge`, `/cap_routing`, `/cap_clawnx`,
      `/cap_hummingbot`. No arg = flip current state; `on`/`off`/`true`/
      `false`/`enable`/`disable`/`yes`/`no`/`y`/`n`/`1`/`0` set
      explicit state.
    - 3 flow controls — `/skip` (advance), `/back` (pop step history),
      `/reonboard` (reset state + clear persona).
  The `OnboardingService` is in-memory only (matches
  `persona_service`), keyed by `sender_id` with `"default"` for the
  single-user CLI case. Step history is a per-sender stack;
  capabilities are a per-sender set. Capability picks are *recorded*
  today, not enforced — a future PR will wire enforcement (suppress
  tool registrations on a per-sender basis) into the
  `clawmes/tools/__init__.py` register loop.
- **Endpoint-allowlist service** (`clawmes/services/endpoint_allowlist.py`)
  layers two capabilities on top of the existing static allowlist in
  `clawmes/lib/http.py`:
    - Runtime user-added hosts (session-scoped). Added via `/allow
      <host>`, removed via `/disallow <host>`. Resets on restart by
      design — an attacker who tricks the agent into adding a host
      can't keep it added across processes.
    - Audit ring buffer. Every blocked HTTP attempt is recorded with
      timestamp, host, and URL (default ring size 100). Reviewable
      via `/allowlist`.
- 3 new commands: `/allowlist` (show defaults + user-added + recent
  blocks), `/allow <host>` (add session host), `/disallow <host>`
  (remove user host; defaults are immutable through this surface).
- `clawmes/lib/http._check_allowlist` now consults the service after
  the default + per-call `extra_hosts` checks, and records blocks for
  audit. Defensive import — if the services subsystem hasn't started
  yet, the existing default-allowlist behavior continues to work.
- **Evolution-mode gate** for self-modifying tools
  (`clawmes/services/evolution_mode.py`). Default OFF. When OFF,
  the write actions of `agent_memory` (`add` / `replace` / `remove`)
  and `skill_evolve` (`propose` / `update` / `revert`) return
  `evolution_gate` errors. Read actions (`query`, `list`) are always
  allowed. Closes a safety hole: previously the agent could rewrite
  its own memory and skills with no gate, which made
  prompt-injection drift much easier. Equivalent to OpenClawnch's
  `wrapWithEvoGate` (`extensions/crypto/index.ts:402-419`).
- 3 commands wrapping the new service: `/evolve` (enable),
  `/stable` (disable, the safe default), `/evolution` (status).
- 2 wrapper commands over `defi_balance`: `/balance [chain]` (native
  balance) and `/portfolio [chain]` (native + curated common-token
  list). Both pick up the wallet address + chain from
  `wallet_service`; both are pure surface deltas over an existing
  read-only tool.
- **Command-history service** (`clawmes/services/command_history.py`) —
  ring buffer of recent slash-command calls + result summaries.
  Recorded explicitly by handlers that opt in via
  `record_command_call(name, args, result)`; sensitive commands
  (`/export_wallet`, `/recover`) deliberately don't record so
  mnemonics don't surface in a recap. Default ring 20 entries; result
  summaries truncated to 240 chars to keep prompt-cache impact
  bounded.
- **`pre_llm_call` hook integration** — `prompt_builder._append_command_history`
  reads the recent ring and injects the last 5 entries as
  `[clawmes/recent-commands]` into the per-turn user message context.
  Net effect: the agent stops re-asking things the user just answered
  via slash (e.g. user runs `/balance`, then "what's my balance?" —
  the agent now sees the previous result and replies without
  re-fetching).
- 5 new info / status commands (`clawmes/commands/info.py`):
  `/history [N]` (show recent recap, default 10, max 20),
  `/clear_history` (wipe the ring),
  `/version`,
  `/about`,
  `/uptime`.
- **`IdentityService`** (`clawmes/services/identity.py`) — ed25519
  keypair + `did:key` encoding. Gives the agent a verifiable
  cryptographic identity independent of the connected wallet. The
  wallet signs on-chain transactions (high-value); the DID signs
  protocol messages — MCP calls, capability delegations, code-review
  signatures, anything where the wallet key is too sensitive for
  the hot path.
- **`agent_identity` tool** (`clawmes/tools/agent_identity.py`) —
  LLM-callable surface with 5 actions: `show`, `create`, `sign`,
  `verify` (static, no identity required), `did_encode` (raw pubkey
  hex → `did:key:z...`).
- **`/identity` slash command** (`clawmes/commands/identity.py`) —
  no-arg shows the current identity, `create` generates a fresh
  keypair, `create force` replaces an existing one.

  v1 is in-memory only. Restart loses the keypair by design — the
  persistence path (encrypted file mirroring the wallet keystore, or
  deterministic derivation from the wallet mnemonic) lands in a
  follow-up PR. Same posture as `persona_service` and `mode_service`.

  Built on `pycryptodome`'s `Crypto.PublicKey.ECC` (Ed25519 curve) +
  `Crypto.Signature.eddsa`. `did:key:z…` encoding via inline
  base58btc (no new external dependency). DER-prefixed import path
  for the public key (pycryptodome doesn't accept raw 32-byte
  ed25519 keys directly).

### Added — agent-economy integration (BV-7X / A2A / EAS)

- **`bv7x` tool** (`clawmes/tools/bv7x.py`) + **`BV7XService`**
  (`clawmes/services/bv7x.py`) — read BV-7X autonomous BTC oracle data
  via their public REST API. Three actions: `regime` (BTC market
  regime classification: CRISIS / BEAR / NEUTRAL / BULL / EUPHORIA),
  `identity` (BV-7X's ERC-8004 agent identity + reputation),
  `discover` (A2A discovery card). 60-second cache. Token-gated
  premium endpoints (`/oracle`, `/copy-trade`) are NOT exposed —
  clawmes does not require third-party token holdings.
- **`a2a_call` tool** (`clawmes/tools/a2a_call.py`) — generic
  agent-to-agent JSON-RPC 2.0 client. `discover` fetches a peer's
  AgentCard at `/.well-known/agent-card.json`; `send_task` posts
  JSON-RPC 2.0 tasks (default path `/api/bv7x/a2a/tasks/send`,
  configurable). Works with any A2A-speaking peer; tested against
  BV-7X. Auth via DID signatures (RFC 9421) is deferred to a
  follow-up that pairs with the IdentityService.
- **`eas_attestation` tool** (`clawmes/tools/eas_attestation.py`) —
  read EAS attestations on Base. `get` fetches by 32-byte UID,
  decoded into the canonical Attestation struct (uid, schema, time,
  recipient, attester, data, etc.). `decode_data` parses the raw
  bytes payload against a caller-supplied ABI schema. Generic
  on-chain primitive — useful for BV-7X signal attestations, trust
  scores, KYC certificates, and any EAS-using protocol. Default
  contract is the canonical EAS singleton on Base
  (`0x4200…0021`); overridable via `eas_address` for other L2s.
- `bv7x.ai` added to `clawmes/lib/http.py` allowlist.

### Added — BV-7X full ecosystem integration

BV-7X is a clawnch-ecosystem project (`$BV7X` launched on the
Clawnch launchpad). This PR layers a full integration on top of
the initial agent-economy scaffold above:

- **`BV7XService` extended** with every public + (auth'd) gated
  endpoint: market data (`btc_price`, `fear_greed`, `etf_flows`,
  `regime`, `signal_metadata`), track record (`scorecard`), on-chain
  attestation oracle (`onchain_latest/history/stats/verify`), agent
  + A2A + commerce (`identity`, `reputation`, `discover`,
  `get_a2a_task`, `commerce_offerings`, `copy_trade_status`), and
  token-gated premium (`oracle`, `oracle_premium`, `copy_trade_next`,
  `copy_trade_history`). Auth = `BV7X_API_KEY` env var, forwarded as
  `Authorization: Bearer …` after the wallet-verify flow at
  bv7x.ai.
- **`bv7x` tool** (extended) — agent / A2A / commerce reads.
- **`bv7x_oracle` tool** (new) — signal + on-chain attestation +
  premium endpoints in one tool. 10 actions.
- **`bv7x_market` tool** (new) — quick BTC reads (price, F&G, ETF).
- **`/bv7x` slash command** — track record + regime + agent id one-shot.
- **`/btc` slash command** — quick BTC price + F&G + ETF line.
- **`clawmes:bv7x` skill bundle** — documents the full BV-7X surface
  for the LLM (`clawmes/skills/bv7x/SKILL.md`).
- **New env var**: `BV7X_API_KEY` (declared in both `plugin.yaml`
  copies, marked `secret: true`, with URL pointing at the
  wallet-verify page).

### Deferred

- **ERC-8004 agent registry integration.** The spec is currently a
  draft EIP (Created 2025-08-13) with no canonical Base-singleton
  address yet. We'll wire this in once the spec finalizes and a
  registry singleton lands.

### Documentation

- README "Using OpenGateway as your LLM provider" section documents
  both integration modes: Mode 1 routes the whole Hermes stack
  through OpenGateway via `hermes model` (config-only, no code), and
  Mode 2 lets specific clawmes tools opt into targeted LLM calls via
  `OpenGatewayService`.

### Added

- `OpenGatewayService` (`clawmes/services/opengateway.py`) — OpenAI-
  compatible LLM client for the gitlawb OpenGateway endpoint
  (`https://opengateway.gitlawb.com/v1`). Ships under the gitlawb
  partnership. Non-streaming chat completions only; streaming remains
  Hermes' responsibility upstream. Calls without `OPENGATEWAY_API_KEY`
  are sent unauthenticated (matches gitlawb's partnership-window
  policy of "auth optional today, required soon") — service emits a
  startup warning so the future auth flip is not a total surprise.
  Setting the key is strongly recommended in production for
  attribution and rate-limit isolation. Live-probed against the real
  gateway; the structured-error body is pulled from the raised
  `httpx.HTTPStatusError.response` so users see real upstream messages
  (`"opengateway error (unsupported_model): Unsupported model …"`)
  instead of useless `"Client error '400 Bad Request' for url …"`.
  Sends `Accept-Encoding: identity` per-request to work around a
  verified upstream bug where the gateway advertises gzip on some 2xx
  responses but returns bodies that fail zlib decompression. New env
  vars: `OPENGATEWAY_API_KEY` (recommended; OpenAI-style
  `ogw_live_…` format) and `OPENGATEWAY_MODEL` (optional default
  model id). `opengateway.gitlawb.com` added to `clawmes/lib/http.py`
  allowlist. Registered as service 6d in `services.start_all` between
  LiFi and the background daemons. No internal consumer wired in this
  change — first consumers land in follow-up PRs as specific tools
  opt in.

## 0.1.0 — 2026-04-29

First versioned release. 45 of 48 PRD tools shipped at 100% test
coverage. Plugin loads in Hermes, CLI subcommands wired,
documentation in place.

### Added — full tool surface (45 of 48 PRD tools)

Complete implementation of every tool defined in PRD §8.1–8.11:

**Wallet (4)**: `clawnchconnect`, `transfer`, `permit2`, `approvals`.
Real-network signing via the WalletConnect bridge, BIP-39 local key
(scrypt + AES-256-GCM keystore), or Bankr custodial. Permit2 EIP-712
signed approvals. ERC-20 allowance enumeration + revocation via
explorer logs API.

**Trading (8)**: `defi_swap` (0x aggregator with permit2 quote/swap),
`defi_balance`, `defi_lend` (Aave V3 supply/withdraw/borrow/repay/
health_factor), `defi_stake` (Lido + Rocket Pool), `defi_price`,
`liquidity` (Uniswap V3 NFT positions), `manage_orders` (limit/stop/
trailing/DCA persisted to plan scheduler), `bridge` (LiFi multi-bridge
quote/execute/status/routes).

**Yield/Analytics (4)**: `yield` (DeFiLlama), `analytics` (RSI/MACD/
Bollinger inline math), `market_intel` (CoinGecko trending + top
movers), `cost_basis` (FIFO P&L from local ledger).

**Launches (6)**: `clawnch_launch`, `clawnch_fees`, `bankr_launch`,
`bankr_automate`, `bankr_polymarket`, `bankr_leverage`.

**Ownership (4)**: `nft` (Reservoir), `airdrop` (OZ Merkle distributor
calldata), `privacy` (Lobster pool), `safe` (Gnosis Safe Transaction
Service).

**Governance (2)**: `governance` (Snapshot GraphQL + Tally),
`farcaster` (Neynar API).

**On-chain Intel (4)**: `block_explorer`, `herd_intelligence`,
`watch_activity` (persistent watch list), `browser` (Playwright).

**Automation (1)**: `compound_action` (delegates to plan scheduler).

**Agent ops (4)**: `molten` (X), `clawnx` (agent-to-agent),
`hummingbot` (local gateway), `wayfinder` (route optimization).

**Memory (3)**: `agent_memory` (Hermes memory provider proxy),
`skill_evolve` (proposal/apply/revert workflow on disk),
`session_recall` (search past sessions).

**Misc (5)**: `giza` (zkML), `nookplot` (Farcaster analytics),
`paysponge` (fiat ramp), `lobster_cash` (privacy pool),
`_user_tools` (custom tool dispatcher).

### Added — services + safety infrastructure

- 14 background services with topologically-ordered start: credential
  redactor → mode → persona → RPC → token decimals → explorer →
  wallet → coingecko → price → bankr → 0x → LiFi → plan scheduler →
  WC notifications.
- ABI encoders: `encode_address` / `encode_uint` / `encode_transfer` /
  `encode_approve` / `encode_allowance` + selectors for ERC-20 surface,
  Aave V3 Pool, Permit2, Uniswap V3 PositionManager, OZ Merkle
  distributor.
- `eth_getTransactionCount` + `eth_sendRawTransaction` +
  `eth_getTransactionReceipt` + `wait_for_receipt` + `estimate_gas`
  in RpcService.
- `TokenDecimalsService.get_strict` — fail-loud path for send routes
  with two-tier cache (verified vs. fallback) preventing 10^12-wei
  silent multiplication on 6-decimal tokens like USDC.
- Pre-broadcast `eth_estimateGas` simulation that distinguishes
  reverts (refuse to broadcast) from network errors (fall back to
  static gas).
- Policy gate's `_extract_value_wei` reads `amount` for native AND
  ERC-20 transfers via `TokenDecimalsService.peek` (cache-only, no
  blocking RPC inside the gate).
- ENS resolution (`clawmes/lib/ens.py`) with namehash + on-chain
  registry walk on Ethereum mainnet, integrated into transfer.
- WalletService.disconnect() across all three modes; switch_chain
  with per-mode behavior (WC bridge, Bankr re-fetch, local-key
  metadata-only).
- Plugin discovery shim (root `__init__.py` + `plugin.yaml`) with
  `sys.modules['clawmes']` aliasing so the same package loads
  correctly via Hermes' git-install path AND pytest.

### Added — slash commands

- 27 commands across `wallet` (7), `tx` (4), `policy` (5), `plans`
  (10), `help` (1), and `doctor` (1).
- `/doctor` surfaces wallet status, RPC endpoints (default vs. user
  override), API keys (six grouped tables), WC bridge build status,
  and plugin manifest counts. Runs entirely offline.
- `PlanScheduler` exposes a real management API (`create_plan`,
  `validate_plan`, `dry_run`, `list_plans`, `cancel_plan`,
  `get_plan_logs`) for `compound_action` to dispatch into. Plans
  persist as JSON under `${HERMES_HOME}/clawmes/plans/`. Trigger
  evaluation lands in v0.2.0.

### Added — skill bundles

- 27 skills total. The original 19 (transfer, defi-trading, lending,
  staking, bridge, block-explorer, defi-swap, governance, nft,
  safe-multisig, approvals, analytics, airdrop, permit2, automation,
  liquidity, watch-activity, farcaster, bankr) plus 8 new ones for
  the most-used remaining tools: manage-orders (limit/stop/trailing/
  DCA), cost-basis (FIFO P&L + tax export), market-intel (trending /
  whales / flows), browser (governance research, headless reads),
  privacy (Lobster pool deposit-and-withdraw), agent-memory
  (cross-session preferences), session-recall (past-session search),
  and skill-evolve (self-improvement workflow).
- Each skill is a directory with frontmatter-tagged SKILL.md.
  Auto-registered via the walker in clawmes/skills/__init__.py.

### Added — CLI subcommands

- `hermes clawmes init` — interactive setup wizard. Three-step flow
  (wallet mode \u2192 per-mode setup \u2192 optional API keys), upserts to
  `~/.hermes/.env` preserving existing keys. `--check` for dry-run,
  `--non-interactive` for CI (CLAWMES_INIT_* env vars), `--reconfigure`
  to re-ask, `--skip-wallet` to bypass. Local-key mode includes real
  keystore creation with mnemonic generate-or-import.

### Fixed

- 0x v2 (Permit2) response parsing: error envelope is {name,
  message, data} not {name, reason}; gas/value are decimal strings
  not hex; liquidityAvailable=false signals no-route. Adds
  parse_0x_int helper that handles decimal, hex, int, or None
  transparently. Request side was already on v2; only response
  parsing needed fixing.

### Security

- `SECURITY.md` — full threat model, recovery checklist, audit
  status. `security@clawn.ch` reporting endpoint.
- Keystore cross-validated against Python stdlib `hashlib.scrypt`;
  tamper-detection tests for ciphertext, tag, nonce, salt; OWASP
  parameter assertion (N≥2^17, r≥8, p≥1, 32-byte derived key).
- `rotate_password()` keystore primitive.

### Tests

- 1970 tests passing, 100% coverage on every commit during the
  implementation grind.
- Plugin loading smoke test (`tests/test_plugin_loading.py`)
  exercises `register(ctx)` against a fake Hermes ctx with full
  dependency stubbing.
- Manifest sync test (`tests/test_plugin_manifest.py`) enforces
  byte-for-byte sync between repo-root and inner `plugin.yaml`.

### Notes

This is still pre-alpha. Surface complete + tested at 100%, but
no real-network validation has happened yet. Public-beta blockers
remaining:
- Real-mainnet smoke test (one tx through each wallet mode).
- Real-phone WalletConnect pairing test.
- Third-party security audit.
- Most "external API" tools (governance, farcaster, herd_intelligence,
  reservoir, etc.) require their own API keys for production traffic.

### Documentation

- `README.md` updated to reflect actual surface (45 tools / 27
  commands / 14 services / 11 hooks vs. PRD's 48 / 118 / 76).
- Configuration section enumerates 18 supported env vars.
- `BANKR_INTEGRATION.md` and `HERMES_PARITY.md` carried forward
  from earlier scaffold.
