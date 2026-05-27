"""Shared pytest fixtures.

Most tests don't need Hermes running. The ``mock_ctx`` fixture provides
a fake :class:`hermes_cli.plugins.PluginContext` that records every
``register_*`` call so tests can assert tools / commands / hooks were
actually wired without spinning up the real plugin manager.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import pytest


class FakePluginContext:
    """In-memory recorder masquerading as Hermes' PluginContext."""

    def __init__(self) -> None:
        self.tools: list[dict[str, Any]] = []
        self.commands: list[dict[str, Any]] = []
        self.hooks: dict[str, list[Any]] = defaultdict(list)
        self.cli_commands: list[dict[str, Any]] = []
        self.skills: list[dict[str, Any]] = []
        self.services: list[dict[str, Any]] = []

    def register_tool(self, **kwargs: Any) -> None:
        self.tools.append(kwargs)

    def register_command(self, **kwargs: Any) -> None:
        self.commands.append(kwargs)

    def register_hook(self, hook_name: str, callback: Any) -> None:
        self.hooks[hook_name].append(callback)

    def register_cli_command(self, **kwargs: Any) -> None:
        self.cli_commands.append(kwargs)

    def register_skill(self, **kwargs: Any) -> None:
        self.skills.append(kwargs)

    def register_service(self, **kwargs: Any) -> None:
        self.services.append(kwargs)

    def dispatch_tool(self, tool_name: str, args: dict[str, Any], **kwargs: Any) -> str:
        # Test seam — overridden by individual tests when needed.
        raise NotImplementedError("dispatch_tool not stubbed in this test")

    def inject_message(self, content: str, role: str = "user") -> bool:
        return False


@pytest.fixture
def mock_ctx() -> FakePluginContext:
    return FakePluginContext()


@pytest.fixture(autouse=True)
def _default_holder_tier(monkeypatch):
    """Default every test to HOLDER tier with no caps.

    Token gating landed in v0.9.0 — most existing tests pre-date it and
    don't care about the gate, so the autouse fixture short-circuits both
    check helpers. Tests that specifically want to exercise the gate
    re-patch these (or call ``token_gate._reset_for_tests()``) themselves.
    """
    try:
        import clawmes.services.token_gate as tg

        monkeypatch.setattr(tg, "check_tier_or_error", lambda *a, **k: None)
        monkeypatch.setattr(tg, "check_cap_or_error", lambda *a, **k: None)
    except ImportError:
        pass
