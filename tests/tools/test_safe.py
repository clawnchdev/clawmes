"""Tests for the ``safe`` tool + service."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from clawmes.tools.safe import safe
from clawmes.wallet.state import WalletState

OWNER = "0x" + "a" * 40
SAFE_ADDR = "0x" + "b" * 40


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    from clawmes.policy import storage as policy_storage
    from clawmes.services import wallet as wallet_mod

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(wallet_mod, "_instance", None)
    policy_storage.save_policies([])


@pytest.fixture
def connected(monkeypatch):
    state = WalletState.for_chain(mode="local", address=OWNER, chain_id=1)
    monkeypatch.setattr("clawmes.tools.safe.get_wallet_state", lambda: state)
    return state


@pytest.fixture
def fake_get(monkeypatch):
    class FakeHttp:
        def __init__(self):
            self.calls: list[dict] = []
            self.responses: list = []

        def __call__(self, url, *, params=None, headers=None, timeout=30.0, **kw):
            self.calls.append({"url": url, "params": params})
            if not self.responses:
                raise AssertionError("no fake response queued")
            response = self.responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response

    fake = FakeHttp()
    monkeypatch.setattr("clawmes.services.safe.http_get", fake)
    return fake


@pytest.fixture
def fake_post(monkeypatch):
    class FakeHttp:
        def __init__(self):
            self.calls: list[dict] = []
            self.responses: list = []

        def __call__(self, url, *, json=None, headers=None, timeout=30.0, **kw):
            self.calls.append({"url": url, "json": json})
            if not self.responses:
                raise AssertionError("no fake response queued")
            response = self.responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response

    fake = FakeHttp()
    monkeypatch.setattr("clawmes.services.safe.http_post", fake)
    return fake


class TestInfo:
    def test_basic(self, connected, fake_get):
        fake_get.responses.append(
            {
                "address": SAFE_ADDR,
                "owners": [OWNER, "0x" + "1" * 40],
                "threshold": 2,
                "nonce": 5,
                "version": "1.4.1",
            }
        )
        out = json.loads(safe({"action": "info", "safe_address": SAFE_ADDR}))
        assert "isError" not in out
        details = out["details"]
        assert details["threshold"] == 2
        assert len(details["owners"]) == 2

    def test_safe_not_found(self, connected, fake_get):
        fake_get.responses.append(RuntimeError("HTTP 404 not found"))
        out = json.loads(safe({"action": "info", "safe_address": SAFE_ADDR}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "not_found"

    def test_api_error(self, connected, fake_get):
        fake_get.responses.append(RuntimeError("rate limited"))
        out = json.loads(safe({"action": "info", "safe_address": SAFE_ADDR}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "api_error"

    def test_non_dict_response(self, connected, fake_get):
        fake_get.responses.append("not a dict")
        out = json.loads(safe({"action": "info", "safe_address": SAFE_ADDR}))
        assert out["isError"] is True


class TestPending:
    def test_basic(self, connected, fake_get):
        fake_get.responses.append(
            {
                "results": [
                    {
                        "safeTxHash": "0x" + "f" * 64,
                        "to": "0x" + "1" * 40,
                        "value": "0",
                        "nonce": 5,
                        "confirmations": [{}, {}],  # 2 sigs
                        "confirmationsRequired": 3,
                        "submissionDate": "2026-01-01",
                    }
                ]
            }
        )
        out = json.loads(safe({"action": "pending", "safe_address": SAFE_ADDR}))
        assert "isError" not in out
        details = out["details"]
        assert details["count"] == 1
        assert details["pending"][0]["confirmations_count"] == 2

    def test_filters_non_dict_entries(self, connected, fake_get):
        fake_get.responses.append({"results": ["not-a-dict", {"safeTxHash": "0x1"}]})
        out = json.loads(safe({"action": "pending", "safe_address": SAFE_ADDR}))
        assert out["details"]["count"] == 1

    def test_empty_results(self, connected, fake_get):
        fake_get.responses.append({"results": []})
        out = json.loads(safe({"action": "pending", "safe_address": SAFE_ADDR}))
        assert out["details"]["count"] == 0


class TestPropose:
    def test_basic(self, connected, fake_post):
        fake_post.responses.append({"safe_tx_hash": "0x" + "f" * 64})
        out = json.loads(
            safe(
                {
                    "action": "propose",
                    "safe_address": SAFE_ADDR,
                    "payload": {
                        "to": "0x" + "1" * 40,
                        "value": "0",
                        "data": "0x",
                        "operation": 0,
                        "safeTxGas": 0,
                        "baseGas": 0,
                        "gasPrice": "0",
                        "gasToken": "0x" + "0" * 40,
                        "refundReceiver": "0x" + "0" * 40,
                        "nonce": 5,
                        "contractTransactionHash": "0x" + "a" * 64,
                        "sender": OWNER,
                        "signature": "0x" + "b" * 130,
                    },
                }
            )
        )
        assert "isError" not in out

    def test_missing_payload(self, connected, fake_post):
        out = json.loads(safe({"action": "propose", "safe_address": SAFE_ADDR}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "param_error"

    def test_payload_not_dict(self, connected, fake_post):
        out = json.loads(
            safe(
                {
                    "action": "propose",
                    "safe_address": SAFE_ADDR,
                    "payload": "not a dict",
                }
            )
        )
        assert out["isError"] is True
        assert out["details"]["error_code"] == "param_error"

    def test_post_error(self, connected, fake_post):
        fake_post.responses.append(RuntimeError("network"))
        out = json.loads(
            safe(
                {
                    "action": "propose",
                    "safe_address": SAFE_ADDR,
                    "payload": {"x": 1},
                }
            )
        )
        assert out["isError"] is True
        assert out["details"]["error_code"] == "api_error"

    def test_confirm_alias(self, connected, fake_post):
        fake_post.responses.append({"submitted": True})
        out = json.loads(
            safe(
                {
                    "action": "confirm",
                    "safe_address": SAFE_ADDR,
                    "payload": {"x": 1},
                }
            )
        )
        assert "isError" not in out


class TestExecute:
    def test_not_implemented(self, connected):
        out = json.loads(safe({"action": "execute", "safe_address": SAFE_ADDR}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "not_implemented"


class TestValidation:
    def test_invalid_address(self, connected):
        out = json.loads(safe({"action": "info", "safe_address": "0xshort"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "param_error"

    def test_unsupported_chain(self, connected):
        out = json.loads(
            safe(
                {
                    "action": "info",
                    "safe_address": SAFE_ADDR,
                    "chain_id": 56,  # BSC not supported
                }
            )
        )
        assert out["isError"] is True
        assert out["details"]["error_code"] == "unsupported_chain"

    def test_chain_id_falls_back(self, monkeypatch, fake_get):
        state = WalletState(connected=True, mode="local", address=OWNER, chain_id=None)
        monkeypatch.setattr("clawmes.tools.safe.get_wallet_state", lambda: state)
        fake_get.responses.append({"owners": [], "threshold": 1, "nonce": 0})
        out = json.loads(safe({"action": "info", "safe_address": SAFE_ADDR}))
        assert "isError" not in out


class TestService:
    def test_propose_non_dict_response_returns_submitted(self, connected, fake_post):
        # Some Safe Service endpoints return null on success
        fake_post.responses.append(None)
        out = json.loads(
            safe(
                {
                    "action": "propose",
                    "safe_address": SAFE_ADDR,
                    "payload": {"x": 1},
                }
            )
        )
        assert "isError" not in out

    def test_pending_non_dict_raises(self, connected, fake_get):
        fake_get.responses.append("garbage")
        out = json.loads(safe({"action": "pending", "safe_address": SAFE_ADDR}))
        assert out["isError"] is True

    def test_pending_api_error(self, connected, fake_get):
        fake_get.responses.append(RuntimeError("timeout"))
        out = json.loads(safe({"action": "pending", "safe_address": SAFE_ADDR}))
        assert out["isError"] is True


class TestServiceLevel:
    def test_get_safe_info_unsupported_chain(self):
        # Direct service call bypasses the tool's pre-check
        from clawmes.services.safe import SafeError, get_safe_info

        with pytest.raises(SafeError) as exc_info:
            get_safe_info(SAFE_ADDR, 56)
        assert exc_info.value.code == "unsupported_chain"

    def test_supports_chain(self):
        from clawmes.services.safe import supports_chain

        assert supports_chain(1)
        assert supports_chain(8453)
        assert not supports_chain(56)


class TestRegister:
    def test_register_calls_ctx(self):
        from clawmes.tools import safe as safe_mod

        recorded = []

        class FakeCtx:
            def register_tool(self, **kw):
                recorded.append(kw)

        safe_mod.register(FakeCtx())
        assert len(recorded) == 1
        assert recorded[0]["name"] == "safe"
