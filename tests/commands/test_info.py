"""Tests for /history, /clear_history, /version, /about, /uptime slash commands."""

from __future__ import annotations

import pytest

from clawmes.commands import info as info_cmd
from clawmes.services import command_history as ch_mod


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(ch_mod, "_instance", None)


# --- /history -----------------------------------------------------------


class TestHandleHistory:
    async def test_empty(self):
        out = await info_cmd.handle_history("")
        assert "No recent slash commands" in out

    async def test_records_itself(self):
        await info_cmd.handle_history("")
        # After running /history, the history should contain ... /history.
        entries = ch_mod.get_command_history_service().recent()
        assert any(e["name"] == "history" for e in entries)

    async def test_lists_recent_commands(self):
        ch_mod.record_command_call("balance", "", "0.5 ETH")
        ch_mod.record_command_call("portfolio", "", "snapshot")
        out = await info_cmd.handle_history("")
        assert "/balance" in out
        assert "/portfolio" in out

    async def test_explicit_limit(self):
        for i in range(15):
            ch_mod.record_command_call(f"cmd{i}", "", f"r{i}")
        out = await info_cmd.handle_history("3")
        # Should mention "Last 3 slash command call(s)".
        assert "Last 3" in out

    async def test_caps_limit_at_20(self):
        for i in range(25):
            ch_mod.record_command_call(f"cmd{i}", "", f"r{i}")
        out = await info_cmd.handle_history("50")
        # Cap is 20 — the service's ring is 20 by default; we just verify
        # the "Last N" line says 20 (or fewer if ring smaller).
        assert "Last " in out
        assert " 20 " in out or " 20\n" in out

    async def test_bad_limit_value(self):
        out = await info_cmd.handle_history("not-a-number")
        assert "Bad limit" in out

    async def test_zero_limit_floors_to_one(self):
        # max(1, int("0")) → 1
        ch_mod.record_command_call("foo", "", "x")
        out = await info_cmd.handle_history("0")
        assert "Last 1" in out

    async def test_handles_empty_summary_lines(self):
        ch_mod.record_command_call("foo", "args here", "")
        out = await info_cmd.handle_history("")
        assert "/foo args here" in out
        assert "(no output)" in out


# --- /clear_history -----------------------------------------------------


class TestHandleClearHistory:
    async def test_clears_then_records_clear(self):
        ch_mod.record_command_call("balance", "", "0.5 ETH")
        ch_mod.record_command_call("portfolio", "", "snapshot")
        out = await info_cmd.handle_clear_history("")
        assert "cleared" in out.lower()
        # After clear, history should ONLY contain the clear_history call.
        entries = ch_mod.get_command_history_service().recent()
        assert len(entries) == 1
        assert entries[0]["name"] == "clear_history"


# --- /version -----------------------------------------------------------


class TestHandleVersion:
    async def test_returns_version_string(self):
        from clawmes._version import __version__

        out = await info_cmd.handle_version("")
        assert __version__ in out
        assert "clawmes" in out.lower()

    async def test_records_itself(self):
        await info_cmd.handle_version("")
        assert any(e["name"] == "version" for e in ch_mod.get_command_history_service().recent())


# --- /about -------------------------------------------------------------


class TestHandleAbout:
    async def test_description(self):
        out = await info_cmd.handle_about("")
        assert "clawmes" in out.lower()
        assert "Hermes" in out
        assert "MIT" in out

    async def test_records_itself(self):
        await info_cmd.handle_about("")
        assert any(e["name"] == "about" for e in ch_mod.get_command_history_service().recent())


# --- /uptime ------------------------------------------------------------


class TestHandleUptime:
    async def test_format(self):
        out = await info_cmd.handle_uptime("")
        assert "uptime" in out.lower()
        # Must end with at least seconds (e.g. "Xs").
        assert "s" in out

    async def test_records_itself(self):
        await info_cmd.handle_uptime("")
        assert any(e["name"] == "uptime" for e in ch_mod.get_command_history_service().recent())


class TestFormatDuration:
    def test_zero(self):
        assert info_cmd._format_duration(0) == "0s"

    def test_negative_clamps(self):
        assert info_cmd._format_duration(-10) == "0s"

    def test_seconds_only(self):
        assert info_cmd._format_duration(42) == "42s"

    def test_minutes_seconds(self):
        assert info_cmd._format_duration(125) == "2m 5s"

    def test_hours(self):
        assert info_cmd._format_duration(3700) == "1h 1m 40s"

    def test_days(self):
        assert info_cmd._format_duration(90061) == "1d 1h 1m 1s"


# --- registration -------------------------------------------------------


class TestRegister:
    def test_registers_five_commands(self):
        captured = []

        class FakeCtx:
            def register_command(self, **kw):
                captured.append(kw["name"])

        info_cmd.register(FakeCtx())
        assert set(captured) == {
            "history",
            "clear_history",
            "version",
            "about",
            "uptime",
        }
