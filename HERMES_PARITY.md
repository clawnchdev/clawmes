# Hermes Compatibility Contract

This document records every Hermes Agent API that clawmes depends on, so
upstream upgrades can be checked against a single source of truth. Mirrors
the FEATURE_PARITY.md pattern from openclawnch.

## Supported Hermes versions

| Range | Status |
|---|---|
| `2026.4.x` | Primary — pinned latest stable, CI green |
| `2026.3.x` | Best-effort, CI nightly |
| `2026.2.x` | Deprecated — known incompatibilities, do not use |

Constraint in `pyproject.toml`:

```toml
"hermes-agent>=2026.4.23,<2027.0.0"
```

## Plugin Context API (Stable)

Methods on `hermes_cli.plugins.PluginContext` that clawmes consumes:

| API | Where used | Stability |
|---|---|---|
| `ctx.register_tool(name, toolset, schema, handler, requires_env, emoji)` | `clawmes/tools/registry.py:register_with_ctx` | Stable |
| `ctx.register_command(name, handler, description, args_hint)` | `clawmes/commands/__init__.py:register_all` | Stable |
| `ctx.register_hook(name, callback)` | `clawmes/hooks/__init__.py:register_all` | Stable |
| `ctx.register_cli_command(name, help, setup_fn, handler_fn, description)` | `clawmes/cli/__init__.py:register_all` | Stable |
| `ctx.register_skill(name, path, description)` | `clawmes/skills/__init__.py:register_all` | Stable |
| `ctx.dispatch_tool(tool_name, args, **kwargs)` | `clawmes/plans/executor.py` | Stable |
| `ctx.inject_message(content, role)` | _planned for v0.2 — chat-dispatch service not yet wired_ | Stable |

## Hooks consumed

Names from `hermes_cli.plugins.VALID_HOOKS`. Return contracts as documented
upstream.

| Hook | Module | Return contract |
|---|---|---|
| `pre_tool_call` | `clawmes/hooks/pre_tool_call.py` | `{"action":"block","message":...}` to veto, otherwise `None` |
| `post_tool_call` | `clawmes/hooks/after_tool_call.py` | `None` (observer; carries `duration_ms`) |
| `pre_llm_call` | `clawmes/hooks/prompt_builder.py` | `{"context": str}` or plain `str` to inject; appended to current turn's user message |
| `post_llm_call` | `clawmes/hooks/after_tool_call.py` (combined) | `None` (only fires on successful turns) |
| `pre_gateway_dispatch` | `clawmes/hooks/pre_gateway_dispatch.py` | `{"action":"skip"}`, `{"action":"rewrite","text":...}`, `{"action":"allow"}`, or `None` |
| `on_session_start` | `clawmes/hooks/on_session.py` | `None` |
| `on_session_end` | `clawmes/hooks/on_session.py` | `None` |
| `on_session_finalize` | `clawmes/hooks/on_session.py` | `None` (fires before reset on swap) |
| `on_session_reset` | `clawmes/hooks/on_session.py` | `None` |
| `transform_terminal_output` | `clawmes/hooks/transform_terminal_output.py` | `str` (mutated) — CLI display redaction |
| `transform_tool_result` | `clawmes/hooks/transform_tool_result.py` | `str` (mutated) — credential leak scrub at source |
| `subagent_stop` | `clawmes/hooks/subagent_stop.py` | `None` |

All callbacks accept `**kwargs` for forward compat.

## Internal APIs (verify each Hermes release)

These are not part of the documented plugin API but clawmes depends on them.
Each gets a CI test that fails loudly on Hermes upgrade if the symbol moves
or the signature changes.

| API | Used for |
|---|---|
| `hermes_constants.get_hermes_home()` | Profile-correct path resolution |
| `hermes_constants.display_hermes_home()` | Pretty-print paths in CLI output |
| `~/.hermes/cron/jobs.json` schema | Plan-tick scheduler integration. Clawmes adds an `internal_handler` field that Hermes ignores; we route via a `pre_llm_call` short-circuit. **Watch for**: Hermes refusing to load jobs without an LLM prompt — fallback is a thread-based scheduler. |

## Hermes features clawmes intentionally does NOT use

Documented to prevent accidental adoption later (and to assert no overlap
in the parity audit):

- `ctx.register_image_gen_provider` — image generation is out of scope.
- `ctx.register_context_engine` — we don't replace Hermes' built-in
  compression engine.
- Memory provider plugin slot (`plugins/memory/<name>/`) — we use Hermes'
  built-in memory tool through `agent_memory`.
- New gateway adapters — clawmes works on whatever channels Hermes ships.

## Known divergences from openclawnch

Where the OpenClaw plugin API surface and the Hermes plugin API surface
disagree, clawmes adapted as follows:

- **`before_prompt_build` → `pre_llm_call`.** OpenClawnch split context
  into `prependSystemContext` (cacheable, identity / agent_rules) and
  `prependContext` (per-user, dynamic). Hermes only offers `pre_llm_call`,
  which injects into the **user message** to keep the system-prompt
  prefix cache-stable. Trade-off: clawmes loses the system-prefix cache
  separation but inherits Hermes' larger built-in cacheable prefix
  (SOUL.md, BOOT.md, MEMORY.md, USER.md).
- **`message_sending` has no equivalent.** OpenClawnch used
  `{ cancel: true }` / `{ content: ... }` for outbound message
  cancellation and rewriting (onboarding suppression, credential
  redaction). Clawmes substitutes:
  - tool-result generation (`transform_tool_result`)
  - CLI display path (`transform_terminal_output`)
  - Hermes' built-in `privacy.redact_pii` for messaging output
- **`message_received` → `pre_gateway_dispatch`.** Different name, similar
  semantics — drop / rewrite / allow.
- **`gateway_start` → no direct equivalent.** Boot work moves into
  `services.start_all()` called from `register(ctx)`. For things that
  must run only when the gateway is up (vs. CLI-only), we use the
  separate gateway-hook system at `~/.hermes/hooks/clawmes-startup/`.
- **No JSON-config mutation pattern.** OpenClawnch's wrapper rewrote
  `~/.openclaw/openclaw.json` on every launch to inject plugin paths.
  Hermes uses YAML and an opt-in `plugins.enabled` allowlist; clawmes
  participates via that mechanism instead.

## Per-release checklist

When bumping `hermes-agent` in `pyproject.toml`:

1. `pip install hermes-agent==<new-version>` in a fresh venv
2. `pytest tests/test_register.py` — verifies `register(ctx)` succeeds
3. Confirm `hermes_cli.plugins.VALID_HOOKS` still includes every hook in
   the table above
4. Confirm `hermes_constants.get_hermes_home` and `display_hermes_home`
   still exist with the expected signatures
5. Run `pytest tests/e2e/test_full_swap_flow.py -m "not slow"` (mocked
   Telegram + Anvil)
6. Update the "Supported Hermes versions" table
7. Update the constraint in `pyproject.toml`
8. Add a changelog entry under `## Unreleased` noting the bump

## CI matrix

`.github/workflows/upstream-compat.yml` runs nightly:

- `hermes-agent==2026.4.23` (pinned latest stable)
- `hermes-agent~=2026.4` (latest 2026.4.x patch)
- `hermes-agent~=2026.3` (previous minor)
- `hermes-agent @ git+https://github.com/NousResearch/hermes-agent.git@main`
  (informational — failure files an issue, does not block release)
