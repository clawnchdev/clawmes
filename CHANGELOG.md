# Changelog

All notable changes to clawmes are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

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
