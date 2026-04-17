# Contributing to clawmes

Thanks for your interest. Clawmes is open source under the MIT license. We
welcome bug reports, feature requests, and pull requests.

## Quick rules

1. **No core-Hermes edits.** Clawmes is a plugin — it must not modify
   files inside the `hermes-agent` package. If you need a capability that
   Hermes doesn't expose, propose a generic upstream change first
   (`PluginContext` method, new hook, etc.) and only then add the
   downstream consumer here.
2. **Profile-correct paths.** Always use `clawmes.lib.paths.*` (which
   wraps `hermes_constants.get_hermes_home`). Hardcoding `~/.hermes`
   breaks profile users.
3. **Tools return JSON strings, not objects.** Use the helpers in
   `clawmes.lib.tool_result` (`text_result`, `json_result`,
   `error_result`).
4. **Write tools must use `@write_tool`.** The decorator wires the
   readonly + policy + delegation + ledger gates. Bypassing it for any
   write action is a security regression.
5. **Cache-aware.** The system prompt is sacred. Inject dynamic context
   via `pre_llm_call` returning `{"context": ...}` (which lands in the
   user message), never by mutating the system prefix.
6. **No new gateway adapters from a plugin.** That requires a Hermes
   upstream PR.

## Development setup

```bash
git clone https://github.com/clawnchdev/clawmes
cd clawmes
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# verify imports + register(ctx)
pytest tests/test_register.py -v
```

You'll need:

- Python ≥ 3.11
- Node ≥ 20 (for the WC + SA bridges, only required at runtime — tests
  mock these)
- A working Hermes Agent install (`pip install hermes-agent`)

## Running tests

```bash
pytest                            # all unit tests
pytest -m "not slow"              # skip slow tests
pytest tests/tools/               # just the tools/ tree
pytest --cov=clawmes              # with coverage
```

The integration tests under `tests/integration/test_bridges.py` spawn
real Node subprocesses and require Node ≥ 20 in `PATH`. Set
`RUN_BRIDGE_INTEGRATION=1` to enable.

End-to-end tests under `tests/e2e/` require a running Hermes gateway and
a local Anvil node. Set `RUN_E2E=1` to enable; expect them to be slow.

## Lint + types

```bash
ruff check clawmes/               # static analysis
ruff format clawmes/              # auto-format
mypy clawmes/                     # type-check (non-strict)
```

CI runs all three on every PR.

## Adding a tool

```python
# clawmes/tools/my_tool.py
from typing import Any

from clawmes.lib.params import read_str
from clawmes.lib.tool_result import error_result, json_result
from clawmes.tools.registry import register_with_ctx, write_tool

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["foo", "bar"]},
    },
    "required": ["action"],
}


@write_tool(
    name="my_tool",
    toolset="clawmes-misc",
    description="Short, action-oriented description shown to the LLM.",
    schema=_SCHEMA,
    emoji="🔧",
)
def my_tool(args: dict[str, Any], **kwargs: Any) -> str:
    action = read_str(args, "action", required=True)
    if action == "foo":
        return json_result({"ok": True}, summary="Foo done")
    if action == "bar":
        return error_result("Bar not yet implemented", code="not_implemented")
    return error_result(f"Unknown action: {action!r}")


def register(ctx) -> None:
    register_with_ctx(ctx, my_tool)
```

Then import and call `register(ctx)` from `clawmes/tools/__init__.py:register_all`.

### Tool conventions

- **Name**: `snake_case`. Match the openclawnch tool name when porting,
  so skill bundles continue to read the same way.
- **Toolset**: one of the documented sets (`clawmes-wallet`,
  `clawmes-trading`, `clawmes-defi`, …). See PRD §8.13.
- **Schema**: OpenAI function-calling format. Always include an
  `"action"` enum if the tool has multiple verbs, and a
  `"policyConfirmationNonce"` field if it's a write tool.
- **Description**: This is what the model sees. Be specific. Include a
  hint about pre-conditions ("requires a connected wallet") so the LLM
  doesn't try to call without setup.

## Adding a service

Implement the `Service` ABC in `clawmes/services/_base.py`:

```python
# clawmes/services/my_service.py
from clawmes.services._base import Service


class MyService(Service):
    id = "clawmes.my_service"
    ticking = True   # opt into the tick loop

    def start(self) -> None:
        ...

    def stop(self) -> None:
        ...

    def tick(self) -> None:
        # called every 60s by Hermes cron via services.registry.tick_all()
        ...


_instance: MyService | None = None


def get_my_service() -> MyService:
    global _instance
    if _instance is None:
        _instance = MyService()
    return _instance
```

Then import and register from `clawmes/services/__init__.py:start_all`.

## Adding a skill

Drop a directory under `clawmes/skills/<name>/` with a `SKILL.md`. The
agentskills.io frontmatter schema applies; clawmes-specific Hermes
metadata can go under `metadata.hermes`.

Skills are registered automatically by walking the `clawmes/skills/`
directory in `clawmes/skills/__init__.py:register_all`.

## Commit style

Conventional commits — `type(scope): subject`:

```
feat(tools):     add bridge tool with LiFi quote support
fix(wallet):     correct WC pairing URI URL-encoding
docs:            note Node 22 requirement in CONTRIBUTING
chore(deps):     bump hermes-agent to 2026.5.0
test(plans):     cover loop early-termination edge case
```

Sign-off (DCO) is not required.

## Pull requests

- Pass `ruff check`, `ruff format --check`, and `mypy`.
- Cover new behavior with tests where reasonable.
- Update `CHANGELOG.md` under `## Unreleased`.
- If you touched the upstream Hermes API surface, update
  `HERMES_PARITY.md`.

## Filing issues

See https://github.com/clawnchdev/clawmes/issues. Include:

- `hermes clawmes doctor` output
- `clawmes` version, `hermes-agent` version, Python version, OS
- Minimal reproduction steps if reporting a bug
