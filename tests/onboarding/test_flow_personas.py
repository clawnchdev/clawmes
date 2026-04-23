"""Tests for clawmes.onboarding.flow and personas."""

from __future__ import annotations

from clawmes.onboarding.flow import OnboardingState
from clawmes.onboarding.personas import PERSONAS, get_persona


class TestOnboardingState:
    def test_default_step(self):
        s = OnboardingState(sender_id="user-1")
        assert s.step == "welcome"
        assert s.complete is False

    def test_advance_to(self):
        s = OnboardingState(sender_id="user-1")
        s.advance_to("pick_persona")
        assert s.step == "pick_persona"
        assert s.complete is False

    def test_advance_to_done_marks_complete(self):
        s = OnboardingState(sender_id="user-1")
        s.advance_to("done")
        assert s.complete is True
        assert s.step == "done"


class TestPersonas:
    def test_get_known(self):
        p = get_persona("professional")
        assert p is not None
        assert p.name == "professional"

    def test_get_case_insensitive(self):
        assert get_persona("DEGEN") is not None
        assert get_persona("Chill") is not None

    def test_get_unknown_returns_none(self):
        assert get_persona("nonsense") is None

    def test_get_none_returns_none(self):
        assert get_persona(None) is None

    def test_get_empty_returns_none(self):
        assert get_persona("") is None

    def test_load_snippet_present(self):
        # All 5 bundled personas should have a non-empty snippet
        for name in ["professional", "degen", "chill", "technical", "mentor"]:
            p = get_persona(name)
            content = p.load_snippet()
            assert isinstance(content, str)
            assert len(content) > 0

    def test_load_snippet_missing_returns_empty(self, tmp_path, monkeypatch):
        # Construct a Persona pointing at a non-existent file; verify
        # load_snippet returns "" without raising.
        from clawmes.onboarding.personas import Persona

        p = Persona(
            name="ghost",
            tagline="missing",
            snippet_path=tmp_path / "no-such.md",
        )
        assert p.load_snippet() == ""

    def test_personas_dict_exposes_five_built_ins(self):
        assert set(PERSONAS.keys()) == {
            "professional",
            "degen",
            "chill",
            "technical",
            "mentor",
        }
