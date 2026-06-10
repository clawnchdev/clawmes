"""Tests for clawmes.lib.inference (provider router)."""

from __future__ import annotations

from typing import Any

import pytest

from clawmes.lib.inference import InferenceError, chat_completion, resolve_provider


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("CLAWMES_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("VENICE_API_KEY", raising=False)


class _Fake:
    def __init__(self, response=None, raises=None):
        self.response = response
        self.raises = raises
        self.calls: list[dict[str, Any]] = []

    def chat_completion(self, messages, **kw):
        self.calls.append({"messages": messages, "kw": kw})
        if self.raises is not None:
            raise self.raises
        return self.response


def _patch_opengateway(monkeypatch, fake):
    import clawmes.services.opengateway as og

    monkeypatch.setattr(og, "get_opengateway_service", lambda: fake)


def _patch_venice(monkeypatch, fake):
    import clawmes.services.venice as v

    monkeypatch.setattr(v, "get_venice_service", lambda: fake)


class TestResolveProvider:
    def test_explicit_venice(self, monkeypatch):
        monkeypatch.setenv("CLAWMES_LLM_PROVIDER", "venice")
        assert resolve_provider() == "venice"

    def test_explicit_opengateway_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("CLAWMES_LLM_PROVIDER", "OpenGateway")
        assert resolve_provider() == "opengateway"

    def test_invalid_choice_falls_to_auto(self, monkeypatch):
        monkeypatch.setenv("CLAWMES_LLM_PROVIDER", "bogus")
        assert resolve_provider() == "opengateway"  # no VENICE_API_KEY

    def test_auto_venice_when_key_set(self, monkeypatch):
        monkeypatch.setenv("VENICE_API_KEY", "venice_k")
        assert resolve_provider() == "venice"

    def test_auto_opengateway_default(self):
        assert resolve_provider() == "opengateway"


class TestChatCompletion:
    def test_opengateway_success(self, monkeypatch):
        fake = _Fake(response={"choices": [{"message": {"content": "ok"}}]})
        _patch_opengateway(monkeypatch, fake)
        r = chat_completion([{"role": "user", "content": "hi"}], temperature=0.2)
        assert r["choices"][0]["message"]["content"] == "ok"
        # model defaults to None (let the provider use its env default).
        assert fake.calls[0]["kw"]["model"] is None
        assert fake.calls[0]["kw"]["temperature"] == 0.2

    def test_opengateway_error_translated(self, monkeypatch):
        from clawmes.services.opengateway import OpenGatewayError

        _patch_opengateway(monkeypatch, _Fake(raises=OpenGatewayError("rate_limited", "slow down")))
        with pytest.raises(InferenceError) as exc:
            chat_completion([{"role": "user", "content": "hi"}])
        assert exc.value.code == "rate_limited"
        assert exc.value.provider == "opengateway"
        assert "slow down" in exc.value.message

    def test_venice_success(self, monkeypatch):
        monkeypatch.setenv("CLAWMES_LLM_PROVIDER", "venice")
        fake = _Fake(response={"choices": [{"message": {"content": "vv"}}]})
        _patch_venice(monkeypatch, fake)
        r = chat_completion([{"role": "user", "content": "hi"}], model="venice-uncensored")
        assert r["choices"][0]["message"]["content"] == "vv"
        assert fake.calls[0]["kw"]["model"] == "venice-uncensored"

    def test_venice_error_translated(self, monkeypatch):
        monkeypatch.setenv("CLAWMES_LLM_PROVIDER", "venice")
        from clawmes.services.venice import VeniceError

        _patch_venice(monkeypatch, _Fake(raises=VeniceError("payment_required", "add credits")))
        with pytest.raises(InferenceError) as exc:
            chat_completion([{"role": "user", "content": "hi"}])
        assert exc.value.code == "payment_required"
        assert exc.value.provider == "venice"
        assert "add credits" in exc.value.message


class TestInferenceError:
    def test_attrs(self):
        e = InferenceError("bad_request", "nope", provider="venice")
        assert e.code == "bad_request"
        assert e.message == "nope"
        assert e.provider == "venice"
        assert str(e) == "nope"

    def test_default_provider(self):
        assert InferenceError("api_error", "x").provider == ""
