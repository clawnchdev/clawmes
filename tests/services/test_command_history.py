"""Tests for clawmes.services.command_history."""

from __future__ import annotations

import pytest

from clawmes.services import command_history as ch_mod
from clawmes.services.command_history import (
    CommandHistoryService,
    get_command_history_service,
    record_command_call,
)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(ch_mod, "_instance", None)


class TestLifecycle:
    def test_start_is_noop(self):
        CommandHistoryService().start()

    def test_stop_clears(self):
        svc = CommandHistoryService()
        svc.record("foo", "", "result")
        svc.stop()
        assert svc.recent() == []


class TestRecord:
    def test_basic_record(self):
        svc = CommandHistoryService()
        svc.record("balance", "", "0.5 ETH")
        entries = svc.recent()
        assert len(entries) == 1
        assert entries[0]["name"] == "balance"
        assert entries[0]["summary"] == "0.5 ETH"

    def test_strips_leading_slash(self):
        svc = CommandHistoryService()
        svc.record("/balance", "", "x")
        assert svc.recent()[0]["name"] == "balance"

    def test_ignores_empty_name(self):
        svc = CommandHistoryService()
        svc.record("", "", "x")
        svc.record("   ", "", "x")
        # The empty-string name is silently ignored.
        assert svc.recent() == []

    def test_ignores_non_string_name(self):
        svc = CommandHistoryService()
        svc.record(123, "", "x")  # type: ignore[arg-type]
        assert svc.recent() == []

    def test_non_string_args_coerce_to_empty(self):
        svc = CommandHistoryService()
        svc.record("foo", 42, "x")  # type: ignore[arg-type]
        assert svc.recent()[0]["args"] == ""

    def test_non_string_result_coerced(self):
        svc = CommandHistoryService()
        svc.record("foo", "", {"a": 1})  # type: ignore[arg-type]
        # Result is coerced via str().
        assert "a" in svc.recent()[0]["summary"]

    def test_truncates_long_summary(self):
        svc = CommandHistoryService(summary_chars=30)
        long_result = "x" * 500
        svc.record("foo", "", long_result)
        entry = svc.recent()[0]
        assert len(entry["summary"]) <= 30
        assert entry["summary"].endswith("...")

    def test_does_not_truncate_short(self):
        svc = CommandHistoryService(summary_chars=30)
        svc.record("foo", "", "short")
        assert svc.recent()[0]["summary"] == "short"


class TestRecent:
    def test_empty(self):
        assert CommandHistoryService().recent() == []

    def test_newest_first(self):
        svc = CommandHistoryService()
        svc.record("first", "", "a")
        svc.record("second", "", "b")
        svc.record("third", "", "c")
        names = [e["name"] for e in svc.recent()]
        assert names == ["third", "second", "first"]

    def test_limit(self):
        svc = CommandHistoryService()
        for i in range(5):
            svc.record(f"cmd{i}", "", f"r{i}")
        entries = svc.recent(limit=2)
        assert len(entries) == 2
        assert entries[0]["name"] == "cmd4"
        assert entries[1]["name"] == "cmd3"

    def test_limit_zero(self):
        svc = CommandHistoryService()
        svc.record("foo", "", "x")
        assert svc.recent(limit=0) == []

    def test_limit_negative(self):
        svc = CommandHistoryService()
        svc.record("foo", "", "x")
        assert svc.recent(limit=-3) == []

    def test_ring_eviction(self):
        svc = CommandHistoryService(ring_size=3)
        for i in range(5):
            svc.record(f"cmd{i}", "", f"r{i}")
        names = [e["name"] for e in svc.recent()]
        # Newest first; oldest evicted.
        assert names == ["cmd4", "cmd3", "cmd2"]

    def test_entry_fields(self):
        svc = CommandHistoryService()
        svc.record("balance", "8453", "0.5 ETH on Base")
        entry = svc.recent()[0]
        assert set(entry.keys()) == {"timestamp", "name", "args", "summary"}
        assert isinstance(entry["timestamp"], float)
        assert entry["args"] == "8453"


class TestClear:
    def test_clear(self):
        svc = CommandHistoryService()
        svc.record("foo", "", "x")
        svc.clear()
        assert svc.recent() == []


class TestModuleLevelRecord:
    def test_record_command_call(self):
        record_command_call("balance", "", "1 ETH")
        svc = get_command_history_service()
        assert svc.recent()[0]["name"] == "balance"

    def test_record_swallows_errors(self, monkeypatch):
        # If the service raises for any reason, the helper must NOT
        # propagate — recording must never break a command.
        class BoomService:
            def record(self, *a, **kw):
                raise RuntimeError("boom")

        monkeypatch.setattr(ch_mod, "_instance", BoomService())
        # Should not raise.
        record_command_call("foo", "", "result")


class TestSingleton:
    def test_singleton(self):
        a = get_command_history_service()
        b = get_command_history_service()
        assert a is b


# --- prompt_builder integration -----------------------------------------


class TestPromptBuilderIntegration:
    def test_empty_history_injects_nothing(self):
        from clawmes.hooks import prompt_builder

        pieces: list[str] = []
        prompt_builder._append_command_history(pieces)
        assert pieces == []

    def test_recent_commands_appear_in_context(self):
        from clawmes.hooks import prompt_builder

        record_command_call("balance", "", "0.5 ETH on Base")
        record_command_call("portfolio", "", "ETH 0.5, USDC 100.0")
        pieces: list[str] = []
        prompt_builder._append_command_history(pieces)
        assert len(pieces) == 1
        block = pieces[0]
        assert "[clawmes/recent-commands]" in block
        assert "/portfolio" in block
        assert "/balance" in block
        # Newest first.
        assert block.index("/portfolio") < block.index("/balance")

    def test_truncates_long_summary_in_context(self):
        from clawmes.hooks import prompt_builder

        record_command_call("foo", "", "x" * 500)
        pieces: list[str] = []
        prompt_builder._append_command_history(pieces)
        # The 500-char string should not appear verbatim.
        assert "x" * 500 not in pieces[0]
        assert "..." in pieces[0]

    def test_service_failure_falls_through(self, monkeypatch):
        # If the service blows up, the hook MUST NOT propagate — other
        # context sources (wallet, mode, persona) should still work.
        from clawmes.hooks import prompt_builder
        from clawmes.services import command_history as ch_mod

        class BoomService:
            def recent(self, limit=10):
                raise RuntimeError("boom")

        monkeypatch.setattr(ch_mod, "_instance", BoomService())
        pieces: list[str] = []
        # Should not raise; pieces stays empty.
        prompt_builder._append_command_history(pieces)
        assert pieces == []

    def test_args_injected(self):
        from clawmes.hooks import prompt_builder

        record_command_call("balance", "ethereum", "0.5 ETH")
        pieces: list[str] = []
        prompt_builder._append_command_history(pieces)
        assert "/balance ethereum" in pieces[0]

    def test_long_first_line_truncated_in_context(self):
        # The hook trims the first line to 120 chars before showing.
        from clawmes.hooks import prompt_builder

        record_command_call("foo", "", "y" * 150)
        pieces: list[str] = []
        prompt_builder._append_command_history(pieces)
        # The injection block should NOT contain the full y*150.
        assert "y" * 150 not in pieces[0]
        assert "..." in pieces[0]
