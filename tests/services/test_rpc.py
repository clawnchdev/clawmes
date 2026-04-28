"""Tests for clawmes.services.rpc."""

from __future__ import annotations

import pytest

from clawmes.services import rpc as rpc_module
from clawmes.services.rpc import RpcError, RpcService, get_rpc_service


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(rpc_module, "_instance", None)


@pytest.fixture
def rpc():
    svc = RpcService()
    svc.start()
    return svc


@pytest.fixture
def fake_http(monkeypatch):
    """Replace ``http_post`` with a recorder. Tests set responses
    in advance via ``fake_http.responses`` (a queue of dicts)."""

    class FakeHttp:
        def __init__(self):
            self.calls: list[dict] = []
            self.responses: list[dict] = []

        def __call__(self, url, *, json=None, headers=None, timeout=30.0, **kw):
            self.calls.append({"url": url, "json": json})
            if not self.responses:
                raise AssertionError("no fake response queued")
            return self.responses.pop(0)

    fake = FakeHttp()
    monkeypatch.setattr(rpc_module, "http_post", fake)
    return fake


class TestStartStop:
    def test_start_populates_endpoints(self, rpc):
        ids = rpc.configured_chain_ids()
        assert 1 in ids
        assert 8453 in ids

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("CLAWMES_RPC_8453", "https://eth-mainnet.g.alchemy.com/custom")
        svc = RpcService()
        svc.start()
        # Calling get_balance should hit the overridden URL
        # (Verify by inspecting internal endpoint dict)
        assert svc._endpoints[8453].url == "https://eth-mainnet.g.alchemy.com/custom"

    def test_stop_clears(self, rpc):
        rpc.stop()
        assert rpc.configured_chain_ids() == []


class TestHasEndpoint:
    def test_known_chain(self, rpc):
        assert rpc.has_endpoint(8453) is True

    def test_unknown_chain(self, rpc):
        assert rpc.has_endpoint(999999) is False


class TestBlockNumber:
    def test_hex_response(self, rpc, fake_http):
        fake_http.responses.append({"jsonrpc": "2.0", "id": 1, "result": "0x1a2b3c"})
        result = rpc.block_number(8453)
        assert result == 0x1A2B3C

    def test_int_response(self, rpc, fake_http):
        # Some RPCs return integers directly (non-spec but seen in the wild)
        fake_http.responses.append({"jsonrpc": "2.0", "id": 1, "result": 12345})
        assert rpc.block_number(8453) == 12345


class TestGetBalance:
    def test_basic(self, rpc, fake_http):
        # 1 ETH = 0xde0b6b3a7640000
        fake_http.responses.append({"jsonrpc": "2.0", "id": 1, "result": "0xde0b6b3a7640000"})
        result = rpc.get_balance("0x" + "a" * 40, 8453)
        assert result == 10**18

    def test_int_response(self, rpc, fake_http):
        fake_http.responses.append({"jsonrpc": "2.0", "id": 1, "result": 1000})
        assert rpc.get_balance("0xabc", 8453) == 1000

    def test_call_shape(self, rpc, fake_http):
        fake_http.responses.append({"jsonrpc": "2.0", "id": 1, "result": "0x0"})
        rpc.get_balance("0xdead", 8453)
        body = fake_http.calls[0]["json"]
        assert body["method"] == "eth_getBalance"
        assert body["params"] == ["0xdead", "latest"]


class TestEthCall:
    def test_basic(self, rpc, fake_http):
        fake_http.responses.append({"jsonrpc": "2.0", "id": 1, "result": "0x" + "0" * 62 + "12"})
        result = rpc.eth_call(to="0xtoken", data="0x70a08231", chain_id=8453)
        assert result == "0x" + "0" * 62 + "12"

    def test_none_result_returns_0x(self, rpc, fake_http):
        fake_http.responses.append({"jsonrpc": "2.0", "id": 1, "result": None})
        assert rpc.eth_call(to="0x", data="0x", chain_id=8453) == "0x"


class TestChainId:
    def test_hex(self, rpc, fake_http):
        fake_http.responses.append({"jsonrpc": "2.0", "id": 1, "result": "0x2105"})  # Base
        assert rpc.chain_id(8453) == 0x2105

    def test_int(self, rpc, fake_http):
        fake_http.responses.append({"jsonrpc": "2.0", "id": 1, "result": 8453})
        assert rpc.chain_id(8453) == 8453


class TestGetTransactionCount:
    def test_hex_response_uses_pending(self, rpc, fake_http):
        fake_http.responses.append({"jsonrpc": "2.0", "id": 1, "result": "0x7"})
        n = rpc.get_transaction_count("0x" + "a" * 40, 8453)
        assert n == 7
        sent = fake_http.calls[0]["json"]
        assert sent["method"] == "eth_getTransactionCount"
        assert sent["params"] == ["0x" + "a" * 40, "pending"]

    def test_int_response(self, rpc, fake_http):
        fake_http.responses.append({"jsonrpc": "2.0", "id": 1, "result": 12})
        assert rpc.get_transaction_count("0x" + "a" * 40, 8453) == 12

    def test_explicit_block(self, rpc, fake_http):
        fake_http.responses.append({"jsonrpc": "2.0", "id": 1, "result": "0x0"})
        rpc.get_transaction_count("0x" + "a" * 40, 8453, block="latest")
        sent = fake_http.calls[0]["json"]
        assert sent["params"][1] == "latest"


class TestSendRawTransaction:
    def test_basic(self, rpc, fake_http):
        fake_http.responses.append({"jsonrpc": "2.0", "id": 1, "result": "0x" + "f" * 64})
        h = rpc.send_raw_transaction("0xdead", 8453)
        assert h == "0x" + "f" * 64

    def test_adds_0x_prefix(self, rpc, fake_http):
        fake_http.responses.append({"jsonrpc": "2.0", "id": 1, "result": "0x" + "f" * 64})
        rpc.send_raw_transaction("dead", 8453)
        sent = fake_http.calls[0]["json"]
        assert sent["params"] == ["0xdead"]

    def test_non_string_result_raises(self, rpc, fake_http):
        fake_http.responses.append({"jsonrpc": "2.0", "id": 1, "result": 123})
        with pytest.raises(RpcError) as exc_info:
            rpc.send_raw_transaction("0xdead", 8453)
        assert exc_info.value.code == -32700
        assert exc_info.value.method == "eth_sendRawTransaction"


class TestGetTransactionReceipt:
    def test_pending_returns_none(self, rpc, fake_http):
        fake_http.responses.append({"jsonrpc": "2.0", "id": 1, "result": None})
        assert rpc.get_transaction_receipt("0x" + "f" * 64, 8453) is None

    def test_mined_returns_dict(self, rpc, fake_http):
        receipt = {"status": "0x1", "blockNumber": "0x123"}
        fake_http.responses.append({"jsonrpc": "2.0", "id": 1, "result": receipt})
        got = rpc.get_transaction_receipt("0x" + "f" * 64, 8453)
        assert got == receipt

    def test_unexpected_type_raises(self, rpc, fake_http):
        fake_http.responses.append({"jsonrpc": "2.0", "id": 1, "result": "not a receipt"})
        with pytest.raises(RpcError) as exc_info:
            rpc.get_transaction_receipt("0x" + "f" * 64, 8453)
        assert exc_info.value.code == -32700


class TestWaitForReceipt:
    def test_returns_immediately_when_present(self, rpc, fake_http, monkeypatch):
        receipt = {"status": "0x1"}
        fake_http.responses.append({"jsonrpc": "2.0", "id": 1, "result": receipt})
        monkeypatch.setattr("clawmes.services.rpc.time.sleep", lambda _: None)
        got = rpc.wait_for_receipt("0x" + "f" * 64, 8453, timeout=1.0, poll_interval=0.01)
        assert got == receipt

    def test_polls_until_present(self, rpc, fake_http, monkeypatch):
        receipt = {"status": "0x1"}
        fake_http.responses.append({"jsonrpc": "2.0", "id": 1, "result": None})
        fake_http.responses.append({"jsonrpc": "2.0", "id": 2, "result": None})
        fake_http.responses.append({"jsonrpc": "2.0", "id": 3, "result": receipt})
        monkeypatch.setattr("clawmes.services.rpc.time.sleep", lambda _: None)
        got = rpc.wait_for_receipt("0x" + "f" * 64, 8453, timeout=10.0, poll_interval=0.01)
        assert got == receipt
        assert len(fake_http.calls) == 3

    def test_times_out(self, rpc, fake_http, monkeypatch):
        # Always-pending receipt; advance monotonic past the deadline on
        # the second iteration so the loop exits cleanly.
        clock = iter([0.0, 100.0])
        monkeypatch.setattr("clawmes.services.rpc.time.monotonic", lambda: next(clock))
        monkeypatch.setattr("clawmes.services.rpc.time.sleep", lambda _: None)
        fake_http.responses.append({"jsonrpc": "2.0", "id": 1, "result": None})
        with pytest.raises(RpcError) as exc_info:
            rpc.wait_for_receipt("0x" + "f" * 64, 8453, timeout=5.0, poll_interval=0.01)
        assert "timed out" in exc_info.value.message
        assert exc_info.value.method == "eth_getTransactionReceipt"


class TestErrorPaths:
    def test_unconfigured_chain(self, rpc):
        with pytest.raises(RpcError) as exc_info:
            rpc.block_number(999999)
        assert "no RPC endpoint" in exc_info.value.message
        assert exc_info.value.method == "eth_blockNumber"

    def test_rpc_error_response(self, rpc, fake_http):
        fake_http.responses.append(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "error": {"code": -32000, "message": "execution reverted"},
            }
        )
        with pytest.raises(RpcError) as exc_info:
            rpc.eth_call(to="0x", data="0x", chain_id=8453)
        assert exc_info.value.code == -32000
        assert "execution reverted" in exc_info.value.message

    def test_rpc_error_non_dict(self, rpc, fake_http):
        # `error` field is a string, not a dict — fall back to defaults
        fake_http.responses.append({"jsonrpc": "2.0", "id": 1, "error": "some string error"})
        with pytest.raises(RpcError) as exc_info:
            rpc.eth_call(to="0x", data="0x", chain_id=8453)
        assert exc_info.value.code == -32000
        assert "some string error" in exc_info.value.message

    def test_non_dict_response(self, rpc, fake_http):
        fake_http.responses.append("not a dict")  # type: ignore[arg-type]
        with pytest.raises(RpcError) as exc_info:
            rpc.eth_call(to="0x", data="0x", chain_id=8453)
        assert exc_info.value.code == -32700


class TestRequestIdIncrement:
    def test_each_call_unique_id(self, rpc, fake_http):
        for _ in range(3):
            fake_http.responses.append({"jsonrpc": "2.0", "id": 0, "result": "0x0"})
        rpc.block_number(8453)
        rpc.block_number(8453)
        rpc.block_number(8453)
        ids = [c["json"]["id"] for c in fake_http.calls]
        assert ids == sorted(set(ids))
        assert len(ids) == 3


class TestSingleton:
    def test_returns_same_instance(self):
        a = get_rpc_service()
        b = get_rpc_service()
        assert a is b


class TestRpcErrorClass:
    def test_message_format(self):
        err = RpcError(-32000, "boom", method="eth_call")
        assert "eth_call failed" in str(err)
        assert "-32000" in str(err)

    def test_no_method(self):
        err = RpcError(-32000, "boom")
        assert "rpc failed" in str(err)
