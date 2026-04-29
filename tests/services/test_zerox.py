"""Tests for clawmes.services.zerox."""

from __future__ import annotations

import pytest

from clawmes.services import zerox as zerox_module
from clawmes.services.zerox import (
    ZeroxError,
    ZeroxService,
    get_zerox_service,
)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(zerox_module, "_instance", None)
    monkeypatch.delenv("ZEROX_API_KEY", raising=False)


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
    monkeypatch.setattr(zerox_module, "http_get", fake)
    return fake


@pytest.fixture
def svc():
    s = ZeroxService()
    s.start()
    return s


class TestStartStop:
    def test_start_no_key(self, svc):
        assert svc._api_key is None

    def test_start_with_key(self, monkeypatch):
        monkeypatch.setenv("ZEROX_API_KEY", "zx-test-123")
        s = ZeroxService()
        s.start()
        assert s._api_key == "zx-test-123"

    def test_stop_clears_key(self, monkeypatch):
        monkeypatch.setenv("ZEROX_API_KEY", "k")
        s = ZeroxService()
        s.start()
        s.stop()
        assert s._api_key is None


class TestSupportsChain:
    def test_supports_known(self, svc):
        for cid in (1, 8453, 42161, 10, 137):
            assert svc.supports_chain(cid)

    def test_rejects_unknown(self, svc):
        assert not svc.supports_chain(56)  # BSC not supported by 0x


class TestGetPrice:
    def test_basic(self, svc, fake_http):
        fake_http.responses.append(
            {"sellAmount": "1000", "buyAmount": "950", "minBuyAmount": "940"}
        )
        result = svc.get_price(
            chain_id=8453,
            sell_token="0x" + "a" * 40,
            buy_token="0x" + "b" * 40,
            sell_amount=1000,
        )
        assert result["buyAmount"] == "950"
        params = fake_http.calls[0]["params"]
        assert params["chainId"] == "8453"
        assert params["sellAmount"] == "1000"

    def test_buy_amount_only(self, svc, fake_http):
        fake_http.responses.append({"sellAmount": "950", "buyAmount": "1000"})
        svc.get_price(
            chain_id=8453,
            sell_token="0x" + "a" * 40,
            buy_token="0x" + "b" * 40,
            buy_amount=1000,
        )
        params = fake_http.calls[0]["params"]
        assert params["buyAmount"] == "1000"
        assert "sellAmount" not in params

    def test_with_taker(self, svc, fake_http):
        fake_http.responses.append({"sellAmount": "1", "buyAmount": "1"})
        svc.get_price(
            chain_id=8453,
            sell_token="0x" + "a" * 40,
            buy_token="0x" + "b" * 40,
            sell_amount=1,
            taker="0x" + "c" * 40,
        )
        params = fake_http.calls[0]["params"]
        assert params["taker"] == "0x" + "c" * 40

    def test_unsupported_chain(self, svc):
        with pytest.raises(ZeroxError) as exc_info:
            svc.get_price(
                chain_id=56,
                sell_token="0x",
                buy_token="0x",
                sell_amount=1,
            )
        assert exc_info.value.code == "unsupported_chain"

    def test_both_amounts_rejected(self, svc):
        with pytest.raises(ZeroxError):
            svc.get_price(
                chain_id=8453,
                sell_token="0x",
                buy_token="0x",
                sell_amount=1,
                buy_amount=1,
            )

    def test_neither_amount_rejected(self, svc):
        with pytest.raises(ZeroxError):
            svc.get_price(
                chain_id=8453,
                sell_token="0x",
                buy_token="0x",
            )

    def test_api_key_in_headers(self, monkeypatch, fake_http):
        monkeypatch.setenv("ZEROX_API_KEY", "zx-secret")
        s = ZeroxService()
        s.start()
        fake_http.responses.append({"sellAmount": "1", "buyAmount": "1"})
        s.get_price(
            chain_id=8453,
            sell_token="0x",
            buy_token="0x",
            sell_amount=1,
        )
        headers = fake_http.calls[0]["headers"]
        assert headers["0x-api-key"] == "zx-secret"
        assert headers["0x-version"] == "v2"

    def test_no_api_key_no_header(self, svc, fake_http):
        fake_http.responses.append({"sellAmount": "1", "buyAmount": "1"})
        svc.get_price(
            chain_id=8453,
            sell_token="0x",
            buy_token="0x",
            sell_amount=1,
        )
        headers = fake_http.calls[0]["headers"]
        assert "0x-api-key" not in headers


class TestGetQuote:
    def test_basic(self, svc, fake_http):
        fake_http.responses.append(
            {
                "sellAmount": "1000",
                "buyAmount": "950",
                "transaction": {
                    "to": "0xrouter",
                    "data": "0xdead",
                    "value": "0x0",
                    "gas": "0x30000",
                },
            }
        )
        result = svc.get_quote(
            chain_id=8453,
            sell_token="0x" + "a" * 40,
            buy_token="0x" + "b" * 40,
            taker="0x" + "c" * 40,
            sell_amount=1000,
        )
        assert "transaction" in result
        params = fake_http.calls[0]["params"]
        assert params["taker"] == "0x" + "c" * 40

    def test_unsupported_chain(self, svc):
        with pytest.raises(ZeroxError) as exc_info:
            svc.get_quote(
                chain_id=999,
                sell_token="0x",
                buy_token="0x",
                taker="0x",
                sell_amount=1,
            )
        assert exc_info.value.code == "unsupported_chain"

    def test_both_amounts_rejected(self, svc):
        with pytest.raises(ZeroxError):
            svc.get_quote(
                chain_id=8453,
                sell_token="0x",
                buy_token="0x",
                taker="0x",
                sell_amount=1,
                buy_amount=1,
            )


class TestErrorClassification:
    def test_rate_limit(self, svc, fake_http):
        fake_http.responses.append(RuntimeError("HTTP 429 Too Many Requests"))
        with pytest.raises(ZeroxError) as exc_info:
            svc.get_price(
                chain_id=8453,
                sell_token="0x",
                buy_token="0x",
                sell_amount=1,
            )
        assert exc_info.value.code == "rate_limited"

    def test_generic_failure(self, svc, fake_http):
        fake_http.responses.append(RuntimeError("connection reset"))
        with pytest.raises(ZeroxError) as exc_info:
            svc.get_price(
                chain_id=8453,
                sell_token="0x",
                buy_token="0x",
                sell_amount=1,
            )
        assert exc_info.value.code == "api_error"

    def test_non_dict_response(self, svc, fake_http):
        fake_http.responses.append("not a dict")
        with pytest.raises(ZeroxError) as exc_info:
            svc.get_price(
                chain_id=8453,
                sell_token="0x",
                buy_token="0x",
                sell_amount=1,
            )
        assert exc_info.value.code == "api_error"

    def test_zerox_error_envelope(self, svc, fake_http):
        fake_http.responses.append({"name": "InputError", "reason": "invalid sellAmount"})
        with pytest.raises(ZeroxError) as exc_info:
            svc.get_price(
                chain_id=8453,
                sell_token="0x",
                buy_token="0x",
                sell_amount=1,
            )
        assert exc_info.value.code == "api_error"
        assert "InputError" in exc_info.value.message

    def test_no_liquidity_classified(self, svc, fake_http):
        fake_http.responses.append(
            {"name": "BadRequest", "reason": "no liquidity available for that path"}
        )
        with pytest.raises(ZeroxError) as exc_info:
            svc.get_price(
                chain_id=8453,
                sell_token="0x",
                buy_token="0x",
                sell_amount=1,
            )
        assert exc_info.value.code == "insufficient_liquidity"

    def test_v2_error_envelope_with_message(self, svc, fake_http):
        """v2 returns {name, message, data}; the legacy path used reason."""
        fake_http.responses.append(
            {
                "name": "INPUT_INVALID",
                "message": "Invalid sellToken address",
                "data": {"zid": "abc-123", "details": []},
            }
        )
        with pytest.raises(ZeroxError) as exc_info:
            svc.get_price(
                chain_id=8453,
                sell_token="0x",
                buy_token="0x",
                sell_amount=1,
            )
        assert exc_info.value.code == "api_error"
        assert "INPUT_INVALID" in exc_info.value.message
        assert "Invalid sellToken" in exc_info.value.message

    def test_v2_no_liquidity_via_liquidity_available_false(self, svc, fake_http):
        """v2 signals no-liquidity via {liquidityAvailable: false}, not an error."""
        fake_http.responses.append({"liquidityAvailable": False, "zid": "abc-123"})
        with pytest.raises(ZeroxError) as exc_info:
            svc.get_price(
                chain_id=8453,
                sell_token="0x",
                buy_token="0x",
                sell_amount=1,
            )
        assert exc_info.value.code == "insufficient_liquidity"

    def test_v2_no_liquidity_via_error_message_keyword(self, svc, fake_http):
        """The 'insufficient_liquidity' keyword in v2 messages also classifies."""
        fake_http.responses.append(
            {
                "name": "SWAP_VALIDATION_FAILED",
                "message": "insufficient_liquidity for sellAmount",
                "data": {"zid": "abc-123"},
            }
        )
        with pytest.raises(ZeroxError) as exc_info:
            svc.get_price(
                chain_id=8453,
                sell_token="0x",
                buy_token="0x",
                sell_amount=1,
            )
        assert exc_info.value.code == "insufficient_liquidity"


class TestParseInt:
    def test_decimal(self):
        from clawmes.services.zerox import parse_0x_int

        assert parse_0x_int("1000000000000000000") == 10**18

    def test_hex(self):
        from clawmes.services.zerox import parse_0x_int

        assert parse_0x_int("0xde0b6b3a7640000") == 10**18

    def test_hex_uppercase_prefix(self):
        from clawmes.services.zerox import parse_0x_int

        assert parse_0x_int("0XDE0B6B3A7640000") == 10**18

    def test_int_passthrough(self):
        from clawmes.services.zerox import parse_0x_int

        assert parse_0x_int(42) == 42

    def test_none_returns_zero(self):
        from clawmes.services.zerox import parse_0x_int

        assert parse_0x_int(None) == 0

    def test_empty_returns_zero(self):
        from clawmes.services.zerox import parse_0x_int

        assert parse_0x_int("") == 0

    def test_invalid_decimal_raises(self):
        from clawmes.services.zerox import parse_0x_int

        with pytest.raises(ValueError):
            parse_0x_int("not-a-number")


class TestQuoteAmountModes:
    def test_quote_with_buy_amount_only(self, svc, fake_http):
        # The get_quote method has the same one-of-amounts logic as
        # get_price; this exercises the buy_amount branch.
        fake_http.responses.append(
            {
                "sellAmount": "950",
                "buyAmount": "1000",
                "transaction": {"to": "0x", "data": "0x", "value": "0x0", "gas": "0x1"},
            }
        )
        svc.get_quote(
            chain_id=8453,
            sell_token="0x" + "a" * 40,
            buy_token="0x" + "b" * 40,
            taker="0x" + "c" * 40,
            buy_amount=1000,
        )
        params = fake_http.calls[0]["params"]
        assert params["buyAmount"] == "1000"
        assert "sellAmount" not in params


class TestSingleton:
    def test_returns_same_instance(self):
        a = get_zerox_service()
        b = get_zerox_service()
        assert a is b
