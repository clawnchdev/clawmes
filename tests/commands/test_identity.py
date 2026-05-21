"""Tests for the /identity slash command."""

from __future__ import annotations

import pytest

from clawmes.commands import identity as identity_cmd
from clawmes.services import identity as id_mod


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(id_mod, "_instance", None)


class TestShow:
    async def test_no_identity(self):
        out = await identity_cmd.handle_identity("")
        assert "No agent identity set" in out
        assert "/identity create" in out

    async def test_with_identity(self):
        # Generate one via the service directly.
        id_mod.get_identity_service().generate()
        out = await identity_cmd.handle_identity("")
        assert "Agent identity:" in out
        assert "did:key:z" in out

    async def test_recording_is_best_effort_when_record_raises(self, monkeypatch):
        # When command_history's record_command_call raises (e.g. service
        # not started, disk full, anything), /identity must still return
        # cleanly. Covers the bare ``except: pass`` branch.
        from clawmes.services import command_history as ch_mod

        def _boom(*a, **kw):
            raise RuntimeError("simulated record failure")

        monkeypatch.setattr(ch_mod, "record_command_call", _boom)
        out = await identity_cmd.handle_identity("")
        assert isinstance(out, str)

    async def test_recording_when_module_available(self, monkeypatch):
        # Cover the happy path: fake command_history module with a
        # record_command_call function — /identity should call it.
        import sys
        import types

        captured: list[tuple[str, str, str]] = []
        fake_mod = types.ModuleType("clawmes.services.command_history")
        fake_mod.record_command_call = lambda name, args, result: captured.append(  # type: ignore[attr-defined]
            (name, args, result)
        )
        monkeypatch.setitem(sys.modules, "clawmes.services.command_history", fake_mod)

        await identity_cmd.handle_identity("")
        assert any(name == "identity" for name, _, _ in captured)


class TestCreate:
    async def test_basic_create(self):
        out = await identity_cmd.handle_identity("create")
        assert "Generated agent identity" in out
        assert id_mod.get_identity_service().has_identity()

    async def test_refuses_overwrite_without_force(self):
        await identity_cmd.handle_identity("create")
        out = await identity_cmd.handle_identity("create")
        assert "already exists" in out
        assert "force" in out

    async def test_create_force_replaces(self):
        first = await identity_cmd.handle_identity("create")
        second = await identity_cmd.handle_identity("create force")
        assert "Generated" in second
        # The two should produce different keys.
        first_did = next(line for line in first.splitlines() if "DID:" in line)
        second_did = next(line for line in second.splitlines() if "DID:" in line)
        assert first_did != second_did


class TestUnknownArg:
    async def test_returns_usage(self):
        out = await identity_cmd.handle_identity("explode")
        assert "Unknown" in out
        assert "/identity" in out


class TestRegister:
    def test_registers_one_command(self):
        captured = []

        class FakeCtx:
            def register_command(self, **kw):
                captured.append(kw["name"])

        identity_cmd.register(FakeCtx())
        assert captured == ["identity"]
