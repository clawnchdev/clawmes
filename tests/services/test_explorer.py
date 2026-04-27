"""Tests for clawmes.services.explorer."""

from __future__ import annotations

import pytest

from clawmes.services import explorer as ex_module
from clawmes.services.explorer import (
    ExplorerError,
    ExplorerService,
    get_explorer_service,
)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(ex_module, "_instance", None)


@pytest.fixture
def fake_http(monkeypatch):
    class FakeHttp:
        def __init__(self):
            self.calls: list[dict] = []
            self.responses: list = []

        def __call__(self, url, *, params=None, headers=None, timeout=30.0, **kw):
            self.calls.append({"url": url, "params": params})
            if not self.responses:
                raise AssertionError("no fake response queued")
            return self.responses.pop(0)

    fake = FakeHttp()
    monkeypatch.setattr(ex_module, "http_get", fake)
    return fake


@pytest.fixture
def svc():
    s = ExplorerService()
    s.start()
    return s


class TestSupportsChain:
    def test_known(self, svc):
        assert svc.supports_chain(8453) is True

    def test_unknown(self, svc):
        assert svc.supports_chain(999999) is False

    def test_explorer_name(self, svc):
        assert svc.explorer_name(8453) == "Basescan"

    def test_explorer_name_unknown_raises(self, svc):
        with pytest.raises(ExplorerError, match="no explorer configured"):
            svc.explorer_name(999999)


class TestGetTxStatus:
    def test_basic(self, svc, fake_http):
        fake_http.responses.append(
            {"status": "1", "message": "OK", "result": {"isError": "0", "errDescription": ""}}
        )
        result = svc.get_tx_status("0x" + "a" * 64, 8453)
        assert result == {"isError": "0", "errDescription": ""}

    def test_error_response(self, svc, fake_http):
        fake_http.responses.append({"status": "0", "message": "NOTOK", "result": "rate limit"})
        with pytest.raises(ExplorerError, match="NOTOK"):
            svc.get_tx_status("0x", 8453)


class TestGetTxReceiptStatus:
    def test_success(self, svc, fake_http):
        fake_http.responses.append({"status": "1", "result": {"status": "1"}})
        result = svc.get_tx_receipt_status("0xabc", 8453)
        assert result == {"status": "1"}

    def test_call_params(self, svc, fake_http):
        fake_http.responses.append({"status": "1", "result": {}})
        svc.get_tx_receipt_status("0xabc", 8453)
        params = fake_http.calls[0]["params"]
        assert params["module"] == "transaction"
        assert params["action"] == "gettxreceiptstatus"
        assert params["txhash"] == "0xabc"


class TestGetAddressBalance:
    def test_string_response(self, svc, fake_http):
        fake_http.responses.append({"status": "1", "result": "1500000000000000000"})
        balance = svc.get_address_balance("0xabc", 8453)
        assert balance == 1_500_000_000_000_000_000

    def test_int_response(self, svc, fake_http):
        fake_http.responses.append({"status": "1", "result": 12345})
        assert svc.get_address_balance("0xabc", 8453) == 12345

    def test_unexpected_type_returns_zero(self, svc, fake_http):
        fake_http.responses.append({"status": "1", "result": ["unexpected"]})
        # Non-str/int → coerced to 0 via the isinstance check
        assert svc.get_address_balance("0xabc", 8453) == 0


class TestGetAddressTxCount:
    def test_hex_response(self, svc, fake_http):
        fake_http.responses.append({"jsonrpc": "2.0", "result": "0x1f"})
        count = svc.get_address_tx_count("0xabc", 8453)
        assert count == 31

    def test_int_response(self, svc, fake_http):
        fake_http.responses.append({"jsonrpc": "2.0", "result": 42})
        assert svc.get_address_tx_count("0xabc", 8453) == 42

    def test_unexpected_response(self, svc, fake_http):
        fake_http.responses.append({"jsonrpc": "2.0", "result": None})
        assert svc.get_address_tx_count("0xabc", 8453) == 0


class TestApiKeyHandling:
    def test_includes_apikey_when_set(self, svc, fake_http, monkeypatch):
        monkeypatch.setenv("BASESCAN_API_KEY", "test-key-123")
        fake_http.responses.append({"status": "1", "result": "0"})
        svc.get_address_balance("0xabc", 8453)
        assert fake_http.calls[0]["params"]["apikey"] == "test-key-123"

    def test_no_apikey_when_unset(self, svc, fake_http, monkeypatch):
        monkeypatch.delenv("BASESCAN_API_KEY", raising=False)
        fake_http.responses.append({"status": "1", "result": "0"})
        svc.get_address_balance("0xabc", 8453)
        assert "apikey" not in fake_http.calls[0]["params"]


class TestErrorPaths:
    def test_unknown_chain_raises(self, svc):
        with pytest.raises(ExplorerError, match="no explorer configured"):
            svc.get_address_balance("0xabc", 999999)

    def test_non_dict_response(self, svc, fake_http):
        fake_http.responses.append("not a dict")
        with pytest.raises(ExplorerError, match="non-dict response"):
            svc.get_address_balance("0xabc", 8453)

    def test_missing_result_passes_through(self, svc, fake_http):
        # Some endpoints return non-standard shape — pass through whole response
        fake_http.responses.append({"jsonrpc": "2.0", "id": 1, "data": "x"})
        result = svc.get_address_tx_count("0xabc", 8453)
        # No "result" → method returns whole response (which is dict, not str/int) → 0
        assert result == 0


class TestSingleton:
    def test_returns_same_instance(self):
        a = get_explorer_service()
        b = get_explorer_service()
        assert a is b


class TestLifecycle:
    def test_stop_is_noop(self):
        s = ExplorerService()
        s.start()
        s.stop()  # must not raise
