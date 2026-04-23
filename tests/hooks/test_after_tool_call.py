"""Tests for the post_tool_call hook — primary side effect is ledger append."""

from __future__ import annotations

import json

import pytest

from clawmes.hooks.after_tool_call import callback
from clawmes.ledger import tx_ledger as ledger_module
from clawmes.ledger.tx_ledger import get_ledger
from clawmes.lib.tool_result import error_result, json_result, text_result


@pytest.fixture(autouse=True)
def _isolate_ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(ledger_module, "_instance", None)


@pytest.fixture(autouse=True)
def _make_transfer_a_write_tool():
    """Ensure ``transfer`` is in WRITE_TOOL_NAMES.

    Importing ``clawmes.tools.transfer`` runs the ``@write_tool`` decorator
    at import time, which adds ``transfer`` to the set. Tests that hit
    the hook directly (without going through register()) need to make
    sure that import has happened.
    """
    from clawmes.tools import transfer  # noqa: F401  — side effect import


class TestWriteToolRecordedOnSuccess:
    def test_basic_record(self):
        result = json_result(
            {"tx_hash": "0xabc123", "chain_id": 8453, "to_addr": "0xdef"},
            summary="Sent 0.5 ETH",
        )
        callback(
            tool_name="transfer",
            args={"to": "alice.eth", "amount": "0.5"},
            result=result,
            duration_ms=120.5,
            session_id="sess-1",
            user_id="user-1",
        )
        records = get_ledger().iter_records()
        assert len(records) == 1
        rec = records[0]
        assert rec.tool_name == "transfer"
        assert rec.tx_hash == "0xabc123"
        assert rec.chain_id == 8453
        assert rec.session_id == "sess-1"
        assert rec.user_id == "user-1"
        assert rec.action_args == {"to": "alice.eth", "amount": "0.5"}

    def test_redacts_policy_nonce(self):
        result = json_result({"tx_hash": "0xabc"})
        callback(
            tool_name="transfer",
            args={
                "to": "alice.eth",
                "amount": "0.5",
                "policyConfirmationNonce": "secret-nonce",
            },
            result=result,
            session_id="s",
            user_id="u",
        )
        rec = get_ledger().iter_records()[0]
        assert "policyConfirmationNonce" not in rec.action_args
        assert rec.action_args["to"] == "alice.eth"

    def test_resolved_address_used_when_to_addr_missing(self):
        result = json_result(
            {"tx_hash": "0xabc", "resolved_address": "0xdeadbeef"},
        )
        callback(
            tool_name="transfer",
            args={"to": "alice.eth", "amount": "1"},
            result=result,
            session_id="s",
            user_id="u",
        )
        rec = get_ledger().iter_records()[0]
        assert rec.to_addr == "0xdeadbeef"


class TestSkippedRecords:
    def test_read_tool_not_recorded(self):
        result = json_result({"price": 3500}, summary="ETH = 3500")
        callback(
            tool_name="defi_price",  # NOT in WRITE_TOOL_NAMES
            args={"symbol": "ETH"},
            result=result,
            session_id="s",
            user_id="u",
        )
        assert get_ledger().iter_records() == []

    def test_failed_tool_call_not_recorded(self):
        callback(
            tool_name="transfer",
            args={"to": "alice.eth"},
            result=None,
            error=RuntimeError("network down"),
            session_id="s",
            user_id="u",
        )
        assert get_ledger().iter_records() == []

    def test_error_envelope_not_recorded(self):
        # Tool returned an error envelope (e.g. policy_block). Even
        # though no exception was raised, the ledger should skip it.
        result = error_result("Blocked by policy: foo", code="policy_block")
        callback(
            tool_name="transfer",
            args={"to": "alice.eth"},
            result=result,
            session_id="s",
            user_id="u",
        )
        assert get_ledger().iter_records() == []

    def test_unparseable_result_not_recorded(self):
        callback(
            tool_name="transfer",
            args={"to": "alice.eth"},
            result="not-json-at-all",
            session_id="s",
            user_id="u",
        )
        assert get_ledger().iter_records() == []

    def test_none_result_not_recorded(self):
        # No exception, but result is None — _parse_result returns None
        callback(
            tool_name="transfer",
            args={"to": "alice.eth"},
            result=None,
            session_id="s",
            user_id="u",
        )
        assert get_ledger().iter_records() == []

    def test_empty_string_result_not_recorded(self):
        callback(
            tool_name="transfer",
            args={"to": "alice.eth"},
            result="",
            session_id="s",
            user_id="u",
        )
        assert get_ledger().iter_records() == []

    def test_non_dict_result_not_recorded(self):
        # Valid JSON, but not a dict (parses to list / string / number)
        callback(
            tool_name="transfer",
            args={"to": "alice.eth"},
            result='["this", "is", "a", "list"]',
            session_id="s",
            user_id="u",
        )
        assert get_ledger().iter_records() == []

    def test_text_only_result_records_with_no_details(self):
        # text_result has no `details` key; ledger should still append
        # using the args, just with tx_hash/chain_id None.
        result = text_result("Sent it")
        callback(
            tool_name="transfer",
            args={"to": "alice.eth"},
            result=result,
            session_id="s",
            user_id="u",
        )
        records = get_ledger().iter_records()
        assert len(records) == 1
        assert records[0].tx_hash is None


class TestRobustness:
    def test_ledger_disk_failure_does_not_propagate(self, monkeypatch):
        """If ledger.append blows up, the hook must swallow it."""
        from clawmes.hooks import after_tool_call as hook_module

        def boom(*a, **kw):
            raise OSError("disk full")

        # Patch the reference cached at import time inside the hook module
        monkeypatch.setattr(hook_module, "record_tx", boom)
        # Must not raise
        callback(
            tool_name="transfer",
            args={"to": "alice.eth"},
            result=json_result({"tx_hash": "0xabc"}),
            session_id="s",
            user_id="u",
        )

    def test_missing_session_user_falls_through(self):
        """Hook should still record even when Hermes provides no session/user kwargs."""
        callback(
            tool_name="transfer",
            args={"to": "alice.eth"},
            result=json_result({"tx_hash": "0xabc"}),
        )
        rec = get_ledger().iter_records()[0]
        assert rec.user_id == "default"
        assert rec.session_id == ""

    def test_session_key_falls_back_to_session_id(self):
        """Hermes uses ``session_key`` in some contexts; we accept both."""
        callback(
            tool_name="transfer",
            args={},
            result=json_result({"tx_hash": "0xabc"}),
            session_key="sess-xyz",
        )
        rec = get_ledger().iter_records()[0]
        assert rec.session_id == "sess-xyz"


class TestNonDictDetails:
    def test_list_details_treated_as_empty(self):
        # Tool returned details as a list — we coerce to {} silently
        result = json.dumps(
            {
                "content": [{"type": "text", "text": "x"}],
                "details": ["not", "a", "dict"],
            }
        )
        callback(
            tool_name="transfer",
            args={"to": "alice.eth"},
            result=result,
            session_id="s",
            user_id="u",
        )
        records = get_ledger().iter_records()
        assert len(records) == 1
        assert records[0].tx_hash is None
        assert records[0].chain_id is None
