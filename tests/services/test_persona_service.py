"""Tests for clawmes.services.persona_service."""

from __future__ import annotations

import pytest

from clawmes.services import persona_service as ps_module
from clawmes.services.persona_service import (
    PersonaService,
    get_persona_service,
)


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(ps_module, "_instance", None)


class TestSetPersona:
    def test_set_known(self):
        svc = PersonaService()
        result = svc.set_persona("degen")
        assert result is not None
        assert result.name == "degen"
        assert svc.active_name == "degen"

    def test_set_unknown_returns_none(self):
        svc = PersonaService()
        result = svc.set_persona("nonexistent")
        assert result is None
        assert svc.active_name is None

    def test_set_case_insensitive(self):
        svc = PersonaService()
        svc.set_persona("CHILL")
        assert svc.active_name == "chill"

    def test_set_none_clears(self):
        svc = PersonaService()
        svc.set_persona("technical")
        svc.set_persona(None)
        assert svc.active_name is None

    def test_set_empty_string_clears(self):
        svc = PersonaService()
        svc.set_persona("mentor")
        svc.set_persona("")
        assert svc.active_name is None

    def test_set_whitespace_clears(self):
        svc = PersonaService()
        svc.set_persona("professional")
        svc.set_persona("   ")
        assert svc.active_name is None


class TestActivePersona:
    def test_default_none(self):
        svc = PersonaService()
        assert svc.active_persona() is None

    def test_after_set(self):
        svc = PersonaService()
        svc.set_persona("degen")
        p = svc.active_persona()
        assert p is not None
        assert p.name == "degen"


class TestActiveSnippet:
    def test_no_persona_returns_empty(self):
        svc = PersonaService()
        assert svc.active_snippet() == ""

    def test_returns_persona_snippet_text(self):
        svc = PersonaService()
        svc.set_persona("degen")
        snippet = svc.active_snippet()
        assert isinstance(snippet, str)
        assert "degen" in snippet.lower() or "ct" in snippet.lower()


class TestLifecycle:
    def test_start(self):
        svc = PersonaService()
        svc.start()  # no-op

    def test_stop_clears_active(self):
        svc = PersonaService()
        svc.set_persona("technical")
        svc.stop()
        assert svc.active_name is None


class TestSingleton:
    def test_returns_same_instance(self):
        a = get_persona_service()
        b = get_persona_service()
        assert a is b
