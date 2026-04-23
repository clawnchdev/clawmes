"""Tests for the /tx, /tx_search, /tx_export, /pending commands."""

from __future__ import annotations

import json

import pytest

from clawmes.commands.tx import (
    handle_pending,
    handle_tx,
    handle_tx_export,
    handle_tx_search,
)
from clawmes.ledger import tx_ledger as ledger_module
from clawmes.ledger.tx_ledger import record_tx


@pytest.fixture(autouse=True)
def _isolate_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(ledger_module, "_instance", None)


def _seed(n=3, **overrides):
    """Seed ``n`` simple records into the ledger."""
    for i in range(n):
        record_tx(
            session_id=f"sess-{i}",
            user_id="user-1",
            tool_name=overrides.get("tool_name", "transfer"),
            action_args=overrides.get("action_args", {"to": f"alice{i}.eth"}),
            tx_hash=f"0x{i:064x}",
            chain_id=8453,
            from_addr="0xfrom",
            to_addr=f"0xto{i:040x}"[:42],
            value_wei="1000000000000000000",
            status=overrides.get("status", "submitted"),
        )


class TestHandleTx:
    @pytest.mark.asyncio
    async def test_empty_ledger(self):
        out = await handle_tx("")
        assert "No transactions" in out

    @pytest.mark.asyncio
    async def test_recent_default_10(self):
        _seed(15)
        out = await handle_tx("")
        # Header line should mention 10 of 15
        assert "Showing 10 of 15" in out
        # Each line includes tool name
        assert out.count("transfer") >= 10

    @pytest.mark.asyncio
    async def test_lookup_by_full_hash(self):
        _seed(3)
        out = await handle_tx(f"0x{1:064x}")
        assert f"0x{1:064x}" in out
        assert "Tool:" in out
        assert "transfer" in out

    @pytest.mark.asyncio
    async def test_lookup_by_hash_prefix(self):
        _seed(3)
        prefix = f"0x{1:064x}"[:10]
        out = await handle_tx(prefix)
        assert "Tool:" in out
        assert "transfer" in out

    @pytest.mark.asyncio
    async def test_lookup_unknown_hash(self):
        _seed(3)
        out = await handle_tx("0xdeadbeef")
        assert "No transaction found" in out


class TestHandleTxSearch:
    @pytest.mark.asyncio
    async def test_empty_query(self):
        _seed(3)
        out = await handle_tx_search("")
        assert "Usage:" in out

    @pytest.mark.asyncio
    async def test_match_by_recipient(self):
        _seed(3)
        out = await handle_tx_search("alice1.eth")
        assert "Showing 1 of 1" in out

    @pytest.mark.asyncio
    async def test_match_by_tool_name(self):
        _seed(2, tool_name="defi_swap")
        _seed(3, tool_name="transfer")  # 5 total
        out = await handle_tx_search("defi_swap")
        assert "Showing 2 of 2" in out

    @pytest.mark.asyncio
    async def test_no_matches(self):
        _seed(3)
        out = await handle_tx_search("nothing-matches")
        assert "No transactions match" in out


class TestHandleTxExport:
    @pytest.mark.asyncio
    async def test_empty(self):
        out = await handle_tx_export("")
        assert "No transactions" in out

    @pytest.mark.asyncio
    async def test_full(self):
        _seed(2)
        out = await handle_tx_export("")
        assert "Exporting 2 record(s)" in out
        # Extract JSON block and parse
        body = out.split("```json\n", 1)[1].split("\n```", 1)[0]
        data = json.loads(body)
        assert len(data) == 2
        assert data[0]["tool_name"] == "transfer"


class TestHandlePending:
    @pytest.mark.asyncio
    async def test_empty(self):
        out = await handle_pending("")
        assert "(empty)" in out

    @pytest.mark.asyncio
    async def test_with_pending(self):
        _seed(3, status="submitted")
        out = await handle_pending("")
        assert "Showing 3 of 3" in out

    @pytest.mark.asyncio
    async def test_excludes_completed(self):
        _seed(2, status="submitted")
        _seed(2, status="confirmed")  # not "submitted"
        out = await handle_pending("")
        assert "Showing 2 of 2" in out
