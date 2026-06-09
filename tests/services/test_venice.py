"""Tests for clawmes.services.venice."""

from __future__ import annotations

import pytest

from clawmes.services import venice as venice_module
from clawmes.services.venice import (
    VeniceError,
    VeniceService,
    get_venice_service,
)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(venice_module, "_instance", None)
    monkeypatch.delenv("VENICE_API_KEY", raising=False)
    monkeypatch.delenv("VENICE_MODEL", raising=False)


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
    monkeypatch.setattr(venice_module, "http_post", fake)
    return fake


@pytest.fixture
def svc(monkeypatch):
    monkeypatch.setenv("VENICE_API_KEY", "venice_test_key")
    monkeypatch.setenv("VENICE_MODEL", "zai-org-glm-4.6")
    s = VeniceService()
    s.start()
    return s


def _err_with_body(msg, body):
    """Build an httpx-style exception with ``.response.json()`` -> ``body``."""

    class _FakeResponse:
        def __init__(self, body):
            self._body = body

        def json(self):
            if isinstance(self._body, Exception):
                raise self._body
            return self._body

    class _FakeHTTPError(RuntimeError):
        def __init__(self, msg, body):
            super().__init__(msg)
            self.response = _FakeResponse(body)

    return _FakeHTTPError(msg, body)


class TestStartStop:
    def test_start_no_key_or_model(self):
        s = VeniceService()
        s.start()
        assert s._api_key is None
        assert s._default_model is None

    def test_start_with_key(self, monkeypatch):
        monkeypatch.setenv("VENICE_API_KEY", "venice_abc")
        s = VeniceService()
        s.start()
        assert s._api_key == "venice_abc"

    def test_start_with_model(self, monkeypatch):
        monkeypatch.setenv("VENICE_MODEL", "zai-org-glm-5")
        s = VeniceService()
        s.start()
        assert s._default_model == "zai-org-glm-5"

    def test_start_empty_string_normalized_to_none(self, monkeypatch):
        monkeypatch.setenv("VENICE_API_KEY", "")
        monkeypatch.setenv("VENICE_MODEL", "")
        s = VeniceService()
        s.start()
        assert s._api_key is None
        assert s._default_model is None

    def test_stop_clears_state(self, svc):
        svc.stop()
        assert svc._api_key is None
        assert svc._default_model is None


class TestHealth:
    def test_authenticated(self, svc):
        h = svc.health()
        assert h["id"] == "clawmes.venice"
        assert h["status"] == "authenticated"
        assert h["default_model"] == "zai-org-glm-4.6"

    def test_unauthenticated(self):
        s = VeniceService()
        s.start()
        h = s.health()
        assert h["status"] == "unauthenticated"
        assert h["default_model"] is None


class TestChatCompletionValidation:
    def test_empty_messages(self, svc):
        with pytest.raises(VeniceError) as exc_info:
            svc.chat_completion([])
        assert exc_info.value.code == "bad_request"
        assert "non-empty" in exc_info.value.message

    def test_streaming_rejected(self, svc):
        with pytest.raises(VeniceError) as exc_info:
            svc.chat_completion([{"role": "user", "content": "hi"}], stream=True)
        assert exc_info.value.code == "bad_request"
        assert "streaming" in exc_info.value.message

    def test_no_model_anywhere(self, monkeypatch):
        monkeypatch.setenv("VENICE_API_KEY", "venice_abc")
        s = VeniceService()
        s.start()
        with pytest.raises(VeniceError) as exc_info:
            s.chat_completion([{"role": "user", "content": "hi"}])
        assert exc_info.value.code == "bad_request"
        assert "model" in exc_info.value.message


class TestChatCompletionRequest:
    def _ok(self):
        return {
            "id": "chatcmpl-v",
            "object": "chat.completion",
            "model": "zai-org-glm-4.6",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "hello"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
        }

    def test_basic_call_uses_env_default_model(self, svc, fake_http):
        fake_http.responses.append(self._ok())
        result = svc.chat_completion([{"role": "user", "content": "hi"}])
        assert result["choices"][0]["message"]["content"] == "hello"
        sent = fake_http.calls[0]["json"]
        assert sent["model"] == "zai-org-glm-4.6"
        assert sent["messages"] == [{"role": "user", "content": "hi"}]

    def test_model_arg_overrides_default(self, svc, fake_http):
        fake_http.responses.append(self._ok())
        svc.chat_completion([{"role": "user", "content": "hi"}], model="other-model")
        assert fake_http.calls[0]["json"]["model"] == "other-model"

    def test_model_arg_only_no_default(self, monkeypatch, fake_http):
        monkeypatch.setenv("VENICE_API_KEY", "venice_abc")
        s = VeniceService()
        s.start()
        fake_http.responses.append(self._ok())
        s.chat_completion([{"role": "user", "content": "hi"}], model="explicit-model")
        assert fake_http.calls[0]["json"]["model"] == "explicit-model"

    def test_temperature_passed(self, svc, fake_http):
        fake_http.responses.append(self._ok())
        svc.chat_completion([{"role": "user", "content": "hi"}], temperature=0.2)
        assert fake_http.calls[0]["json"]["temperature"] == 0.2

    def test_temperature_omitted_when_none(self, svc, fake_http):
        fake_http.responses.append(self._ok())
        svc.chat_completion([{"role": "user", "content": "hi"}])
        assert "temperature" not in fake_http.calls[0]["json"]

    def test_max_tokens_passed(self, svc, fake_http):
        fake_http.responses.append(self._ok())
        svc.chat_completion([{"role": "user", "content": "hi"}], max_tokens=128)
        assert fake_http.calls[0]["json"]["max_tokens"] == 128

    def test_max_tokens_omitted_when_none(self, svc, fake_http):
        fake_http.responses.append(self._ok())
        svc.chat_completion([{"role": "user", "content": "hi"}])
        assert "max_tokens" not in fake_http.calls[0]["json"]

    def test_extra_kwargs_passed_through(self, svc, fake_http):
        fake_http.responses.append(self._ok())
        svc.chat_completion(
            [{"role": "user", "content": "hi"}],
            top_p=0.9,
            venice_parameters={"include_venice_system_prompt": False},
        )
        sent = fake_http.calls[0]["json"]
        assert sent["top_p"] == 0.9
        assert sent["venice_parameters"] == {"include_venice_system_prompt": False}

    def test_bearer_auth_header(self, svc, fake_http):
        fake_http.responses.append(self._ok())
        svc.chat_completion([{"role": "user", "content": "hi"}])
        headers = fake_http.calls[0]["headers"]
        assert headers["Authorization"] == "Bearer venice_test_key"
        assert headers["Content-Type"] == "application/json"

    def test_unauth_call_sends_without_auth_header(self, monkeypatch, fake_http):
        monkeypatch.setenv("VENICE_MODEL", "zai-org-glm-4.6")
        s = VeniceService()
        s.start()
        fake_http.responses.append(self._ok())
        s.chat_completion([{"role": "user", "content": "hi"}])
        assert "Authorization" not in fake_http.calls[0]["headers"]

    def test_request_url(self, svc, fake_http):
        fake_http.responses.append(self._ok())
        svc.chat_completion([{"role": "user", "content": "hi"}])
        assert fake_http.calls[0]["url"] == "https://api.venice.ai/api/v1/chat/completions"

    def test_custom_timeout(self, svc, fake_http):
        fake_http.responses.append(self._ok())
        svc.chat_completion([{"role": "user", "content": "hi"}], timeout=5.0)
        assert fake_http.calls[0]["timeout"] == 5.0

    def test_default_timeout(self, svc, fake_http):
        fake_http.responses.append(self._ok())
        svc.chat_completion([{"role": "user", "content": "hi"}])
        assert fake_http.calls[0]["timeout"] == 60.0


class TestErrorClassificationSubstring:
    def _call(self, svc):
        return svc.chat_completion([{"role": "user", "content": "hi"}])

    def test_payment_required_via_402(self, svc, fake_http):
        # Venice's x402 unauthenticated response.
        fake_http.responses.append(RuntimeError("Client error '402 Payment Required' for url"))
        with pytest.raises(VeniceError) as exc_info:
            self._call(svc)
        assert exc_info.value.code == "no_credentials"

    def test_unauthorized_via_401(self, svc, fake_http):
        fake_http.responses.append(RuntimeError("HTTP 401 Unauthorized"))
        with pytest.raises(VeniceError) as exc_info:
            self._call(svc)
        assert exc_info.value.code == "no_credentials"

    def test_forbidden_via_403(self, svc, fake_http):
        fake_http.responses.append(RuntimeError("HTTP 403 Forbidden"))
        with pytest.raises(VeniceError) as exc_info:
            self._call(svc)
        assert exc_info.value.code == "no_credentials"

    def test_no_credentials_via_keyword(self, svc, fake_http):
        fake_http.responses.append(RuntimeError("authentication required"))
        with pytest.raises(VeniceError) as exc_info:
            self._call(svc)
        assert exc_info.value.code == "no_credentials"

    def test_rate_limit_via_429(self, svc, fake_http):
        fake_http.responses.append(RuntimeError("HTTP 429 Too Many Requests"))
        with pytest.raises(VeniceError) as exc_info:
            self._call(svc)
        assert exc_info.value.code == "rate_limited"

    def test_rate_limit_via_keyword(self, svc, fake_http):
        fake_http.responses.append(RuntimeError("upstream rate limit hit"))
        with pytest.raises(VeniceError) as exc_info:
            self._call(svc)
        assert exc_info.value.code == "rate_limited"

    def test_model_not_found_via_404(self, svc, fake_http):
        fake_http.responses.append(RuntimeError("HTTP 404 Not Found"))
        with pytest.raises(VeniceError) as exc_info:
            self._call(svc)
        assert exc_info.value.code == "model_not_found"

    def test_model_not_found_via_keyword(self, svc, fake_http):
        fake_http.responses.append(RuntimeError("the model 'xyz' was not found"))
        with pytest.raises(VeniceError) as exc_info:
            self._call(svc)
        assert exc_info.value.code == "model_not_found"

    def test_bad_request_via_400(self, svc, fake_http):
        fake_http.responses.append(RuntimeError("HTTP 400 Bad Request: bad params"))
        with pytest.raises(VeniceError) as exc_info:
            self._call(svc)
        assert exc_info.value.code == "bad_request"

    def test_generic_failure(self, svc, fake_http):
        fake_http.responses.append(RuntimeError("connection reset"))
        with pytest.raises(VeniceError) as exc_info:
            self._call(svc)
        assert exc_info.value.code == "api_error"

    def test_non_dict_response(self, svc, fake_http):
        fake_http.responses.append("not a dict")
        with pytest.raises(VeniceError) as exc_info:
            self._call(svc)
        assert exc_info.value.code == "api_error"


class TestErrorEnvelope:
    """2xx response carrying an OpenAI-style error dict in the body."""

    def _call(self, svc):
        return svc.chat_completion([{"role": "user", "content": "hi"}])

    def test_rate_via_type(self, svc, fake_http):
        fake_http.responses.append({"error": {"message": "limit", "type": "rate_limit_error"}})
        with pytest.raises(VeniceError) as exc_info:
            self._call(svc)
        assert exc_info.value.code == "rate_limited"

    def test_rate_via_code(self, svc, fake_http):
        fake_http.responses.append({"error": {"message": "limit", "code": "rate_limit_exceeded"}})
        with pytest.raises(VeniceError) as exc_info:
            self._call(svc)
        assert exc_info.value.code == "rate_limited"

    def test_unsupported_model(self, svc, fake_http):
        fake_http.responses.append(
            {
                "error": {
                    "message": "Unsupported model: foo",
                    "type": "invalid_request_error",
                    "code": "unsupported_model",
                }
            }
        )
        with pytest.raises(VeniceError) as exc_info:
            self._call(svc)
        assert exc_info.value.code == "model_not_found"
        assert "unsupported_model" in exc_info.value.message
        assert "Unsupported model" in exc_info.value.message

    def test_model_not_found_code(self, svc, fake_http):
        fake_http.responses.append(
            {"error": {"message": "no such model", "code": "model_not_found"}}
        )
        with pytest.raises(VeniceError) as exc_info:
            self._call(svc)
        assert exc_info.value.code == "model_not_found"

    def test_auth_via_type(self, svc, fake_http):
        fake_http.responses.append(
            {"error": {"message": "bad token", "type": "authentication_error"}}
        )
        with pytest.raises(VeniceError) as exc_info:
            self._call(svc)
        assert exc_info.value.code == "no_credentials"

    def test_permission_denied(self, svc, fake_http):
        fake_http.responses.append({"error": {"message": "no access", "type": "permission_denied"}})
        with pytest.raises(VeniceError) as exc_info:
            self._call(svc)
        assert exc_info.value.code == "no_credentials"

    def test_invalid_request_model_via_message(self, svc, fake_http):
        fake_http.responses.append(
            {
                "error": {
                    "message": "The model 'gpt-7' was not found",
                    "type": "invalid_request_error",
                }
            }
        )
        with pytest.raises(VeniceError) as exc_info:
            self._call(svc)
        assert exc_info.value.code == "model_not_found"

    def test_invalid_request_plain(self, svc, fake_http):
        fake_http.responses.append(
            {"error": {"message": "invalid temperature", "type": "invalid_request_error"}}
        )
        with pytest.raises(VeniceError) as exc_info:
            self._call(svc)
        assert exc_info.value.code == "bad_request"

    def test_generic_envelope(self, svc, fake_http):
        fake_http.responses.append({"error": {"message": "mishap", "type": "server_error"}})
        with pytest.raises(VeniceError) as exc_info:
            self._call(svc)
        assert exc_info.value.code == "api_error"

    def test_missing_fields(self, svc, fake_http):
        fake_http.responses.append({"error": {}})
        with pytest.raises(VeniceError) as exc_info:
            self._call(svc)
        assert exc_info.value.code == "api_error"

    def test_non_dict_error_field_ignored(self, svc, fake_http):
        # `error` is a string in a 2xx body -> returned as-is, not raised.
        fake_http.responses.append({"error": "not a dict", "choices": [{"x": 1}]})
        result = self._call(svc)
        assert result["choices"] == [{"x": 1}]


class TestStructuredErrorBodyExtraction:
    """Recover the error envelope from a raised exception's ``.response``."""

    def _call(self, svc):
        return svc.chat_completion([{"role": "user", "content": "hi"}])

    def test_pulls_structured_body_and_classifies(self, svc, fake_http):
        exc = _err_with_body(
            "Client error '400 Bad Request' for url",
            {
                "error": {
                    "message": "Unsupported model: foo",
                    "type": "invalid_request_error",
                    "code": "unsupported_model",
                }
            },
        )
        fake_http.responses.append(exc)
        with pytest.raises(VeniceError) as exc_info:
            self._call(svc)
        # Structured body (model_not_found) beats the "400" substring (bad_request).
        assert exc_info.value.code == "model_not_found"
        assert "Unsupported model" in exc_info.value.message

    def test_venice_flat_error_402(self, svc, fake_http):
        # Venice's real unauth body: flat {"error": "Authentication required"} + 402.
        exc = _err_with_body(
            "Client error '402 Payment Required' for url",
            {"x402Version": 2, "error": "Authentication required"},
        )
        fake_http.responses.append(exc)
        with pytest.raises(VeniceError) as exc_info:
            self._call(svc)
        assert exc_info.value.code == "no_credentials"
        # The flat error string is surfaced as the detail.
        assert exc_info.value.message == "Authentication required"

    def test_response_json_raises_falls_through_to_substring(self, svc, fake_http):
        exc = _err_with_body("Client error '400 Bad Request' for url", ValueError("not json"))
        fake_http.responses.append(exc)
        with pytest.raises(VeniceError) as exc_info:
            self._call(svc)
        assert exc_info.value.code == "bad_request"

    def test_response_json_returns_non_dict(self, svc, fake_http):
        exc = _err_with_body("Client error '400 Bad Request' for url", ["not", "a", "dict"])
        fake_http.responses.append(exc)
        with pytest.raises(VeniceError) as exc_info:
            self._call(svc)
        assert exc_info.value.code == "bad_request"

    def test_response_json_dict_without_error_field(self, svc, fake_http):
        exc = _err_with_body("Client error '400 Bad Request' for url", {"detail": "something else"})
        fake_http.responses.append(exc)
        with pytest.raises(VeniceError) as exc_info:
            self._call(svc)
        assert exc_info.value.code == "bad_request"


class TestSingleton:
    def test_returns_same_instance(self):
        a = get_venice_service()
        b = get_venice_service()
        assert a is b
