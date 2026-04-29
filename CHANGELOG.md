# Changelog

All notable changes to clawmes are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

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

### Security

- `SECURITY.md` — full threat model, recovery checklist, audit
  status. `security@clawn.ch` reporting endpoint.
- Keystore cross-validated against Python stdlib `hashlib.scrypt`;
  tamper-detection tests for ciphertext, tag, nonce, salt; OWASP
  parameter assertion (N≥2^17, r≥8, p≥1, 32-byte derived key).
- `rotate_password()` keystore primitive.

### Tests

- 1930 tests passing, 100% coverage on every commit during the
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
