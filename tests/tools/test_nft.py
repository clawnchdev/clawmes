"""Tests for the ``nft`` tool."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from clawmes.tools.nft import nft
from clawmes.wallet.state import WalletState

OWNER = "0x" + "a" * 40
NFT_CONTRACT = "0x" + "b" * 40
RECIPIENT = "0x" + "c" * 40


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
    monkeypatch.setattr("clawmes.tools.nft.get_wallet_state", lambda: state)
    return state


@pytest.fixture
def fake_http(monkeypatch):
    class FakeHttp:
        def __init__(self):
            self.calls: list[dict] = []
            self.responses: list = []

        def __call__(self, url, *, params=None, headers=None, timeout=30.0, **kw):
            self.calls.append({"url": url, "params": params, "headers": headers})
            if not self.responses:
                raise AssertionError("no fake response queued")
            response = self.responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response

    fake = FakeHttp()
    monkeypatch.setattr("clawmes.tools.nft.http_get", fake)
    return fake


@pytest.fixture
def fake_mode(monkeypatch):
    from clawmes.services import wallet as wallet_mod

    mode = MagicMock()
    mode.send_transaction.return_value = "0x" + "f" * 64
    svc = MagicMock()
    svc.active_mode = mode
    monkeypatch.setattr(wallet_mod, "get_wallet_service", lambda: svc)
    return mode


class TestMint:
    def test_basic(self, connected, fake_mode):
        out = json.loads(nft({"action": "mint", "contract": NFT_CONTRACT, "value_wei": "1000"}))
        assert "isError" not in out
        kwargs = fake_mode.send_transaction.call_args.kwargs
        assert kwargs["to"] == NFT_CONTRACT
        assert kwargs["value"] == 1000

    def test_default_calldata(self, connected, fake_mode):
        nft({"action": "mint", "contract": NFT_CONTRACT})
        kwargs = fake_mode.send_transaction.call_args.kwargs
        # Default mint() selector
        assert kwargs["data"] == "0x1249c58b"

    def test_custom_calldata(self, connected, fake_mode):
        nft(
            {
                "action": "mint",
                "contract": NFT_CONTRACT,
                "calldata": "0xdeadbeef",
            }
        )
        kwargs = fake_mode.send_transaction.call_args.kwargs
        assert kwargs["data"] == "0xdeadbeef"

    def test_invalid_contract(self, connected, fake_mode):
        out = json.loads(nft({"action": "mint", "contract": "0xshort"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "param_error"

    def test_bad_value(self, connected, fake_mode):
        out = json.loads(
            nft(
                {
                    "action": "mint",
                    "contract": NFT_CONTRACT,
                    "value_wei": "not-a-number",
                }
            )
        )
        assert out["isError"] is True
        assert out["details"]["error_code"] == "param_error"


class TestTransfer:
    def test_basic(self, connected, fake_mode):
        out = json.loads(
            nft(
                {
                    "action": "transfer",
                    "contract": NFT_CONTRACT,
                    "to": RECIPIENT,
                    "token_id": "42",
                }
            )
        )
        assert "isError" not in out
        kwargs = fake_mode.send_transaction.call_args.kwargs
        # safeTransferFrom selector
        assert kwargs["data"].startswith("0x42842e0e")
        # token_id 42 in calldata
        assert kwargs["data"].endswith("a")  # 42 = 0x2a, ends in 'a'

    def test_invalid_to(self, connected, fake_mode):
        out = json.loads(
            nft(
                {
                    "action": "transfer",
                    "contract": NFT_CONTRACT,
                    "to": "0xshort",
                    "token_id": "1",
                }
            )
        )
        assert out["isError"] is True
        assert out["details"]["error_code"] == "param_error"

    def test_invalid_contract(self, connected, fake_mode):
        out = json.loads(
            nft(
                {
                    "action": "transfer",
                    "contract": "bad",
                    "to": RECIPIENT,
                    "token_id": "1",
                }
            )
        )
        assert out["isError"] is True

    def test_bad_token_id(self, connected, fake_mode):
        out = json.loads(
            nft(
                {
                    "action": "transfer",
                    "contract": NFT_CONTRACT,
                    "to": RECIPIENT,
                    "token_id": "not-a-num",
                }
            )
        )
        assert out["isError"] is True


class TestBurn:
    def test_basic(self, connected, fake_mode):
        out = json.loads(nft({"action": "burn", "contract": NFT_CONTRACT, "token_id": "7"}))
        assert "isError" not in out
        kwargs = fake_mode.send_transaction.call_args.kwargs
        # transferFrom selector
        assert kwargs["data"].startswith("0x23b872dd")
        # 0xdEaD address present (lowercased to 'dead')
        assert "dead" in kwargs["data"].lower()

    def test_invalid_contract(self, connected, fake_mode):
        out = json.loads(nft({"action": "burn", "contract": "x", "token_id": "1"}))
        assert out["isError"] is True

    def test_bad_token_id(self, connected, fake_mode):
        out = json.loads(nft({"action": "burn", "contract": NFT_CONTRACT, "token_id": "junk"}))
        assert out["isError"] is True


class TestRead:
    def test_info_basic(self, connected, fake_http):
        fake_http.responses.append({"tokens": [{"token": {"tokenId": "1", "name": "Test #1"}}]})
        out = json.loads(nft({"action": "info", "contract": NFT_CONTRACT, "token_id": "1"}))
        assert "isError" not in out

    def test_info_not_found(self, connected, fake_http):
        fake_http.responses.append({"tokens": []})
        out = json.loads(nft({"action": "info", "contract": NFT_CONTRACT, "token_id": "999"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "not_found"

    def test_floor(self, connected, fake_http):
        fake_http.responses.append(
            {
                "collections": [
                    {
                        "id": NFT_CONTRACT,
                        "name": "Test Collection",
                        "floorAsk": {"price": {"amount": {"native": 0.5}}},
                        "volume": {"1day": 5.0},
                        "ownerCount": 100,
                        "tokenCount": 10000,
                    }
                ]
            }
        )
        out = json.loads(nft({"action": "floor", "contract": NFT_CONTRACT}))
        assert "isError" not in out
        assert out["details"]["floor_price_eth"] == 0.5

    def test_floor_not_found(self, connected, fake_http):
        fake_http.responses.append({"collections": []})
        out = json.loads(nft({"action": "floor", "contract": NFT_CONTRACT}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "not_found"

    def test_holdings(self, connected, fake_http):
        fake_http.responses.append({"tokens": [{"token": {"tokenId": "1"}}]})
        out = json.loads(nft({"action": "holdings"}))
        assert "isError" not in out
        assert out["details"]["count"] == 1

    def test_holdings_no_owner_no_wallet(self, monkeypatch, fake_http):
        # Disconnect wallet
        monkeypatch.setattr(
            "clawmes.tools.nft.get_wallet_state",
            lambda: WalletState.disconnected(),
        )
        out = json.loads(nft({"action": "holdings"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "param_error"

    def test_unsupported_chain(self, connected, fake_http):
        out = json.loads(nft({"action": "floor", "contract": NFT_CONTRACT, "chain_id": 56}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "unsupported_chain"

    def test_api_failure(self, connected, fake_http):
        fake_http.responses.append(RuntimeError("network"))
        out = json.loads(nft({"action": "info", "contract": NFT_CONTRACT, "token_id": "1"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "api_error"

    def test_non_dict_response(self, connected, fake_http):
        fake_http.responses.append("garbage")
        out = json.loads(nft({"action": "info", "contract": NFT_CONTRACT, "token_id": "1"}))
        assert out["isError"] is True

    def test_floor_api_failure(self, connected, fake_http):
        fake_http.responses.append(RuntimeError("network"))
        out = json.loads(nft({"action": "floor", "contract": NFT_CONTRACT}))
        assert out["isError"] is True

    def test_floor_non_dict(self, connected, fake_http):
        fake_http.responses.append("garbage")
        out = json.loads(nft({"action": "floor", "contract": NFT_CONTRACT}))
        assert out["isError"] is True

    def test_holdings_api_failure(self, connected, fake_http):
        fake_http.responses.append(RuntimeError("network"))
        out = json.loads(nft({"action": "holdings"}))
        assert out["isError"] is True

    def test_holdings_non_dict(self, connected, fake_http):
        fake_http.responses.append("garbage")
        out = json.loads(nft({"action": "holdings"}))
        assert out["isError"] is True

    def test_with_api_key(self, connected, fake_http, monkeypatch):
        monkeypatch.setenv("RESERVOIR_API_KEY", "rsv-test")
        fake_http.responses.append({"tokens": []})
        nft({"action": "holdings"})
        headers = fake_http.calls[0]["headers"]
        assert headers.get("x-api-key") == "rsv-test"


class TestWriteWithoutWallet:
    def test_mint_no_wallet(self, monkeypatch):
        monkeypatch.setattr(
            "clawmes.tools.nft.get_wallet_state",
            lambda: WalletState.disconnected(),
        )
        out = json.loads(nft({"action": "mint", "contract": NFT_CONTRACT}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "wallet_not_connected"


class TestSendFailure:
    def test_no_active_mode(self, connected, monkeypatch):
        from clawmes.services import wallet as wallet_mod

        svc = MagicMock()
        svc.active_mode = None
        monkeypatch.setattr(wallet_mod, "get_wallet_service", lambda: svc)
        out = json.loads(nft({"action": "mint", "contract": NFT_CONTRACT}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "wallet_not_connected"

    def test_send_raises(self, connected, fake_mode):
        fake_mode.send_transaction.side_effect = RuntimeError("rejected")
        out = json.loads(nft({"action": "mint", "contract": NFT_CONTRACT}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "send_failed"


class TestRegister:
    def test_register_calls_ctx(self):
        from clawmes.tools import nft as nft_mod

        recorded = []

        class FakeCtx:
            def register_tool(self, **kw):
                recorded.append(kw)

        nft_mod.register(FakeCtx())
        assert len(recorded) == 1
        assert recorded[0]["name"] == "nft"
