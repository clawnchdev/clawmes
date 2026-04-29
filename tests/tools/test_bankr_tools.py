"""Tests for the four Bankr-tier tools.

bankr_launch, bankr_automate, bankr_polymarket, bankr_leverage are
all thin wrappers around the Bankr API. Their tests share a common
fixture pattern: stub BankrService.request to return canned responses.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from clawmes.tools.bankr_automate import bankr_automate
from clawmes.tools.bankr_launch import bankr_launch
from clawmes.tools.bankr_leverage import bankr_leverage
from clawmes.tools.bankr_polymarket import bankr_polymarket


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    from clawmes.policy import storage as policy_storage
    from clawmes.services import bankr_service as bs_mod

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(bs_mod, "_instance", None)
    policy_storage.save_policies([])


@pytest.fixture
def fake_bankr(monkeypatch):
    from clawmes.services import bankr_service as bs_mod

    svc = MagicMock()
    svc.request.return_value = {"ok": True}
    monkeypatch.setattr(bs_mod, "_instance", svc)
    return svc


class TestBankrLaunch:
    def test_deploy(self, fake_bankr):
        out = json.loads(
            bankr_launch(
                {
                    "action": "deploy",
                    "name": "Test",
                    "symbol": "TST",
                    "supply": "1000000",
                }
            )
        )
        assert "isError" not in out
        # Verify service called with the right path + body
        args = fake_bankr.request.call_args
        assert args.args == ("POST", "/v1/launch/deploy")
        assert args.kwargs["body"]["name"] == "Test"
        assert args.kwargs["body"]["chain"] == "base"

    def test_pair(self, fake_bankr):
        out = json.loads(bankr_launch({"action": "pair", "token": "0x" + "a" * 40}))
        assert "isError" not in out

    def test_info(self, fake_bankr):
        out = json.loads(
            bankr_launch(
                {
                    "action": "info",
                    "token": "0x" + "a" * 40,
                    "chain": "solana",
                }
            )
        )
        assert "isError" not in out
        # GET request
        args = fake_bankr.request.call_args
        assert args.args[0] == "GET"

    def test_bankr_error_propagates(self, fake_bankr):
        from clawmes.services.bankr_service import BankrError

        fake_bankr.request.side_effect = BankrError("no_credentials", "BANKR_API_KEY not set")
        out = json.loads(
            bankr_launch(
                {
                    "action": "deploy",
                    "name": "T",
                    "symbol": "T",
                    "supply": "1",
                }
            )
        )
        assert out["isError"] is True
        assert out["details"]["error_code"] == "no_credentials"


class TestBankrAutomate:
    def test_create(self, fake_bankr):
        out = json.loads(bankr_automate({"action": "create", "payload": {"rule_type": "dca"}}))
        assert "isError" not in out

    def test_create_missing_payload(self, fake_bankr):
        out = json.loads(bankr_automate({"action": "create"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "param_error"

    def test_list(self, fake_bankr):
        out = json.loads(bankr_automate({"action": "list"}))
        assert "isError" not in out
        args = fake_bankr.request.call_args
        assert args.args == ("GET", "/v1/automate/list")

    def test_pause(self, fake_bankr):
        out = json.loads(bankr_automate({"action": "pause", "rule_id": "abc-123"}))
        assert "isError" not in out
        args = fake_bankr.request.call_args
        assert args.args[1] == "/v1/automate/pause/abc-123"

    def test_pause_missing_rule_id(self, fake_bankr):
        out = json.loads(bankr_automate({"action": "pause"}))
        assert out["isError"] is True

    def test_bankr_error(self, fake_bankr):
        from clawmes.services.bankr_service import BankrError

        fake_bankr.request.side_effect = BankrError("network", "down")
        out = json.loads(bankr_automate({"action": "list"}))
        assert out["isError"] is True


class TestBankrPolymarket:
    def test_markets(self, fake_bankr):
        out = json.loads(bankr_polymarket({"action": "markets"}))
        assert "isError" not in out

    def test_positions(self, fake_bankr):
        out = json.loads(bankr_polymarket({"action": "positions"}))
        assert "isError" not in out

    def test_bet(self, fake_bankr):
        out = json.loads(
            bankr_polymarket(
                {
                    "action": "bet",
                    "payload": {
                        "market_id": "m-123",
                        "outcome": "yes",
                        "amount": "100",
                    },
                }
            )
        )
        assert "isError" not in out

    def test_bet_missing_payload(self, fake_bankr):
        out = json.loads(bankr_polymarket({"action": "bet"}))
        assert out["isError"] is True

    def test_sell(self, fake_bankr):
        out = json.loads(bankr_polymarket({"action": "sell", "payload": {"position_id": "p-1"}}))
        assert "isError" not in out

    def test_claim(self, fake_bankr):
        out = json.loads(bankr_polymarket({"action": "claim", "payload": {"position_id": "p-1"}}))
        assert "isError" not in out

    def test_bankr_error(self, fake_bankr):
        from clawmes.services.bankr_service import BankrError

        fake_bankr.request.side_effect = BankrError("api_error", "boom")
        out = json.loads(bankr_polymarket({"action": "markets"}))
        assert out["isError"] is True


class TestBankrLeverage:
    def test_open(self, fake_bankr):
        out = json.loads(
            bankr_leverage(
                {
                    "action": "open",
                    "payload": {
                        "market": "ETH-PERP",
                        "direction": "long",
                        "size": "1000",
                        "leverage": 5,
                    },
                }
            )
        )
        assert "isError" not in out

    def test_open_missing_payload(self, fake_bankr):
        out = json.loads(bankr_leverage({"action": "open"}))
        assert out["isError"] is True

    def test_positions(self, fake_bankr):
        out = json.loads(bankr_leverage({"action": "positions"}))
        assert "isError" not in out

    def test_funding(self, fake_bankr):
        out = json.loads(bankr_leverage({"action": "funding", "payload": {"market": "ETH-PERP"}}))
        assert "isError" not in out

    def test_funding_missing_market(self, fake_bankr):
        out = json.loads(bankr_leverage({"action": "funding"}))
        assert out["isError"] is True

    def test_funding_payload_not_dict(self, fake_bankr):
        out = json.loads(bankr_leverage({"action": "funding", "payload": "not-a-dict"}))
        assert out["isError"] is True

    def test_close(self, fake_bankr):
        out = json.loads(bankr_leverage({"action": "close", "payload": {"position_id": "p-1"}}))
        assert "isError" not in out

    def test_adjust(self, fake_bankr):
        out = json.loads(
            bankr_leverage(
                {
                    "action": "adjust",
                    "payload": {"position_id": "p-1", "leverage": 3},
                }
            )
        )
        assert "isError" not in out

    def test_close_missing_payload(self, fake_bankr):
        out = json.loads(bankr_leverage({"action": "close"}))
        assert out["isError"] is True

    def test_bankr_error(self, fake_bankr):
        from clawmes.services.bankr_service import BankrError

        fake_bankr.request.side_effect = BankrError("rate_limited", "too many")
        out = json.loads(bankr_leverage({"action": "positions"}))
        assert out["isError"] is True


class TestBankrServiceRequest:
    def test_unsupported_method(self, monkeypatch):
        from clawmes.services import bankr_service as bs_mod
        from clawmes.services.bankr_service import BankrError, BankrService

        monkeypatch.setenv("BANKR_API_KEY", "k")
        svc = BankrService()
        svc.start()
        monkeypatch.setattr(bs_mod, "_instance", svc)
        with pytest.raises(BankrError) as exc_info:
            svc.request("DELETE", "/v1/x")
        assert "unsupported method" in exc_info.value.message

    def test_request_get_routing(self, monkeypatch, fake_bankr):
        # Verifies that request dispatches to the underlying _get/_post
        from clawmes.services import bankr_service as bs_mod
        from clawmes.services.bankr_service import BankrService

        monkeypatch.setenv("BANKR_API_KEY", "k")
        svc = BankrService()
        svc.start()
        monkeypatch.setattr(bs_mod, "_instance", svc)

        # Stub _get + _post so we can verify routing without HTTP
        called = {}

        def fake_get(path):
            called["get"] = path
            return {"ok": "get"}

        def fake_post(path, body):
            called["post"] = (path, body)
            return {"ok": "post"}

        monkeypatch.setattr(svc, "_get", fake_get)
        monkeypatch.setattr(svc, "_post", fake_post)
        assert svc.request("GET", "/v1/x") == {"ok": "get"}
        assert svc.request("POST", "/v1/x", body={"a": 1}) == {"ok": "post"}
        # POST without body uses empty dict
        svc.request("POST", "/v1/y")
        assert called["post"] == ("/v1/y", {})


class TestRegister:
    def test_bankr_launch_register(self):
        from clawmes.tools import bankr_launch as bl

        recorded = []

        class FakeCtx:
            def register_tool(self, **kw):
                recorded.append(kw)

        bl.register(FakeCtx())
        assert recorded[0]["name"] == "bankr_launch"

    def test_bankr_automate_register(self):
        from clawmes.tools import bankr_automate as ba

        recorded = []

        class FakeCtx:
            def register_tool(self, **kw):
                recorded.append(kw)

        ba.register(FakeCtx())
        assert recorded[0]["name"] == "bankr_automate"

    def test_bankr_polymarket_register(self):
        from clawmes.tools import bankr_polymarket as bp

        recorded = []

        class FakeCtx:
            def register_tool(self, **kw):
                recorded.append(kw)

        bp.register(FakeCtx())
        assert recorded[0]["name"] == "bankr_polymarket"

    def test_bankr_leverage_register(self):
        from clawmes.tools import bankr_leverage as bl

        recorded = []

        class FakeCtx:
            def register_tool(self, **kw):
                recorded.append(kw)

        bl.register(FakeCtx())
        assert recorded[0]["name"] == "bankr_leverage"
