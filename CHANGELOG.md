# Changelog

All notable changes to clawmes are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

## 0.18.1 — 2026-06-09

### Fixed — Venice: distinguish "out of credits" from "bad auth"

Venice returns HTTP 402 for two different conditions: a missing/invalid key
("authentication required") and a valid key on an account with no funds
("insufficient USD or Diem balance"). The service previously classified both as
`no_credentials`. The balance case now raises `VeniceError("payment_required",
…)` so the error is actionable (add credits at venice.ai/settings/api vs fix the
key). Surfaced while live-testing a real key.

## 0.18.0 — 2026-06-09

### Added — Venice AI inference provider

A new `venice` service (`clawmes/services/venice.py`) — an OpenAI-compatible
client for Venice AI (https://docs.venice.ai/models/overview), alongside the
existing OpenGateway provider. Lets tools run targeted inference (classifiers,
summarizers, structured-extraction helpers) outside the host Hermes agent loop.

- Base URL `https://api.venice.ai/api/v1`; configured via `VENICE_API_KEY`
  (required — Venice answers unauthenticated calls with HTTP 402 / x402) and an
  optional `VENICE_MODEL` default. Non-streaming chat completions only.
- Robust error classification including Venice's flat `{"error": "..."}` + HTTP
  402 auth challenge (→ `no_credentials`) in addition to OpenAI-style envelopes.
- `api.venice.ai` added to the network allowlist.
- Independent from Hermes' main conversational LLM (that's a Hermes-level
  concern); this is opt-in, per-call inference for clawmes tools.

Verified live against the Venice API (the 402 auth path) and with full unit
coverage of the success + every error path.

## 0.17.3 — 2026-06-02

### Fixed — clawmes couldn't reach the Clawnch backend in production

The Clawnch apex host `clawn.ch` 307-redirects to the `www.clawn.ch` canonical
host, but (a) `www.clawn.ch` was not on the network allowlist and (b) the HTTP
client deliberately does not follow cross-host redirects (an allowlisted host
redirecting to a non-allowlisted one would otherwise bypass the allowlist). The
combination meant every Clawnch API call — agent registration, token deploys,
`leaderboard` / `my_launches` reads — failed with either a 307 error or a
`NetworkAllowlistError`.

- `lib/http.py`: added `www.clawn.ch` to `_DEFAULT_ALLOWLIST` alongside the
  apex.
- `services/clawnch.py`: the default base URL is now `https://www.clawn.ch`
  (the canonical host, no redirect). `CLAWNCH_BASE_URL` still overrides for
  staging / local dev.

No config change is needed; existing installs pick this up on update.

## 0.17.2 — 2026-06-02

### Reverted — no bundled WalletConnect project ID (security)

0.17.1 bundled a default WalletConnect project ID so pairing worked with no
setup. That was a mistake: a project id baked into an open-source client ships
publicly (the repo + the PyPI sdist), where anyone can extract it and burn the
shared Reown relay quota — degrading WalletConnect for all users. Reown's own
guidance is "avoid committing project keys to the repo; use env variables."

- Removed the bundled default. `WALLETCONNECT_PROJECT_ID` is supplied per-user
  via the environment (typically `~/.hermes/.env`); the WC bridge inherits it.
  When unset, WalletConnect returns a clear "set WALLETCONNECT_PROJECT_ID"
  error and `hermes clawmes doctor` reports it as not-configured with a setup
  hint.
- The 0.17.1 default id has been **rotated out and disabled** in Reown, so it is
  dead regardless.

For a shared, zero-setup project id done safely, the only sound approach is a
server-side relay proxy (the id never reaches clients) — tracked as future
work; not bundling a public key.

## 0.17.1 — 2026-06-02

### Changed — WalletConnect works out of the box (bundled project ID)

A WalletConnect project ID is a *per-application*, public client identifier (a
dapp ships one for all its users; it's embedded in browser bundles and isn't a
secret), not a per-user secret. clawmes previously required every user to
create their own at cloud.walletconnect.com and set `WALLETCONNECT_PROJECT_ID`
before `/connect` / `clawnchconnect(mode=walletconnect)` would work.

- Bundled a default project ID so WalletConnect pairing works immediately with
  no setup. `WALLETCONNECT_PROJECT_ID` (e.g. in `~/.hermes/.env`) still
  **overrides** it for users who want their own relay quota / analytics, and an
  explicitly-empty value falls back to the default.
- `hermes clawmes doctor` now reports the WC project ID as OK by default and
  notes when the bundled default is in use.

Tradeoff: default-id traffic counts against one shared Reown relay quota; set
your own `WALLETCONNECT_PROJECT_ID` to use a separate quota.

## 0.17.0 — 2026-06-02

### Added — `clawmes_info`: agent-callable bridge for the read-only command surface

The Hermes Desktop app curates its slash-command autocomplete to a built-in
allowlist (`apps/desktop` → `desktop-slash-commands.ts`), so clawmes's plugin
slash commands don't appear in the `/` menu and their output renders as a
status line instead of a selectable chat bubble. Verified against the desktop
source.

To make clawmes usable from natural language in the desktop (and render as a
proper tool card), this adds a single read-only **tool** that bridges the key
informational commands:

- **`clawmes_info`** (tool 53, toolset `clawmes-trading`) — `op` + optional
  `args`. Ops: `wallet`, `balance`, `portfolio`, `research`, `scan`,
  `trending`, `leaderboard`, `my_launches`. The agent invokes it from phrases
  like "what's my wallet balance" or "research CLAWNCH"; the result renders as
  a normal tool card, and any HTML card the underlying command generates
  (e.g. `/research`) is surfaced as a preview attachment via
  `json_result(preview=...)`.
- Includes an async-safe runner so the sync tool can drive the `async` command
  handlers whether or not an event loop is already running.

Read-only by design — write actions keep their own gated tools (`defi_swap`,
`transfer`, …). Tool count: 52 → 53.

## 0.16.4 — 2026-06-02

### Fixed — preview cards now actually reach the desktop

A source-level audit of NousResearch/hermes-agent (the gateway event publisher
+ the desktop renderer) revealed the v0.16.0 card mechanism was emitting the
preview path one level too deep:

- The desktop chat tool-card (`tool-fallback-model.ts` `toolPreviewTarget`)
  reads the **top level** of the tool-result envelope — `result.preview` /
  `result.url` — **not** `result.details.preview`. clawmes was placing the
  card path inside `details`, so cards never rendered as in-app preview
  attachments (they only appeared as external-open File artifacts).

Fix:

- `json_result` gained a `preview=` keyword that emits an **envelope
  top-level** `preview` key (sibling of `content`/`details`). `defi_balance`
  (portfolio) and `clawnch_launch` (receipt) now pass their card path there,
  so the desktop opens them in the preview pane.
- Removed `attach_preview` / `will_auto_open` / `PREVIEW_TRIGGER_KEYS` from
  `ui_artifacts` — they encoded the disproven "details.preview auto-opens"
  assumption.
- `write_card` filenames now include a random suffix (not just a ms timestamp)
  so rapid same-millisecond renders can't collide.

Link enrichment was unaffected by the bug — the desktop Artifacts view
deep-recurses the result JSON, so `details.*_url` links surfaced correctly all
along; this release only fixes the card/preview path.

## 0.16.3 — 2026-06-02

### Added — scannable QR on the connect card

- `connect_card` now embeds a real scannable **QR** of the WalletConnect
  URI (black on a white tile so wallet scanners read it against the dark
  card), alongside the existing copyable URI fallback. Rendered as inline
  SVG — best-effort, falling back to URI-only if encoding fails.

### Dependency

- Added **`qrcode>=8.0`** (BSD, Lincoln Loop). Pinned **without** the
  `[pil]` extra and used via the `SvgPathImage` factory, so it pulls in
  **no Pillow / no C extensions** — pure-Python, zero transitive deps on
  macOS/Linux (`colorama` is Windows-only). Verified against OSV (no
  known advisories).

## 0.16.2 — 2026-06-02

### Added — Hermes Desktop UI: complete tx-tool link coverage

- Link enrichment threaded into `nft` (mint / transfer / burn) and
  `liquidity` (provide / withdraw / compound) via their shared `_send`
  helpers — each now surfaces a clickable explorer link for the tx.
  (ERC-721 market links are intentionally omitted from `nft`; DexScreener
  and Clanker don't apply to NFTs.) This completes explorer-link coverage
  across every clawmes transaction-producing tool.

### Notes — intentionally deferred

- **Read-tool token links** (`defi_price`, `market_intel`, `cost_basis`):
  these don't carry a token address + chain pair (CoinGecko ids;
  chain-agnostic cost tracking), so a correct market link can't be built
  without guessing — skipped rather than emit wrong links.
- **Scannable QR** for the connect card needs a QR dependency; kept the
  plugin dependency-free pending an explicit decision.
- **Per-toolset descriptions**: the Hermes registration API exposes
  per-*tool* descriptions only (no per-toolset metadata), so there's
  nothing to add for the desktop Settings panel.

## 0.16.1 — 2026-06-02

### Added — Hermes Desktop UI follow-ups

Continues the v0.16.0 desktop integration across more surfaces.

- **Link enrichment** extended to `defi_lend` (Aave supply/withdraw/
  borrow/repay), `defi_stake` (Lido/Rocket Pool), and `permit2` (revoke):
  each now surfaces a clickable explorer link for the tx — and, for
  `defi_lend`, DexScreener/Clanker/token-explorer links for the asset.
  Passive descriptive keys (no preview auto-open), so they're safe on
  any call path.

- **`/research <token>`** now writes a research card
  (`research_card`) — price, liquidity, volume, market cap, 24h change,
  risk flags, plus DexScreener/Clanker/explorer links — and surfaces its
  path as a clickable artifact beneath the text report. Best-effort: a
  card failure never affects the report.

## 0.16.0 — 2026-06-02

### Added — Hermes Desktop UI integration

Surface clawmes crypto capabilities natively in the Hermes Desktop app
(`apps/desktop`). The desktop has no plugin-UI API — it renders tool
output through generic systems (Artifacts view, structured tool-card
summary, and an auto-opening side preview pane). This release shapes
clawmes output to light those systems up.

- **`clawmes/lib/ui_artifacts.py`** — canonical link builders
  (`explorer_tx_url`, `explorer_address_url`, `explorer_token_url`,
  `dexscreener_url`, `clanker_url`) plus `enrich_tx_links` /
  `enrich_token_links` helpers. Links are emitted under descriptive
  keys (`explorer_url`, `dexscreener_url`, `clanker_url`) so they
  become clickable **Link artifacts** in the desktop without hijacking
  the preview pane. `attach_preview` / `will_auto_open` give precise
  control over the desktop's preview auto-open (`url/target/path/
  file/filepath/preview` trigger keys).

- **`clawmes/lib/ui_cards.py`** — self-contained, offline,
  HTML-escaped preview cards rendered to `${HERMES_HOME}/clawmes/cards/`
  and surfaced under the `preview` key so the desktop auto-opens them in
  the side rail: `portfolio_card`, `research_card`, `receipt_card`,
  `connect_card`, plus the `render_card` / `kv_section` / `links_section`
  primitives.

- **Link enrichment** threaded into `defi_swap`, `bridge`, and
  `clawnch_launch` — every swap/bridge/launch now surfaces a clickable
  explorer link (and DexScreener / Clanker / token-explorer links for
  the relevant token). Safe for scheduler-driven calls (no I/O, passive
  keys, never auto-opens).

- **Auto-opening cards** wired into `defi_balance` (summary →
  portfolio dashboard) and `clawnch_launch` (→ launch receipt), and a
  pairing card surfaced by `/connect`. Card rendering is best-effort —
  a UI failure never breaks the underlying read or transaction.

- Test isolation: an autouse fixture points `HERMES_HOME` at a per-test
  temp dir so card/state writes never touch the developer's real
  `~/.hermes`.

## 0.15.0 — 2026-05-29

### Added — trader-focused features

Four additions targeted at the things crypto traders actually need
that nobody else builds well.

- **`/mev-protect on|off|status`** (HOLDER) — global toggle for
  privacy-RPC swap routing. When enabled, swap transactions route
  through Flashbots Protect (Ethereum mainnet endpoint registered;
  Base equivalent slots open for when one exists). Sandwich attacks
  become impossible on chains with a registered privacy endpoint.
  Other transactions (transfers, deploys, burns) are unaffected
  because they don't need protection.

- **`/limit_order --bracket <tp_pct>:<sl_pct>`** (HOLDER) — bracket
  orders attached to buy orders. On fill, the scheduler auto-
  materializes a take-profit sell order at +`tp_pct%` and a stop-
  loss sell order at -`sl_pct%`, both anchored to the actual fill
  price. Children inherit slippage_bps from the parent. State
  scheduling pattern remains identical — children are just new
  orders in the same list with `parent_order_id` + `kind` metadata.

- **`/scan <wallet> [--json]`** (HOLDER) — comprehensive wallet
  analysis. Pulls native ETH balance, recent ERC-20 transfer
  activity (capped at 100), aggregated top holdings, and derived
  risk flags (`no_recent_activity`, `very_low_activity`,
  `single_token_concentration`, `single_token_dominance`,
  `empty_wallet`). One-screen output by default; `--json` returns
  the raw structured snapshot for piping into other tools.

- **`/airdrop scan|list|claim <name>|history`** (UNLIMITED) —
  autonomous airdrop scanner + claimer. Maintains a conservative
  registry of known airdrops. `scan` queries every registered
  checker for the active wallet's eligibility. `claim` submits the
  claim transaction (only for entries with verified claim
  contracts; check-only entries return a manual-claim hint).
  Refuses to fire a claim tx without a recent eligible scan to
  avoid wasting gas on revert. State persists scans + claims
  history.

### Internal

- `clawmes/commands/mev_protect.py`, `scan.py`, `airdrop.py` (all
  new). `limit_order.py` extended with `_materialize_bracket` and
  bracket flag parsing.
- 4482 tests pass at 100% coverage (was 4372). README counts:
  94 → 97 commands.

## 0.14.0 — 2026-05-28

### Added — the agent manages the agent

The Clawmes tier philosophy now reads:

  * FREE       — you do the work with the agent.
  * HOLDER     — you manage the agent.
  * UNLIMITED  — the agent manages the agent.

Four new commands operationalize that third line. Each one
handles a piece of the operator's job — reviewing performance,
remembering goals, tuning schedules, doing research — instead
of just executing tasks.

- **`/report now|daily|weekly|objectives`** (UNLIMITED) —
  autonomous performance report across every automation surface
  (`/dca`, `/copy`, `/alerts`, `/limit_order`, `/sniper`,
  `/objective`). Counts active schedules, executions, ETH
  spent, success rate, fires, and surfaces objectives + their
  progress. No prompt required.

- **`/objective add <name> <goal> --budget <eth>
  [--horizon <interval>]`** (UNLIMITED) — high-level goal
  tracking. Stores a named, free-text goal with a budget cap.
  Progress is computed forward-only from the registration
  anchor, summing successful executions across `/dca` + `/copy`.
  Surfaced in `/report objectives` and `/objective progress
  <id>`.

- **`/auto-tune review|apply [<id>]|history`** (UNLIMITED) —
  autonomous schedule review. Walks every automation surface,
  applies conservative heuristics, and produces recommendations:
    - DCA with success rate < 50% (5+ executions) → suggest pause
    - Copy with 0 successful in 7 days but tx activity → suggest pause
    - Sniper idle 7+ days with budget remaining → suggest review
    - Limit order active 30+ days → suggest review / cancel
    - Wallet alert active 30+ days, never fired → suggest review
  `apply` commits recommended pauses; all mutations are reversible
  via the underlying command's `resume`.

- **`/research <token> [--no-narrative] [--json]`** (UNLIMITED) —
  structured token research. Pulls DexScreener pair data,
  `defi_price` fallback, Clawnch launch metadata, and derived
  risk flags (`low_liquidity`, `thin_volume_24h`,
  `major_drawdown_24h`, `blow_off_top_candidate`) into one
  report. Optional LLM-synthesized 3-4 sentence summary via
  OpenGateway; graceful fallback when unavailable.

### Internal

- `clawmes/commands/report.py`, `objective.py`, `auto_tune.py`,
  `research.py` (all new).
- 4372 tests pass at 100% coverage (was 4143). README counts:
  90 → 94 commands.

## 0.13.0 — 2026-05-28

### Added — more UNLIMITED features

Three additions deepening the autopilot tier.

- **`/sniper --auto-trail <pct>`** (UNLIMITED) — trailing stop-loss on
  snipes. As price rises, the stop anchor moves up to track the
  running high-water mark. When price drops `pct%` below the high-
  water mark, we sell our balance back to ETH. Pairs naturally with
  `--auto-sell`: take-profit triggers a full exit immediately, while
  trailing-stop lets winners run.

  Trigger priority when both are configured: `take_profit` →
  `trailing_stop` → `stop_loss`. Whichever fires first wins.

- **`/dca --conditional <expr>`** (UNLIMITED) — only fire if the
  conditional evaluates to True at tick time. Grammar:

    `price_above:<token>:<usd>`  — fire only when price(token) > USD
    `price_below:<token>:<usd>`  — fire only when price(token) < USD

  Blocked runs are recorded with `status: conditional_blocked` and
  the schedule's `next_run_epoch` still advances so the cadence stays
  intact. Use case: "buy CLAWNCH every hour BUT only when BTC > $100k"
  or "DCA into ETH BUT only when ETH < $3000" (buy-the-dip).

- **`/strategy`** (UNLIMITED) — preset templates that compose multiple
  commands. UNLIMITED-gated. Three presets to start:

  * `whale-shadow <wallet> <eth_per_copy>` → `/copy add --invert` +
    `/alerts add wallet` — copy a whale's buys + sells, get notified
    on any new ERC-20 receipt.
  * `dca-and-snipe <token> <dca_eth> <interval> <snipe_eth>` →
    `/dca add` + `/sniper add` — DCA into a token on a cadence,
    also snipe any newly-launched tokens.
  * `laddered-tp <eth_per_copy> <wallet> <tp1>:<tp2>:<tp3>` →
    `/copy add` + `/sniper add --auto-sell` — copy a wallet's buys
    with a take-profit ladder attached.

  `/strategy list` shows available presets. `/strategy preview` shows
  what would be created without actually creating it. `/strategy
  apply` materializes the steps. `/strategy history` shows past
  applications.

### Internal

- `clawmes/commands/sniper.py`: `auto_trail_pct` field added to
  config + watch. `_evaluate_auto_sell_watches` extended with
  `high_water_price_usd` tracking and trailing-stop math. Watch
  schema includes `high_water_price_usd` and `trail_drawdown_pct`
  on close. Backward-compat: watches without `high_water_price_usd`
  seed from `buy_price_usd` on first tick.
- `clawmes/commands/dca.py`: `_parse_conditional`,
  `_describe_conditional`, `_conditional_satisfied` helpers added.
  `_run_due_with_lines` checks conditional first; blocked runs
  advance `next_run_epoch` without executing.
- `clawmes/commands/strategy.py` (new). `_PRESETS` registry +
  `_dispatch_step` lazy command imports.
- 4143 tests pass at 100% coverage (was 4068). README: +1 command
  (89 → 90).

## 0.12.0 — 2026-05-28

### Added — paid-tier expansions

Four feature additions across existing commands, expanding what each
paid tier unlocks.

- **`/copy --invert`** (HOLDER) — also mirror SELLS from the watched
  wallet. When the wallet sends an ERC-20 OUT (to a DEX router,
  presumably to sell), we check our balance for that token and
  submit a corresponding sell on our side. Captures the "whale
  exiting" signal so followers can ride down with the leader too,
  not just on the way up.

- **`/copy --multi <0xa,0xb,…>`** (UNLIMITED) — follow multiple
  wallets in one follow record. The primary wallet is the first
  argument; extras come from `--multi`. The poller iterates over
  all of them on each tick. Combined with `--invert` you can build
  "watch this cohort of whales; copy buys + sells from any of them."

- **`/alerts add … --webhook <url>`** (HOLDER) — POST a JSON payload
  to the configured URL when the alert fires. Payload includes
  `alert_id`, `sender_id`, `type`, `fired_at`, `detail`, and the
  full fire metadata. Webhook delivery never blocks or breaks the
  alert tick — failures are recorded on the fire history.

- **`/sniper --auto-sell <gain_pct>:<loss_pct>`** (UNLIMITED) — full
  lifecycle automation. After each successful snipe, the config
  registers an auto-sell watch anchored to the buy-time USD price.
  On subsequent ticks the scheduler polls the token's price; when
  the price moves up by `gain_pct` (take-profit) or down by
  `loss_pct` (stop-loss), the watch fires a sell-our-balance
  transaction. The watch transitions to `filled` on success or
  `close_failed` if the sell fails.

### Internal

- `_split_flags` in `copy.py` / `sniper.py` / `alerts.py` rewritten
  to handle bare flags. Now peeks at the next token: if it's another
  `--flag` or doesn't exist, the current flag captures an empty
  string. Fixes a latent bug where `--invert` silently swallowed the
  next `--flag` as its value.
- `clawmes/commands/copy.py`: new `_all_watched_wallets`,
  `_basescan_token_transfers_all`, `_execute_sell`,
  `_read_our_token_balance`. `_process_follow` refactored to iterate
  primary + extra wallets and dispatch on transfer direction.
  `_EDITABLE` extended with `invert` + `extra_wallets`.
- `clawmes/commands/alerts.py`: new `_split_alert_flags`,
  `_post_webhook`. Add helpers now thread `webhook_url` through to
  the persisted alert; tick loop POSTs on fire and records delivery
  status.
- `clawmes/commands/sniper.py`: new `_fetch_price`,
  `_evaluate_auto_sell_watches`, `_submit_token_sell`,
  `_read_our_token_balance`. `_run_due_with_lines` extended to
  evaluate auto-sell watches after the main snipe loop. Successful
  snipes anchor a watch at the buy-time price.
- 4068 tests pass at 100% coverage (was 3984). README unchanged on
  the counts row — all additions are flags / behaviors on existing
  commands.

## 0.11.0 — 2026-05-27

### Added — Clawmes Unlimited: autopilot tier + `/sniper` + `/agent --ai`

A new top tier and two extreme features that live behind it.

- **`UNLIMITED` tier** — any wallet holding **100,000,000+ $CLAWNCH**
  (~$1,050 at session-time price). Sits above `HOLDER` (10M+) and
  unlocks autopilot features that go beyond what a normal trader
  needs. The Tier enum is now ordered (FREE=0 < HOLDER=1 <
  UNLIMITED=2); checks use `tier.value >= required.value` so an
  UNLIMITED holder automatically passes any HOLDER gate.

- **`/sniper`** — auto-buy newly-launched Clawnch tokens.
  - `/sniper add <eth_amount> [--max-buys N] [--source X]
    [--symbol-filter <regex>] [--max-mcap <usd>] [--max-age <seconds>]
    [--slippage <bps>]`
  - Watches `/api/launches` on the registry tick. Any new launch
    matching the filters triggers a `defi_swap` buy at the configured
    ETH amount.
  - Filters compose: source attribution (`clawmes` / `clawncher` /
    `4claw` / `moltbook`), symbol regex, max market cap, max age
    (default 10min — older launches are presumed already sniped).
  - Auto-exhausts after `--max-buys` snipes (default 10). Per-snipe
    slippage cap. Per-config errors caught internally.
  - `SniperSchedulerService` (id `clawmes.sniper_scheduler`) drives
    the loop. UNLIMITED tier required to `/sniper add`.

- **`/agent --ai`** — LLM fallback for prompts the regex parser can't
  understand. Layered on top of the existing intent matcher: regex
  runs first, the LLM only sees segments that didn't match.
  - The LLM is constrained to rewrite each segment as one of the
    supported phrasings; rewrites are re-validated through `_parse_one`
    so the LLM can't invent new commands.
  - Powered by the existing OpenGateway service. Falls back gracefully
    when OpenGateway is unreachable / errors — original "couldn't
    parse" message displays.
  - UNLIMITED tier required.

### Internal

- `clawmes/services/token_gate.py` — `UNLIMITED_THRESHOLD =
  100_000_000` constant added. Tier enum reordered (now numeric values
  for ranked comparison). `_TIER_THRESHOLD_TOKENS` + `_TIER_LABELS`
  internal maps so error messages mention "Holder tier" vs "Clawmes
  Unlimited" correctly.
- `clawmes/commands/sniper.py` + `clawmes/services/sniper_scheduler.py`.
- `clawmes/commands/agent_plan.py` extended with `_llm_extract` +
  `_extract_llm_text` helpers and `use_ai` parameter on `_cmd_parse`.
- 3984 tests pass at 100% coverage (was 3831). README counts: 88 →
  89 commands, 28 → 29 services.

## 0.10.1 — 2026-05-27

### Changed — HOLDER tier threshold raised 10k → 10M $CLAWNCH

The token-gating threshold moves from 10,000 $CLAWNCH (~$0.10) to
**10,000,000 $CLAWNCH** (~$105 at session-time price). The previous
threshold was too low to function as a meaningful commitment signal —
anyone could clear it for less than a coffee. The 10M threshold puts
the HOLDER tier in the same conviction range as actually deploying a
token through the launchpad (which requires the same 1M+ burn).

What this changes:
- Wallets between 10k–10M $CLAWNCH that were previously HOLDER tier
  are now free tier. They still get every command, just with the
  documented free-tier caps: 1 active `/dca`, 1 active `/copy`, 3
  active `/alerts`, 1 active `/limit_order`, no `/dca` safeguard
  flags, no `/copy --pct`, no `/agent` multi-step.
- Existing schedules / follows / orders on those wallets keep
  running. Caps only bite on the next `add` attempt.

Why now: the gate exists to drive $CLAWNCH demand from clawmes power
users. At 10k it was symbolic. At 10M it's signal.

### Internal

- `clawmes/services/token_gate.py`: `HOLDER_THRESHOLD = 10_000_000`.
- Tests updated to use 5M / 11M / 50M values straddling the new
  threshold.
- 3831 tests pass at 100% coverage (unchanged).

## 0.10.0 — 2026-05-27

### Added — trader power-pack: `/portfolio` v2 + `/limit_order` + multi-wallet + `/copy --pct`

Four features expanding clawmes from a Base-launchpad agent into a
generalist trader's home base.

- **`/portfolio` v2** — new subcommands route to the existing
  `cost_basis` tool for P&L views:
  - `/portfolio` (default) — live balance summary via
    `defi_balance` (unchanged behavior + a "P&L views" hint footer).
  - `/portfolio pnl` — overall realized + unrealized summary.
  - `/portfolio realized` — closed positions only.
  - `/portfolio unrealized` — open positions marked at last price.
  - `/portfolio export` — full lot-by-lot ledger.

- **`/limit_order`** — DEX limit buys + take-profit sells.
  - `/limit_order add buy <token> <eth_amount> below <usd>` — spend
    ETH when price drops.
  - `/limit_order add sell <token> <amount> above <usd>` — sell
    held tokens when price rises.
  - Full mutation surface: `list`, `pause`, `resume`, `cancel`,
    `edit`, `tick`, `status`, `history`. Same vocabulary as `/dca`
    and `/copy`.
  - State machine: active → filled (swap succeeded) | failed
    (max_attempts exhausted) | paused (manual) | cancelled (manual).
    Terminal states are sticky; only paused orders can `resume`.
  - `LimitOrderSchedulerService` (id `clawmes.limit_order_scheduler`)
    ticks on the registry cadence. Polls prices via `defi_price` and
    submits swaps via `defi_swap` when thresholds cross.
  - Free tier cap: 1 active order. HOLDER tier: unlimited.

- **Multi-wallet tag system** on `/wallet`:
  - `/wallet tag <name>` — bookmark the active wallet under `<name>`
    (records address + chain + mode).
  - `/wallet tags` — list all saved tags.
  - `/wallet untag <name>` — remove a tag.
  - `/wallet show <name>` — print the address/chain/mode under one tag.
  - Tags persist in `${HERMES_HOME}/clawmes/wallet/tags.json`.
    They're metadata-only bookmarks; switching the active wallet
    still goes through the existing `/connect*` flow.

- **`/copy --pct N`** — percentage-based copy sizing.
  - Scale each copy to `N%` of the target wallet's outgoing ETH on
    the seen tx, capped at `eth_per_copy` (so a whale's huge buy
    can't drain the follower's wallet).
  - Falls back to fixed `eth_per_copy` when target tx had no ETH
    value (token-token swap) or when the lookup fails.
  - Reads parent-tx ETH value via Basescan's
    `eth_getTransactionByHash` proxy endpoint — one RPC call per
    detected copy, bounded by the existing per-tick cap of 20.
  - HOLDER tier feature.

### Internal

- `clawmes/commands/limit_order.py` (+ tests).
- `clawmes/services/limit_order_scheduler.py` (+ tests).
- `clawmes/commands/copy.py` extended with `_compute_copy_amount`,
  `_get_tx_eth_value`, and `--pct` flag handling.
- `clawmes/commands/balance.py` extended with `_render_pnl` +
  subcommand routing.
- `clawmes/commands/wallet.py` extended with tag subcommands +
  `_load_tags`/`_save_tags` JSON state.
- `clawmes/services/token_gate.py` `FREE_TIER_CAPS` extended with
  `limit_order: 1`.
- 3831 tests pass at 100% coverage (was 3660). README counts: 87 →
  88 commands, 27 → 28 services.

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
