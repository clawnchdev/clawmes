"""Tests for the ``cost_basis`` tool."""

from __future__ import annotations

import json

import pytest

from clawmes.ledger.tx_ledger import TxRecord
from clawmes.tools.cost_basis import cost_basis


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    from clawmes.ledger import tx_ledger as tl_mod
    from clawmes.policy import storage as policy_storage

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    # Reset the ledger singleton so each test sees a fresh state
    monkeypatch.setattr(tl_mod, "_instance", None, raising=False)
    policy_storage.save_policies([])


def _record(
    tool: str,
    *,
    user: str = "default",
    token: str = "0xtoken",
    value: int = 10**18,
    price: float = 100.0,
    ts: str = "2026-01-01T00:00:00Z",
    status: str = "ok",
) -> TxRecord:
    return TxRecord(
        ts=ts,
        session_id="s",
        user_id=user,
        tool_name=tool,
        action_args={"token": token, "price_usd": price},
        value_wei=str(value),
        status=status,
    )


@pytest.fixture
def populated_ledger(monkeypatch):
    from clawmes.ledger.tx_ledger import get_ledger

    ledger = get_ledger()
    # Two buys + one partial sell of token A
    ledger.append(
        _record("defi_swap", token="USDC", value=1000, price=1.0, ts="2026-01-01T00:00:00Z")
    )
    ledger.append(
        _record("defi_swap", token="USDC", value=500, price=1.05, ts="2026-01-02T00:00:00Z")
    )
    ledger.append(
        _record("transfer", token="USDC", value=800, price=1.10, ts="2026-01-03T00:00:00Z")
    )
    return ledger


class TestSummary:
    def test_basic(self, populated_ledger):
        out = json.loads(cost_basis({"action": "summary"}))
        assert "isError" not in out
        details = out["details"]
        assert details["tokens_tracked"] == 1
        # Realized: 800 sold against the first buy (cost=1.0) at 1.10
        # → P&L = 0.10 * 800 = 80
        assert details["realized_pnl_usd"] == pytest.approx(80.0)
        # Open lots: 200 of first buy left + 500 of second buy = 2
        assert details["open_lots_count"] == 2

    def test_no_records(self):
        out = json.loads(cost_basis({"action": "summary"}))
        assert "isError" not in out
        assert out["details"]["tokens_tracked"] == 0


class TestByToken:
    def test_basic(self, populated_ledger):
        out = json.loads(cost_basis({"action": "by_token"}))
        assert "isError" not in out
        breakdown = out["details"]["breakdown"]
        assert len(breakdown) == 1
        assert breakdown[0]["token"] == "USDC"
        assert breakdown[0]["realized_pnl_usd"] == pytest.approx(80.0)

    def test_filter(self, populated_ledger):
        out = json.loads(cost_basis({"action": "by_token", "token": "USDC"}))
        assert out["details"]["count"] == 1

    def test_filter_no_match(self, populated_ledger):
        out = json.loads(cost_basis({"action": "by_token", "token": "NONEXISTENT"}))
        # Filter at fetch time → empty result
        assert out["details"]["count"] == 0


class TestRealized:
    def test_basic(self, populated_ledger):
        out = json.loads(cost_basis({"action": "realized"}))
        assert "isError" not in out
        details = out["details"]
        # The 800-sell is one realized lot
        assert details["realized_count"] == 1
        assert details["total_pnl_usd"] == pytest.approx(80.0)


class TestUnrealized:
    def test_basic(self, populated_ledger):
        out = json.loads(cost_basis({"action": "unrealized"}))
        assert "isError" not in out
        details = out["details"]
        # 200 left from buy1 + 500 from buy2 = 2 lots
        assert details["open_lot_count"] == 2
        # Cost basis: 200*1.0 + 500*1.05 = 200 + 525 = 725
        assert details["total_cost_basis_usd"] == pytest.approx(725.0)


class TestExport:
    def test_basic(self, populated_ledger):
        out = json.loads(cost_basis({"action": "export"}))
        assert "isError" not in out
        rows = out["details"]["rows"]
        # 1 realized + 2 open = 3 rows
        assert len(rows) == 3
        types = {r["type"] for r in rows}
        assert types == {"realized", "open"}


class TestUserScoping:
    def test_user_filter(self, monkeypatch, populated_ledger):
        from clawmes.ledger.tx_ledger import get_ledger

        # Add an alice record on top of the default-user records
        get_ledger().append(_record("defi_swap", user="alice", token="DAI", value=100, price=1.0))
        # Query alice — only DAI shows
        out = json.loads(cost_basis({"action": "summary", "user_id": "alice"}))
        assert out["details"]["tokens_tracked"] == 1
        assert "DAI" in out["details"]["tokens"]


class TestEdgeCases:
    def test_zero_value_skipped(self, monkeypatch):
        from clawmes.ledger.tx_ledger import get_ledger

        get_ledger().append(_record("defi_swap", value=0))
        out = json.loads(cost_basis({"action": "summary"}))
        assert out["details"]["tokens_tracked"] == 0

    def test_unknown_tool_skipped(self, monkeypatch):
        from clawmes.ledger.tx_ledger import get_ledger

        # A record with a non-buy/sell tool — should not affect lots
        get_ledger().append(_record("nft", token="bored-ape", value=1000))
        out = json.loads(cost_basis({"action": "summary"}))
        # Token registered (nonzero value) but tool=nft is neither
        # buy nor sell — bucket has 0 buys and 0 sells, but the token
        # was first_seen so it's tracked.
        assert "bored-ape" in out["details"]["tokens"]

    def test_native_transfer(self, monkeypatch):
        from clawmes.ledger.tx_ledger import get_ledger

        # Native transfer — no token arg, value present → "native" sentinel
        rec = TxRecord(
            ts="2026-01-01T00:00:00Z",
            session_id="s",
            user_id="default",
            tool_name="transfer",
            action_args={"price_usd": 3500.0},
            value_wei=str(10**18),
            status="ok",
        )
        get_ledger().append(rec)
        out = json.loads(cost_basis({"action": "summary"}))
        assert "native" in out["details"]["tokens"]

    def test_negative_amount_safe(self, monkeypatch):
        from clawmes.ledger.tx_ledger import get_ledger

        get_ledger().append(_record("defi_swap", value=-500))
        out = json.loads(cost_basis({"action": "summary"}))
        assert out["details"]["tokens_tracked"] == 0

    def test_malformed_value(self, monkeypatch):
        from clawmes.ledger.tx_ledger import get_ledger

        rec = TxRecord(
            ts="t",
            session_id="s",
            user_id="default",
            tool_name="defi_swap",
            action_args={"token": "X"},
            value_wei="not-a-number",
            status="ok",
        )
        get_ledger().append(rec)
        out = json.loads(cost_basis({"action": "summary"}))
        # Bad value → 0 → skipped
        assert out["details"]["tokens_tracked"] == 0

    def test_record_with_no_token_no_value_skipped(self, monkeypatch):
        from clawmes.ledger.tx_ledger import get_ledger

        # No token in args AND no value_wei → _extract_token returns None
        rec = TxRecord(
            ts="t",
            session_id="s",
            user_id="default",
            tool_name="defi_swap",
            action_args={},
            value_wei=None,
            status="ok",
        )
        get_ledger().append(rec)
        out = json.loads(cost_basis({"action": "summary"}))
        assert out["details"]["tokens_tracked"] == 0

    def test_exact_lot_match_pops_buy(self, monkeypatch):
        # Buy 1000 then sell 1000 — exact match → popleft branch
        from clawmes.ledger.tx_ledger import get_ledger

        ledger = get_ledger()
        ledger.append(_record("defi_swap", token="X", value=1000, price=1.0))
        ledger.append(_record("transfer", token="X", value=1000, price=1.5))
        out = json.loads(cost_basis({"action": "summary"}))
        details = out["details"]
        # Realized P&L: (1.5 - 1.0) * 1000 = 500
        assert details["realized_pnl_usd"] == pytest.approx(500.0)
        # No open lots — buy was fully consumed
        assert details["open_lots_count"] == 0

    def test_malformed_price_safe(self, monkeypatch):
        from clawmes.ledger.tx_ledger import get_ledger

        rec = TxRecord(
            ts="t",
            session_id="s",
            user_id="default",
            tool_name="defi_swap",
            action_args={"token": "X", "price_usd": "garbage"},
            value_wei=str(1000),
            status="ok",
        )
        get_ledger().append(rec)
        out = json.loads(cost_basis({"action": "summary"}))
        # Malformed price → 0 → still tracked
        assert "X" in out["details"]["tokens"]


class TestRegister:
    def test_register_calls_ctx(self):
        from clawmes.tools import cost_basis as cb_mod

        recorded = []

        class FakeCtx:
            def register_tool(self, **kw):
                recorded.append(kw)

        cb_mod.register(FakeCtx())
        assert len(recorded) == 1
        assert recorded[0]["name"] == "cost_basis"
