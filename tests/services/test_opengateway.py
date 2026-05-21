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
    def test_authenticated(self, svc):
        h = svc.health()
        assert h["id"] == "clawmes.opengateway"
        assert h["status"] == "authenticated"
        assert h["default_model"] == "mimo-v2.5-pro"

    def test_unauthenticated(self):
        s = OpenGatewayService()
        s.start()
        h = s.health()
        assert h["status"] == "unauthenticated"
        assert h["default_model"] is None


class TestStartLogging:
    """Service must clearly signal whether it's running authenticated.

    The "partnership window allows unauth" policy means missing-key is a
    valid state — but it must be loud enough that users notice before
    gitlawb flips the auth wall on.

    The clawmes root logger has ``propagate=False`` so pytest's stock
    ``caplog`` doesn't see its records (see ``tests/services/test_rpc.py``
    for prior art). We attach a recorder handler directly.
    """

    @staticmethod
    def _capture_logs(monkeypatch):
        import logging

        records: list[logging.LogRecord] = []
        handler = logging.Handler()
        handler.emit = records.append  # type: ignore[assignment]
        clawmes_root = logging.getLogger("clawmes")
        clawmes_root.addHandler(handler)
        monkeypatch.setattr(
            handler,
            "_cleanup",
            lambda: clawmes_root.removeHandler(handler),
            raising=False,
        )
        return records

    def test_start_with_key_logs_info_not_warning(self, monkeypatch):
        import logging

        monkeypatch.setenv("OPENGATEWAY_API_KEY", "ogw_live_abc")
        records = self._capture_logs(monkeypatch)
        OpenGatewayService().start()
        msgs = [(r.levelno, r.getMessage()) for r in records]
        assert any(
            lvl == logging.INFO and "opengateway service started (auth=key" in m for lvl, m in msgs
        )
        assert not any(lvl >= logging.WARNING for lvl, _ in msgs)

    def test_start_without_key_logs_warning(self, monkeypatch):
        import logging

        records = self._capture_logs(monkeypatch)
        OpenGatewayService().start()
        msgs = [(r.levelno, r.getMessage()) for r in records]
        assert any(lvl == logging.WARNING and "UNAUTHENTICATED" in m for lvl, m in msgs)


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

    def test_unauth_call_sends_without_auth_header(self, monkeypatch, fake_http):
        """No API key means no Authorization header — but the call still goes out.

        Matches gitlawb's partnership-window behavior: anon traffic is
        accepted by the server today, will be 401'd after the auth flip.
        """
        monkeypatch.setenv("OPENGATEWAY_MODEL", "mimo-v2.5-pro")
        s = OpenGatewayService()
        s.start()
        fake_http.responses.append(
            {
                "id": "anon-call",
                "choices": [{"message": {"role": "assistant", "content": "hi"}}],
            }
        )
        result = s.chat_completion([{"role": "user", "content": "hi"}])
        assert result["id"] == "anon-call"
        headers = fake_http.calls[0]["headers"]
        assert "Authorization" not in headers
        assert headers["Content-Type"] == "application/json"

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

    def test_identity_encoding_header(self, svc, fake_http):
        """The Accept-Encoding: identity workaround for OpenGateway's
        broken gzip stream must be present on every request."""
        fake_http.responses.append(self._ok_response())
        svc.chat_completion([{"role": "user", "content": "hi"}])
        headers = fake_http.calls[0]["headers"]
        assert headers["Accept-Encoding"] == "identity"

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
        # "rate limit" substring, not just "rate" — the previous matcher
        # was too loose (any "rate" word would trigger).
        fake_http.responses.append(RuntimeError("upstream rate limit hit"))
        with pytest.raises(OpenGatewayError) as exc_info:
            self._call(svc)
        assert exc_info.value.code == "rate_limited"

    def test_unauthorized_via_401(self, svc, fake_http):
        # Once gitlawb flips the auth wall on, unauth callers see 401.
        fake_http.responses.append(RuntimeError("HTTP 401 Unauthorized"))
        with pytest.raises(OpenGatewayError) as exc_info:
            self._call(svc)
        assert exc_info.value.code == "no_credentials"

    def test_forbidden_via_403(self, svc, fake_http):
        fake_http.responses.append(RuntimeError("HTTP 403 Forbidden"))
        with pytest.raises(OpenGatewayError) as exc_info:
            self._call(svc)
        assert exc_info.value.code == "no_credentials"

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

    def test_error_envelope_rate_via_code(self, svc, fake_http):
        # OpenAI's canonical rate-limit code.
        fake_http.responses.append(
            {"error": {"message": "you have hit the limit", "code": "rate_limit_exceeded"}}
        )
        with pytest.raises(OpenGatewayError) as exc_info:
            self._call(svc)
        assert exc_info.value.code == "rate_limited"

    def test_error_envelope_unsupported_model(self, svc, fake_http):
        """Matches the real OpenGateway error captured from prod probe:
        ``{"error":{"message":"Unsupported model for unified routing: …",
        "type":"invalid_request_error","code":"unsupported_model"}}``."""
        fake_http.responses.append(
            {
                "error": {
                    "message": "Unsupported model for unified routing: foo",
                    "type": "invalid_request_error",
                    "code": "unsupported_model",
                }
            }
        )
        with pytest.raises(OpenGatewayError) as exc_info:
            self._call(svc)
        assert exc_info.value.code == "model_not_found"
        # The structured display string should include the code + message
        # so callers can actually tell what went wrong.
        assert "unsupported_model" in exc_info.value.message
        assert "Unsupported model" in exc_info.value.message

    def test_error_envelope_model_not_found_code(self, svc, fake_http):
        fake_http.responses.append(
            {"error": {"message": "no such model", "code": "model_not_found"}}
        )
        with pytest.raises(OpenGatewayError) as exc_info:
            self._call(svc)
        assert exc_info.value.code == "model_not_found"

    def test_error_envelope_auth_via_type(self, svc, fake_http):
        fake_http.responses.append(
            {
                "error": {
                    "message": "invalid bearer token",
                    "type": "authentication_error",
                }
            }
        )
        with pytest.raises(OpenGatewayError) as exc_info:
            self._call(svc)
        assert exc_info.value.code == "no_credentials"

    def test_error_envelope_permission_denied(self, svc, fake_http):
        fake_http.responses.append(
            {
                "error": {
                    "message": "you do not have access to this model",
                    "type": "permission_denied",
                }
            }
        )
        with pytest.raises(OpenGatewayError) as exc_info:
            self._call(svc)
        assert exc_info.value.code == "no_credentials"

    def test_error_envelope_invalid_request_model_via_message(self, svc, fake_http):
        """invalid_request_error with 'model … not found' phrasing but no
        ``code`` field — message inspection inside the invalid_request
        branch should still classify as model_not_found."""
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
        assert exc_info.value.code == "model_not_found"

    def test_error_envelope_invalid_request_plain(self, svc, fake_http):
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
        # error dict with no message / no type / no code should still
        # raise api_error, not crash.
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


class TestStructuredErrorBodyExtraction:
    """The real failure path: lib/http.http_post calls raise_for_status,
    which throws away the response body. The structured error envelope
    lives on the raised exception's ``.response`` attribute. The service
    duck-types past lib/http to recover it.
    """

    def _call(self, svc):
        return svc.chat_completion([{"role": "user", "content": "hi"}])

    def _raise_with_body(self, body):
        """Helper: build an exception with a ``.response`` that yields ``body`` from .json()."""

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

        return _FakeHTTPError("Client error '400 Bad Request' for url '...'", body)

    def test_pulls_structured_body_and_classifies(self, svc, fake_http):
        """An httpx.HTTPStatusError-shaped exception has the body on
        ``.response.json()``. The service should extract it and classify
        from ``error.code`` rather than falling back to substring."""
        exc = self._raise_with_body(
            {
                "error": {
                    "message": "Unsupported model for unified routing: foo",
                    "type": "invalid_request_error",
                    "code": "unsupported_model",
                }
            }
        )
        fake_http.responses.append(exc)
        with pytest.raises(OpenGatewayError) as exc_info:
            self._call(svc)
        # If substring matching were used, the exception text contains
        # "400 Bad Request" which would classify as bad_request. The
        # structured body wins → model_not_found.
        assert exc_info.value.code == "model_not_found"
        assert "Unsupported model" in exc_info.value.message

    def test_response_json_raises_falls_through_to_substring(self, svc, fake_http):
        # Body extraction failure (e.g. response was HTML, not JSON)
        # must fall through to the substring fallback, not crash.
        exc = self._raise_with_body(ValueError("not json"))
        # The exception message has "400" — substring fallback should
        # classify as bad_request.
        fake_http.responses.append(exc)
        with pytest.raises(OpenGatewayError) as exc_info:
            self._call(svc)
        assert exc_info.value.code == "bad_request"

    def test_response_json_returns_non_dict(self, svc, fake_http):
        # If .json() returns a list/string/None, we should fall through
        # to substring matching, not crash.
        exc = self._raise_with_body(["not", "a", "dict"])
        fake_http.responses.append(exc)
        with pytest.raises(OpenGatewayError) as exc_info:
            self._call(svc)
        assert exc_info.value.code == "bad_request"  # via "400" substring

    def test_response_json_dict_without_error_field(self, svc, fake_http):
        # JSON body but no "error" field — fall through.
        exc = self._raise_with_body({"detail": "something else"})
        fake_http.responses.append(exc)
        with pytest.raises(OpenGatewayError) as exc_info:
            self._call(svc)
        assert exc_info.value.code == "bad_request"  # via "400" substring

    def test_response_json_error_field_not_dict(self, svc, fake_http):
        # error field is a string — fall through.
        exc = self._raise_with_body({"error": "just a string"})
        fake_http.responses.append(exc)
        with pytest.raises(OpenGatewayError) as exc_info:
            self._call(svc)
        assert exc_info.value.code == "bad_request"  # via "400" substring


class TestSingleton:
    def test_returns_same_instance(self):
        a = get_opengateway_service()
        b = get_opengateway_service()
        assert a is b


class TestChatCompletionPremium:
    """chat_completion_premium gates on the Clawnch premium tier."""

    def test_denies_when_no_premium(self, svc, monkeypatch):
        from clawmes.services import clawnch_premium as cp_mod

        class _FakeSvc:
            def has_access(self, feature_id):
                return False

        monkeypatch.setattr(cp_mod, "_instance", None)
        monkeypatch.setattr(cp_mod, "get_clawnch_premium_service", lambda: _FakeSvc())
        out = svc.chat_completion_premium([{"role": "user", "content": "hi"}])
        assert out["isError"] is True
        assert "details" in out
        assert out["details"]["feature"] == "opengateway_high_tier"

    def test_grants_when_premium(self, svc, monkeypatch, fake_http):
        from clawmes.services import clawnch_premium as cp_mod

        class _FakeSvc:
            def has_access(self, feature_id):
                return True

        monkeypatch.setattr(cp_mod, "_instance", None)
        monkeypatch.setattr(cp_mod, "get_clawnch_premium_service", lambda: _FakeSvc())
        fake_http.responses.append({"choices": [{"message": {"content": "ok"}}]})
        out = svc.chat_completion_premium(
            [{"role": "user", "content": "hi"}],
            model="some-premium-model",
        )
        assert out["choices"][0]["message"]["content"] == "ok"
