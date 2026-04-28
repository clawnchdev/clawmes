"""Tests that the plugin.yaml manifest matches the implemented surface.

The manifest is the source of truth for what users see in
``hermes plugins list``. Listing tools / hooks that aren't actually
wired in ``register(ctx)`` is bad faith — it advertises capabilities
the plugin doesn't have, leading to "tool not found" errors at runtime.
These tests fail the build any time the two drift.
"""

from __future__ import annotations

from pathlib import Path

import yaml

import clawmes
from clawmes import hooks as hooks_module
from clawmes import tools as tools_module

_MANIFEST_PATH = Path(clawmes.__file__).parent / "plugin.yaml"


def _load_manifest() -> dict:
    return yaml.safe_load(_MANIFEST_PATH.read_text())


class FakeCtx:
    """Recorder that captures everything ``register(ctx)`` registers."""

    def __init__(self) -> None:
        self.tools: list[dict] = []
        self.commands: list[dict] = []
        self.hooks: list[str] = []

    def register_tool(self, **kw):
        self.tools.append(kw)

    def register_command(self, **kw):
        self.commands.append(kw)

    def register_hook(self, name, callback):  # noqa: ARG002
        self.hooks.append(name)


def test_manifest_tools_match_register_all():
    ctx = FakeCtx()
    tools_module.register_all(ctx)
    actual = sorted(t["name"] for t in ctx.tools)

    manifest = _load_manifest()
    declared = sorted(manifest.get("provides_tools", []))

    assert actual == declared, (
        f"plugin.yaml provides_tools drifted from tools.register_all().\n"
        f"  declared:    {declared}\n"
        f"  actual:      {actual}\n"
        f"  in manifest only: {sorted(set(declared) - set(actual))}\n"
        f"  in code only:     {sorted(set(actual) - set(declared))}"
    )


def test_manifest_hooks_match_register_all():
    ctx = FakeCtx()
    hooks_module.register_all(ctx)
    actual = sorted(set(ctx.hooks))

    manifest = _load_manifest()
    declared = sorted(manifest.get("provides_hooks", []))

    assert actual == declared, (
        f"plugin.yaml provides_hooks drifted from hooks.register_all().\n"
        f"  declared: {declared}\n"
        f"  actual:   {actual}"
    )


def test_manifest_required_keys_present():
    manifest = _load_manifest()
    for key in ("name", "version", "description", "author", "kind"):
        assert key in manifest, f"plugin.yaml missing required key: {key}"


def test_manifest_version_matches_package():
    manifest = _load_manifest()
    assert manifest["version"] == clawmes.__version__, (
        f"plugin.yaml version ({manifest['version']!r}) does not match "
        f"clawmes.__version__ ({clawmes.__version__!r})"
    )
