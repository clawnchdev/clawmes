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

# Hermes plugin discovery has two file requirements:
#   * Repo-root plugin.yaml + __init__.py — what `hermes plugins list`
#     and `discover_and_load` look for at the plugin's install dir.
#   * Inner clawmes/plugin.yaml — bundled in the wheel for
#     ``importlib.resources`` access at runtime.
# Both must stay in sync; the ``test_plugin_yaml_copies_match`` test
# enforces that.
_REPO_ROOT = Path(clawmes.__file__).resolve().parent.parent
_ROOT_MANIFEST_PATH = _REPO_ROOT / "plugin.yaml"
_INNER_MANIFEST_PATH = Path(clawmes.__file__).parent / "plugin.yaml"

# Default to the repo-root copy — it's what Hermes discovers.
_MANIFEST_PATH = _ROOT_MANIFEST_PATH


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


def test_plugin_yaml_copies_match():
    """Repo-root and inner plugin.yaml must stay byte-for-byte
    identical. Either file can drift independently — this test
    catches the drift the moment it happens."""
    if not _INNER_MANIFEST_PATH.exists():
        return  # inner copy may be removed in a later commit
    root = yaml.safe_load(_ROOT_MANIFEST_PATH.read_text())
    inner = yaml.safe_load(_INNER_MANIFEST_PATH.read_text())
    assert root == inner, (
        f"plugin.yaml drift between {_ROOT_MANIFEST_PATH} and "
        f"{_INNER_MANIFEST_PATH}. Update both or remove the inner copy."
    )


def test_repo_root_shim_present():
    """Hermes' git-install loader requires __init__.py and plugin.yaml
    at the install dir (= repo root). This test guards both paths."""
    init_at_root = _REPO_ROOT / "__init__.py"
    yaml_at_root = _REPO_ROOT / "plugin.yaml"
    assert init_at_root.exists(), (
        f"Missing repo-root __init__.py at {init_at_root}. Hermes' "
        "git-install loader and `plugins list` both require this file."
    )
    assert yaml_at_root.exists(), (
        f"Missing repo-root plugin.yaml at {yaml_at_root}. Hermes' "
        "git-install loader and `plugins list` both require this file."
    )


def test_repo_root_shim_exposes_register():
    """The repo-root shim must re-export register and __version__ from
    the inner package so Hermes can call them after spec-load."""
    import importlib.util
    import sys

    init_path = _REPO_ROOT / "__init__.py"
    # Use a unique module name to avoid colliding with the already-
    # imported `clawmes` in this test process.
    real = sys.modules.get("clawmes")
    sys.modules.pop("clawmes", None)

    try:
        spec = importlib.util.spec_from_file_location(
            "_clawmes_shim_test",
            init_path,
            submodule_search_locations=[str(_REPO_ROOT)],
        )
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        assert hasattr(module, "register"), "shim missing register()"
        assert callable(module.register)
        assert hasattr(module, "__version__"), "shim missing __version__"
        assert module.__version__ == clawmes.__version__
    finally:
        if real is not None:
            sys.modules["clawmes"] = real
        else:
            sys.modules.pop("clawmes", None)


def test_module_alias_for_namespaced_load():
    """When Hermes loads via spec_from_file_location with a namespaced
    module name (e.g. hermes_plugins.clawmes), the inner __init__.py
    aliases ``clawmes`` in sys.modules so absolute imports throughout
    the package keep working. This test simulates Hermes' loader path
    and asserts the alias is set up before the rest of __init__.py
    runs (otherwise the absolute imports inside __init__.py would
    fail with 'cannot import name X from clawmes').
    """
    import importlib.util
    import sys
    import types

    inner_init = Path(clawmes.__file__)
    inner_dir = inner_init.parent

    # Snapshot existing clawmes entry so we can restore it
    real_clawmes = sys.modules.get("clawmes")
    sys.modules.pop("clawmes", None)

    # Set up a fake parent namespace package the way Hermes does
    parent_name = "hermes_test_namespace"
    sys.modules.pop(f"{parent_name}.clawmes", None)
    real_parent = sys.modules.get(parent_name)
    if real_parent is None:
        ns_pkg = types.ModuleType(parent_name)
        ns_pkg.__path__ = []
        ns_pkg.__package__ = parent_name
        sys.modules[parent_name] = ns_pkg

    try:
        spec = importlib.util.spec_from_file_location(
            f"{parent_name}.clawmes",
            inner_init,
            submodule_search_locations=[str(inner_dir)],
        )
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        module.__package__ = f"{parent_name}.clawmes"
        module.__path__ = [str(inner_dir)]
        sys.modules[f"{parent_name}.clawmes"] = module

        spec.loader.exec_module(module)

        # The alias makes `clawmes` resolve to the same module object
        assert sys.modules.get("clawmes") is module
        assert hasattr(module, "register")
        assert callable(module.register)
    finally:
        sys.modules.pop(f"{parent_name}.clawmes", None)
        if real_parent is None:
            sys.modules.pop(parent_name, None)
        if real_clawmes is not None:
            sys.modules["clawmes"] = real_clawmes
        else:
            sys.modules.pop("clawmes", None)
