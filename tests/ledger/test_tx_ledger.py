"""Tests for clawmes.ledger.tx_ledger."""

from __future__ import annotations

import json

import pytest

from clawmes.ledger.tx_ledger import TxLedger, TxRecord, record_tx


@pytest.fixture
def ledger(tmp_path):
    return TxLedger(path=tmp_path / "events.jsonl")


@pytest.fixture(autouse=True)
def isolate_state(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))


class TestAppend:
    def test_creates_file_on_first_write(self, ledger):
        assert not ledger.path.exists()
        ledger.append(_make_record())
        assert ledger.path.exists()

    def test_each_record_is_one_line(self, ledger):
        ledger.append(_make_record())
        ledger.append(_make_record())
        ledger.append(_make_record())
        lines = ledger.path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 3

    def test_written_record_round_trips_to_json(self, ledger):
        rec = _make_record(tool_name="defi_swap")
        ledger.append(rec)
        line = ledger.path.read_text(encoding="utf-8").strip()
        parsed = json.loads(line)
        assert parsed["tool_name"] == "defi_swap"
        assert parsed["session_id"] == "sess-1"

    def test_creates_parent_dir(self, tmp_path):
        deep = tmp_path / "a" / "b" / "c" / "events.jsonl"
        ledger = TxLedger(path=deep)
        ledger.append(_make_record())
        assert deep.exists()


class TestIterRecords:
    def test_empty_when_file_missing(self, ledger):
        assert ledger.iter_records() == []

    def test_reads_back_in_order(self, ledger):
        for i in range(3):
            ledger.append(_make_record(session_id=f"s-{i}"))
        records = ledger.iter_records()
        assert [r.session_id for r in records] == ["s-0", "s-1", "s-2"]

    def test_skips_blank_lines(self, ledger):
        ledger.append(_make_record())
        ledger.path.write_text(
            ledger.path.read_text(encoding="utf-8") + "\n\n",
            encoding="utf-8",
        )
        records = ledger.iter_records()
        assert len(records) == 1

    def test_skips_corrupt_lines(self, ledger, caplog):
        ledger.append(_make_record())
        # Append a non-JSON line
        with ledger.path.open("a", encoding="utf-8") as fh:
            fh.write("not-json\n")
        ledger.append(_make_record())

        records = ledger.iter_records()
        assert len(records) == 2  # the corrupt line is skipped, others kept


class TestRecordTx:
    def test_default_path_writes_under_hermes_home(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        # Reset module-level cached instance
        import clawmes.ledger.tx_ledger as mod

        monkeypatch.setattr(mod, "_instance", None)

        record_tx(
            session_id="sess",
            user_id="user",
            tool_name="transfer",
            action_args={"to": "alice.eth", "amount": "1"},
            tx_hash="0xabcd",
            chain_id=8453,
        )
        target = tmp_path / "clawmes" / "ledger" / "events.jsonl"
        assert target.exists()
        line = target.read_text(encoding="utf-8").strip()
        rec = json.loads(line)
        assert rec["tool_name"] == "transfer"
        assert rec["chain_id"] == 8453
        assert rec["tx_hash"] == "0xabcd"

    def test_status_default_is_submitted(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        import clawmes.ledger.tx_ledger as mod

        monkeypatch.setattr(mod, "_instance", None)
        record_tx(
            session_id="s",
            user_id="u",
            tool_name="transfer",
            action_args={},
        )
        target = tmp_path / "clawmes" / "ledger" / "events.jsonl"
        rec = json.loads(target.read_text(encoding="utf-8").strip())
        assert rec["status"] == "submitted"

    def test_timestamp_is_iso_z_form(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        import clawmes.ledger.tx_ledger as mod

        monkeypatch.setattr(mod, "_instance", None)
        record_tx(
            session_id="s",
            user_id="u",
            tool_name="transfer",
            action_args={},
        )
        target = tmp_path / "clawmes" / "ledger" / "events.jsonl"
        rec = json.loads(target.read_text(encoding="utf-8").strip())
        assert rec["ts"].endswith("Z")
        assert "T" in rec["ts"]


class TestThreadSafety:
    def test_concurrent_appends(self, ledger):
        import threading

        def worker(i):
            ledger.append(_make_record(session_id=f"s-{i}"))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        records = ledger.iter_records()
        assert len(records) == 50
        # Each session_id appears exactly once — no truncation
        ids = {r.session_id for r in records}
        assert len(ids) == 50


# Helpers ------------------------------------------------------------------


def _make_record(**overrides) -> TxRecord:
    base = {
        "ts": "2026-04-27T12:00:00Z",
        "session_id": "sess-1",
        "user_id": "user-1",
        "tool_name": "transfer",
        "action_args": {"to": "alice.eth"},
        "tx_hash": None,
        "chain_id": 8453,
        "from_addr": None,
        "to_addr": None,
        "value_wei": None,
        "status": "submitted",
    }
    base.update(overrides)
    return TxRecord(**base)
