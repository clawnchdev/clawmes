"""Tests for clawmes.services.opengateway."""

from __future__ import annotations

import pytest

from clawmes.services import opengateway as ogw_module
from clawmes.services.opengateway import (
    OpenGatewayError,
    OpenGatewayService,
    get_opengateway_service,
)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(ogw_module, "_instance", None)
    monkeypatch.delenv("OPENGATEWAY_API_KEY", raising=False)
    monkeypatch.delenv("OPENGATEWAY_MODEL", raising=False)


@pytest.fixture
def fake_http(monkeypatch):
    class FakeHttp:
        def __init__(self):
            self.calls: list[dict] = []
            self.responses: list = []

        def __call__(self, url, *, json=None, headers=None, timeout=30.0, **kw):
            self.calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
            if not self.responses:
                raise AssertionError("no fake response queued")
            response = self.responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response

    fake = FakeHttp()
    monkeypatch.setattr(ogw_module, "http_post", fake)
    return fake


@pytest.fixture
def svc(monkeypatch):
    monkeypatch.setenv("OPENGATEWAY_API_KEY", "ogw_live_test_key")
    monkeypatch.setenv("OPENGATEWAY_MODEL", "mimo-v2.5-pro")
    s = OpenGatewayService()
    s.start()
    return s


class TestStartStop:
    def test_start_no_key_or_model(self):
        s = OpenGatewayService()
        s.start()
        assert s._api_key is None
        assert s._default_model is None

    def test_start_with_key(self, monkeypatch):
        monkeypatch.setenv("OPENGATEWAY_API_KEY", "ogw_live_abc")
        s = OpenGatewayService()
        s.start()
        assert s._api_key == "ogw_live_abc"

    def test_start_with_model(self, monkeypatch):
        monkeypatch.setenv("OPENGATEWAY_MODEL", "mimo-v2.5-pro")
        s = OpenGatewayService()
        s.start()
        assert s._default_model == "mimo-v2.5-pro"

    def test_start_empty_string_normalized_to_none(self, monkeypatch):
        # Empty-string env vars are treated as unset — covers the `or None`
        # fallback in start(). bash users hit this when they `export FOO=`.
        monkeypatch.setenv("OPENGATEWAY_API_KEY", "")
        monkeypatch.setenv("OPENGATEWAY_MODEL", "")
        s = OpenGatewayService()
        s.start()
        assert s._api_key is None
        assert s._default_model is None

    def test_stop_clears_state(self, svc):
        svc.stop()
        assert svc._api_key is None
        assert svc._default_model is None


class TestHealth:
    def test_configured(self, svc):
        h = svc.health()
        assert h["id"] == "clawmes.opengateway"
        assert h["status"] == "configured"
        assert h["default_model"] == "mimo-v2.5-pro"

    def test_missing_key(self):
        s = OpenGatewayService()
        s.start()
        h = s.health()
        assert h["status"] == "missing_key"
        assert h["default_model"] is None


class TestChatCompletionGuards:
    def test_empty_messages(self, svc):
        with pytest.raises(OpenGatewayError) as exc_info:
            svc.chat_completion([])
        assert exc_info.value.code == "bad_request"
        assert "non-empty" in exc_info.value.message

    def test_streaming_rejected(self, svc):
        with pytest.raises(OpenGatewayError) as exc_info:
            svc.chat_completion(
                [{"role": "user", "content": "hi"}],
                stream=True,
            )
        assert exc_info.value.code == "bad_request"
        assert "streaming" in exc_info.value.message

    def test_no_api_key(self, monkeypatch):
        monkeypatch.setenv("OPENGATEWAY_MODEL", "mimo-v2.5-pro")
        s = OpenGatewayService()
        s.start()
        with pytest.raises(OpenGatewayError) as exc_info:
            s.chat_completion([{"role": "user", "content": "hi"}])
        assert exc_info.value.code == "no_credentials"

    def test_no_model_anywhere(self, monkeypatch):
        monkeypatch.setenv("OPENGATEWAY_API_KEY", "ogw_live_abc")
        s = OpenGatewayService()
        s.start()
        with pytest.raises(OpenGatewayError) as exc_info:
            s.chat_completion([{"role": "user", "content": "hi"}])
        assert exc_info.value.code == "bad_request"
        assert "model" in exc_info.value.message


class TestChatCompletionRequest:
    def _ok_response(self):
        return {
            "id": "chatcmpl-abc",
            "object": "chat.completion",
            "model": "mimo-v2.5-pro",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "hello"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
        }

    def test_basic_call_uses_env_default_model(self, svc, fake_http):
        fake_http.responses.append(self._ok_response())
        result = svc.chat_completion([{"role": "user", "content": "hi"}])
        assert result["choices"][0]["message"]["content"] == "hello"
        sent = fake_http.calls[0]["json"]
        assert sent["model"] == "mimo-v2.5-pro"
        assert sent["messages"] == [{"role": "user", "content": "hi"}]

    def test_model_arg_overrides_default(self, svc, fake_http):
        fake_http.responses.append(self._ok_response())
        svc.chat_completion(
            [{"role": "user", "content": "hi"}],
            model="some-other-model",
        )
        assert fake_http.calls[0]["json"]["model"] == "some-other-model"

    def test_model_arg_only_no_default(self, monkeypatch, fake_http):
        monkeypatch.setenv("OPENGATEWAY_API_KEY", "ogw_live_abc")
        s = OpenGatewayService()
        s.start()
        fake_http.responses.append(self._ok_response())
        s.chat_completion(
            [{"role": "user", "content": "hi"}],
            model="explicit-model",
        )
        assert fake_http.calls[0]["json"]["model"] == "explicit-model"

    def test_temperature_passed(self, svc, fake_http):
        fake_http.responses.append(self._ok_response())
        svc.chat_completion(
            [{"role": "user", "content": "hi"}],
            temperature=0.2,
        )
        assert fake_http.calls[0]["json"]["temperature"] == 0.2

    def test_temperature_omitted_when_none(self, svc, fake_http):
        fake_http.responses.append(self._ok_response())
        svc.chat_completion([{"role": "user", "content": "hi"}])
        assert "temperature" not in fake_http.calls[0]["json"]

    def test_max_tokens_passed(self, svc, fake_http):
        fake_http.responses.append(self._ok_response())
        svc.chat_completion(
            [{"role": "user", "content": "hi"}],
            max_tokens=128,
        )
        assert fake_http.calls[0]["json"]["max_tokens"] == 128

    def test_max_tokens_omitted_when_none(self, svc, fake_http):
        fake_http.responses.append(self._ok_response())
        svc.chat_completion([{"role": "user", "content": "hi"}])
        assert "max_tokens" not in fake_http.calls[0]["json"]

    def test_extra_kwargs_passed_through(self, svc, fake_http):
        fake_http.responses.append(self._ok_response())
        svc.chat_completion(
            [{"role": "user", "content": "hi"}],
            top_p=0.9,
            stop=["\n\n"],
            response_format={"type": "json_object"},
        )
        sent = fake_http.calls[0]["json"]
        assert sent["top_p"] == 0.9
        assert sent["stop"] == ["\n\n"]
        assert sent["response_format"] == {"type": "json_object"}

    def test_bearer_auth_header(self, svc, fake_http):
        fake_http.responses.append(self._ok_response())
        svc.chat_completion([{"role": "user", "content": "hi"}])
        headers = fake_http.calls[0]["headers"]
        assert headers["Authorization"] == "Bearer ogw_live_test_key"
        assert headers["Content-Type"] == "application/json"

    def test_request_url(self, svc, fake_http):
        fake_http.responses.append(self._ok_response())
        svc.chat_completion([{"role": "user", "content": "hi"}])
        assert fake_http.calls[0]["url"] == ("https://opengateway.gitlawb.com/v1/chat/completions")

    def test_custom_timeout(self, svc, fake_http):
        fake_http.responses.append(self._ok_response())
        svc.chat_completion(
            [{"role": "user", "content": "hi"}],
            timeout=5.0,
        )
        assert fake_http.calls[0]["timeout"] == 5.0

    def test_default_timeout(self, svc, fake_http):
        fake_http.responses.append(self._ok_response())
        svc.chat_completion([{"role": "user", "content": "hi"}])
        assert fake_http.calls[0]["timeout"] == 60.0


class TestChatCompletionErrorClassification:
    def _call(self, svc):
        return svc.chat_completion([{"role": "user", "content": "hi"}])

    def test_rate_limit_via_429(self, svc, fake_http):
        fake_http.responses.append(RuntimeError("HTTP 429 Too Many Requests"))
        with pytest.raises(OpenGatewayError) as exc_info:
            self._call(svc)
        assert exc_info.value.code == "rate_limited"

    def test_rate_limit_via_keyword(self, svc, fake_http):
        fake_http.responses.append(RuntimeError("upstream rate exceeded"))
        with pytest.raises(OpenGatewayError) as exc_info:
            self._call(svc)
        assert exc_info.value.code == "rate_limited"

    def test_model_not_found_via_404(self, svc, fake_http):
        fake_http.responses.append(RuntimeError("HTTP 404 Not Found"))
        with pytest.raises(OpenGatewayError) as exc_info:
            self._call(svc)
        assert exc_info.value.code == "model_not_found"

    def test_model_not_found_via_keyword(self, svc, fake_http):
        fake_http.responses.append(RuntimeError("the model 'xyz' was not found"))
        with pytest.raises(OpenGatewayError) as exc_info:
            self._call(svc)
        assert exc_info.value.code == "model_not_found"

    def test_bad_request_via_400(self, svc, fake_http):
        fake_http.responses.append(RuntimeError("HTTP 400 Bad Request: bad params"))
        with pytest.raises(OpenGatewayError) as exc_info:
            self._call(svc)
        assert exc_info.value.code == "bad_request"

    def test_generic_failure(self, svc, fake_http):
        fake_http.responses.append(RuntimeError("connection reset"))
        with pytest.raises(OpenGatewayError) as exc_info:
            self._call(svc)
        assert exc_info.value.code == "api_error"

    def test_non_dict_response(self, svc, fake_http):
        fake_http.responses.append("not a dict")
        with pytest.raises(OpenGatewayError) as exc_info:
            self._call(svc)
        assert exc_info.value.code == "api_error"

    def test_error_envelope_rate_via_type(self, svc, fake_http):
        fake_http.responses.append(
            {
                "error": {
                    "message": "you have hit the limit",
                    "type": "rate_limit_error",
                }
            }
        )
        with pytest.raises(OpenGatewayError) as exc_info:
            self._call(svc)
        assert exc_info.value.code == "rate_limited"

    def test_error_envelope_rate_via_message(self, svc, fake_http):
        fake_http.responses.append({"error": {"message": "rate limit hit", "type": "server_error"}})
        with pytest.raises(OpenGatewayError) as exc_info:
            self._call(svc)
        assert exc_info.value.code == "rate_limited"

    def test_error_envelope_model_not_found(self, svc, fake_http):
        fake_http.responses.append(
            {
                "error": {
                    "message": "The model 'gpt-7' was not found",
                    "type": "invalid_request_error",
                }
            }
        )
        with pytest.raises(OpenGatewayError) as exc_info:
            self._call(svc)
        # The "model … not found" keyword check takes precedence over the
        # invalid_request_error type check; both are valid classifications
        # but the more specific one wins.
        assert exc_info.value.code == "model_not_found"

    def test_error_envelope_invalid_request(self, svc, fake_http):
        fake_http.responses.append(
            {
                "error": {
                    "message": "invalid temperature value",
                    "type": "invalid_request_error",
                }
            }
        )
        with pytest.raises(OpenGatewayError) as exc_info:
            self._call(svc)
        assert exc_info.value.code == "bad_request"

    def test_error_envelope_generic(self, svc, fake_http):
        fake_http.responses.append(
            {"error": {"message": "internal mishap", "type": "server_error"}}
        )
        with pytest.raises(OpenGatewayError) as exc_info:
            self._call(svc)
        assert exc_info.value.code == "api_error"

    def test_error_envelope_missing_fields(self, svc, fake_http):
        # error dict with no message / no type should still raise api_error,
        # not crash.
        fake_http.responses.append({"error": {}})
        with pytest.raises(OpenGatewayError) as exc_info:
            self._call(svc)
        assert exc_info.value.code == "api_error"

    def test_non_dict_error_field_ignored(self, svc, fake_http):
        # If `error` is a string (some upstreams return this), the
        # isinstance check fails and we return the response as-is.
        fake_http.responses.append({"error": "not a dict", "choices": [{"x": 1}]})
        result = self._call(svc)
        assert result["choices"] == [{"x": 1}]


class TestSingleton:
    def test_returns_same_instance(self):
        a = get_opengateway_service()
        b = get_opengateway_service()
        assert a is b
