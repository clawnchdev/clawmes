# Changelog

All notable changes to clawmes are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

### Added

- Plugin scaffold with `register(ctx)` entry point.
- Tool registry with `@write_tool` / `@read_tool` decorators that wrap
  handlers with the readonly + policy + delegation + ledger gating
  pipeline.
- Service registry and `Service` base class with managed `start()` /
  `stop()` / `tick()` lifecycle.
- `lib/` utilities: `tool_result`, `params`, `paths` (profile-aware via
  `hermes_constants.get_hermes_home`), `logger`, `chains`, `addr`,
  `decimals`, `http`, `time`.
- Persona system with idempotent `SOUL.md` install
  (`persona.ensure_soul_md()`).
- Bundled `SOUL.md` carrying the clawmes identity, capability summary,
  security model, and onboarding-persona table.
- All eight Hermes lifecycle hooks scaffolded: `pre_tool_call`,
  `post_tool_call`, `pre_llm_call`, `pre_gateway_dispatch`,
  `on_session_*`, `transform_terminal_output`, `transform_tool_result`,
  `subagent_stop`.
- Wallet abstraction with three modes: WalletConnect (delegates to Node
  bridge), local key (BIP-39 + scrypt + AES-256-GCM + macOS Keychain via
  `keyring`), Bankr custodial (HTTP).
- Policy engine scaffold (`evaluator`, `confirm_store` for one-time
  nonces).
- Event-sourced transaction ledger scaffold (`tx_ledger`).
- Compound action engine scaffold (IR, compiler, validator, scheduler,
  executor, time/price/on-chain triggers).
- Node sub-process bridges scaffold (`process` base, `wc_client`,
  `sa_client`, `installer`).
- Onboarding flow scaffold with five built-in personas (professional,
  degen, chill, technical, mentor) plus `custom`.
- CLI subcommand tree (`hermes clawmes init|doctor|wallet|plans|policy
  |persona|skills|update|version|status|logs|uninstall`).
- First concrete tool: `transfer` (skeleton — handler returns
  `not_implemented` until wallet bridge lands).
- First concrete skill bundle: `transfer/SKILL.md`.
- Test scaffold (smoke test for `register(ctx)`, import sanity).
- CI workflow (`.github/workflows/ci.yml`).
- `HERMES_PARITY.md` upstream API contract document.
- `BANKR_INTEGRATION.md` feature tier breakdown (ported from openclawnch).

### Notes

This release is pre-alpha. The plugin imports cleanly and registers
surfaces with Hermes, but most tool handlers return `not_implemented`
errors. Real on-chain execution lands in v0.1.0 once the wallet bridges
and core services (RPC, gas, price, 0x) are wired through.
