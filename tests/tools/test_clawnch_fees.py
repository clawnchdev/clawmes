"""Tests for clawmes.tools.clawnch_fees."""

from __future__ import annotations

import json

import pytest

from clawmes.services import clawnch as cl_mod
from clawmes.services.clawnch import ClawnchError
from clawmes.tools.clawnch_fees import clawnch_fees, register


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(cl_mod, "_instance", None)


class _FakeSvc:
    def __init__(self):
        self.my_launches_return: dict = {"launches": []}
        self.launch_return: dict = {"name": "X"}
        self.raise_on: Exception | None = None
        self.rh_launches_return: dict = {
            "ok": True,
            "chain": "robinhood",
            "launches": [],
            "pagination": {"limit": 50, "offset": 0, "total": 0, "hasMore": False},
        }
        self.rh_launches_calls: list[dict] = []
        self.rh_claimable_return: dict = {
            "ok": True,
            "chainId": 4663,
            "token": "0x" + "a" * 40,
            "address": "0x" + "1" * 40,
            "feeShare": "0x" + "e" * 40,
            "isClaimer": True,
            "bps": 4000,
            "claimableWei": "1000000000000000",
            "claim": {"to": "0x" + "e" * 40, "data": "0x1", "value": "0x0", "chainId": 4663},
        }
        self.rh_claimable_calls: list[dict] = []
        self.rh_claim_return: dict = {
            "ok": True,
            "ready": True,
            "claimableWei": "1000000000000000",
            "claim": {"to": "0x" + "e" * 40, "data": "0x1", "value": "0x0", "chainId": 4663},
        }
        self.rh_claim_calls: list[str] = []

    def get_my_launches(self):
        if self.raise_on:
            raise self.raise_on
        return self.my_launches_return

    def get_launch(self, token):
        if self.raise_on:
            raise self.raise_on
        return self.launch_return

    def rh_launches(self, *, agent=None, limit=50, offset=0):
        self.rh_launches_calls.append({"agent": agent, "limit": limit, "offset": offset})
        if self.raise_on:
            raise self.raise_on
        return self.rh_launches_return

    def rh_claimable(self, *, token, address):
        self.rh_claimable_calls.append({"token": token, "address": address})
        if self.raise_on:
            raise self.raise_on
        return self.rh_claimable_return

    def rh_claim(self, *, token):
        self.rh_claim_calls.append(token)
        if self.raise_on:
            raise self.raise_on
        return self.rh_claim_return


@pytest.fixture
def fake_svc(monkeypatch):
    s = _FakeSvc()
    monkeypatch.setattr(
        "clawmes.services.clawnch.get_clawnch_service",
        lambda: s,
    )
    return s


class TestMyLaunches:
    def test_empty(self, fake_svc):
        out = json.loads(clawnch_fees({"action": "my_launches"}))
        assert "No launches" in out["content"][0]["text"]

    def test_with_launches(self, fake_svc):
        fake_svc.my_launches_return = {"launches": [{"id": 1}, {"id": 2}]}
        out = json.loads(clawnch_fees({"action": "my_launches"}))
        assert "2 launch" in out["content"][0]["text"]

    def test_with_tokens_key(self, fake_svc):
        # Some endpoints return `tokens` instead of `launches`
        fake_svc.my_launches_return = {"tokens": [{"id": 1}]}
        out = json.loads(clawnch_fees({"action": "my_launches"}))
        assert "1 launch" in out["content"][0]["text"]

    def test_non_list_value_default_summary(self, fake_svc):
        # Defensive: if upstream returns malformed data
        fake_svc.my_launches_return = {"launches": "not a list"}
        out = json.loads(clawnch_fees({"action": "my_launches"}))
        assert "retrieved" in out["content"][0]["text"]

    def test_clawnch_error(self, fake_svc):
        fake_svc.raise_on = ClawnchError("no_credentials", "no key")
        out = json.loads(clawnch_fees({"action": "my_launches"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "no_credentials"

    def test_unexpected_error(self, fake_svc):
        fake_svc.raise_on = RuntimeError("network")
        out = json.loads(clawnch_fees({"action": "my_launches"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "api_error"


class TestLaunchInfo:
    def test_requires_token(self, fake_svc):
        out = json.loads(clawnch_fees({"action": "launch_info"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "param_error"

    def test_basic(self, fake_svc):
        out = json.loads(clawnch_fees({"action": "launch_info", "token": "0xabc"}))
        assert out["details"] == {"name": "X"}

    def test_clawnch_error(self, fake_svc):
        fake_svc.raise_on = ClawnchError("not_found", "no token")
        out = json.loads(clawnch_fees({"action": "launch_info", "token": "0xabc"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "not_found"


class TestRegister:
    def test_registers_one_tool(self):
        captured = []

        class FakeCtx:
            def register_tool(self, **kw):
                captured.append(kw["name"])

        register(FakeCtx())
        assert captured == ["clawnch_fees"]


# ──────────────────────────────────────────────────────────────────────
#  Robinhood Chain actions
# ──────────────────────────────────────────────────────────────────────

_ADDR = "0x" + "a" * 40
_WALLET = "0x" + "1" * 40


class TestRHLaunches:
    def test_empty_feed(self, fake_svc):
        out = json.loads(clawnch_fees({"action": "rh_launches"}))
        assert "0 Robinhood Chain launch" in out["content"][0]["text"]

    def test_with_rows_and_total(self, fake_svc):
        fake_svc.rh_launches_return = {
            "ok": True,
            "launches": [{"token": _ADDR}, {"token": _ADDR}],
            "pagination": {"limit": 50, "offset": 0, "total": 9, "hasMore": False},
        }
        out = json.loads(clawnch_fees({"action": "rh_launches"}))
        assert "2 Robinhood Chain launch(es)" in out["content"][0]["text"]
        assert "of 9" in out["content"][0]["text"]
        assert fake_svc.rh_launches_calls[0] == {"agent": None, "limit": 50, "offset": 0}

    def test_agent_filter_and_pagination(self, fake_svc):
        out = json.loads(
            clawnch_fees({"action": "rh_launches", "agent": _WALLET, "limit": 5, "offset": 10})
        )
        assert fake_svc.rh_launches_calls[0] == {"agent": _WALLET, "limit": 5, "offset": 10}
        assert _WALLET in out["content"][0]["text"]

    def test_clawnch_error(self, fake_svc):
        fake_svc.raise_on = ClawnchError("rate_limited", "slow down")
        out = json.loads(clawnch_fees({"action": "rh_launches"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "rate_limited"


class TestRHClaimable:
    def test_requires_token(self, fake_svc):
        out = json.loads(clawnch_fees({"action": "rh_claimable"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "param_error"

    def test_requires_address(self, fake_svc):
        out = json.loads(clawnch_fees({"action": "rh_claimable", "token": _ADDR}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "param_error"

    def test_claimer_summary(self, fake_svc):
        out = json.loads(
            clawnch_fees({"action": "rh_claimable", "token": _ADDR, "address": _WALLET})
        )
        assert fake_svc.rh_claimable_calls[0] == {"token": _ADDR, "address": _WALLET}
        assert "0.001000 ETH" in out["content"][0]["text"]
        assert "4000 bps" in out["content"][0]["text"]

    def test_not_claimer_summary(self, fake_svc):
        fake_svc.rh_claimable_return = dict(fake_svc.rh_claimable_return, isClaimer=False)
        out = json.loads(
            clawnch_fees({"action": "rh_claimable", "token": _ADDR, "address": _WALLET})
        )
        assert "not a fee claimer" in out["content"][0]["text"]

    def test_bad_claimable_value_is_tolerated(self, fake_svc):
        fake_svc.rh_claimable_return = dict(
            fake_svc.rh_claimable_return, claimableWei="not-a-number"
        )
        out = json.loads(
            clawnch_fees({"action": "rh_claimable", "token": _ADDR, "address": _WALLET})
        )
        assert "isError" not in out

    def test_clawnch_error(self, fake_svc):
        fake_svc.raise_on = ClawnchError("no_fee_share", "not a Bags token")
        out = json.loads(
            clawnch_fees({"action": "rh_claimable", "token": _ADDR, "address": _WALLET})
        )
        assert out["isError"] is True
        assert out["details"]["error_code"] == "no_fee_share"


class TestRHClaim:
    def test_requires_token(self, fake_svc):
        out = json.loads(clawnch_fees({"action": "rh_claim"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "param_error"

    def test_ready_claim(self, fake_svc):
        out = json.loads(clawnch_fees({"action": "rh_claim", "token": _ADDR}))
        assert fake_svc.rh_claim_calls == [_ADDR]
        assert "Unsigned claim tx ready" in out["content"][0]["text"]
        assert out["details"]["claim"]["chainId"] == 4663

    def test_nothing_claimable(self, fake_svc):
        fake_svc.rh_claim_return = {
            "ok": True,
            "ready": False,
            "claimableWei": "0",
            "claim": None,
            "note": "re-check after more trading",
        }
        out = json.loads(clawnch_fees({"action": "rh_claim", "token": _ADDR}))
        assert "isError" not in out
        assert "Nothing claimable" in out["content"][0]["text"]
        assert "re-check" in out["content"][0]["text"]

    def test_not_claimer_error(self, fake_svc):
        fake_svc.raise_on = ClawnchError("not_claimer", "not a claimer")
        out = json.loads(clawnch_fees({"action": "rh_claim", "token": _ADDR}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "not_claimer"


class TestSchema:
    def test_rhc_actions_advertised(self):
        from clawmes.tools.clawnch_fees import _SCHEMA

        enum = _SCHEMA["properties"]["action"]["enum"]
        assert {"rh_launches", "rh_claimable", "rh_claim"} <= set(enum)
        for key in ("token", "address", "agent", "limit", "offset"):
            assert key in _SCHEMA["properties"]
