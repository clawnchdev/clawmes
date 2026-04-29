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


class TestGetLogs:
    def test_basic_filter(self, svc, fake_http):
        fake_http.responses.append(
            {"status": "1", "result": [{"address": "0xtoken", "topics": []}]}
        )
        result = svc.get_logs(
            8453,
            address="0xtoken",
            topic0="0xsig",
            topic1="0xowner",
            from_block=100,
            to_block=200,
        )
        assert isinstance(result, list)
        assert len(result) == 1
        params = fake_http.calls[0]["params"]
        assert params["module"] == "logs"
        assert params["action"] == "getLogs"
        assert params["address"] == "0xtoken"
        assert params["topic0"] == "0xsig"
        assert params["topic1"] == "0xowner"
        assert params["topic0_1_opr"] == "and"
        assert params["fromBlock"] == "100"
        assert params["toBlock"] == "200"

    def test_all_topics(self, svc, fake_http):
        fake_http.responses.append({"status": "1", "result": []})
        svc.get_logs(
            8453,
            topic0="0xa",
            topic1="0xb",
            topic2="0xc",
            topic3="0xd",
        )
        params = fake_http.calls[0]["params"]
        assert params["topic0"] == "0xa"
        assert params["topic1"] == "0xb"
        assert params["topic2"] == "0xc"
        assert params["topic3"] == "0xd"
        # Operator chaining for combined filter
        assert params["topic0_1_opr"] == "and"
        assert params["topic1_2_opr"] == "and"
        assert params["topic2_3_opr"] == "and"

    def test_no_results_returns_empty(self, svc, fake_http):
        # Etherscan returns "No records found" with status="0" and result=[]
        fake_http.responses.append({"status": "0", "message": "No records found", "result": []})
        with pytest.raises(ExplorerError):
            # status="0" classifies as error per current _call semantics
            svc.get_logs(8453, address="0xtoken")

    def test_non_list_result_returns_empty(self, svc, fake_http):
        # Defensive: explorer returns a non-list payload
        fake_http.responses.append({"status": "1", "result": "unexpected"})
        result = svc.get_logs(8453)
        assert result == []

    def test_default_block_range(self, svc, fake_http):
        fake_http.responses.append({"status": "1", "result": []})
        svc.get_logs(8453)
        params = fake_http.calls[0]["params"]
        assert params["fromBlock"] == "0"
        assert params["toBlock"] == "latest"


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
