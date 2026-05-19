"""Tests for clawmes.services.onboarding_service."""

from __future__ import annotations

import pytest

from clawmes.services import onboarding_service as ob_module
from clawmes.services import persona_service as persona_module
from clawmes.services.onboarding_service import (
    CAPABILITIES,
    OnboardingService,
    get_onboarding_service,
)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(ob_module, "_instance", None)
    monkeypatch.setattr(persona_module, "_instance", None)


@pytest.fixture
def svc():
    return OnboardingService()


class TestLifecycle:
    def test_start_is_noop(self, svc):
        # Must not raise; pure in-memory service.
        svc.start()

    def test_stop_clears_state(self, svc):
        svc.set_capability("trading", True)
        svc.advance_step("pick_persona")
        svc.stop()
        # After stop, get_state should create a fresh blank entry.
        state = svc.get_state()
        assert state.step == "welcome"
        assert svc.get_capabilities() == frozenset()


class TestGetState:
    def test_creates_on_first_access(self, svc):
        state = svc.get_state()
        assert state.sender_id == "default"
        assert state.step == "welcome"
        assert not state.complete

    def test_returns_same_instance_on_repeat_access(self, svc):
        a = svc.get_state()
        b = svc.get_state()
        assert a is b

    def test_per_sender_isolation(self, svc):
        a = svc.get_state("alice")
        b = svc.get_state("bob")
        assert a is not b
        assert a.sender_id == "alice"
        assert b.sender_id == "bob"


class TestCapabilities:
    def test_set_enable(self, svc):
        result = svc.set_capability("trading", True)
        assert result is True
        assert "trading" in svc.get_capabilities()

    def test_set_disable_when_already_off(self, svc):
        result = svc.set_capability("trading", False)
        assert result is False
        assert "trading" not in svc.get_capabilities()

    def test_set_disable_after_enable(self, svc):
        svc.set_capability("trading", True)
        result = svc.set_capability("trading", False)
        assert result is False

    def test_unknown_capability_raises(self, svc):
        with pytest.raises(ValueError, match="unknown capability"):
            svc.set_capability("not-a-real-cap", True)

    def test_toggle_off_to_on(self, svc):
        assert svc.toggle_capability("prices") is True

    def test_toggle_on_to_off(self, svc):
        svc.set_capability("prices", True)
        assert svc.toggle_capability("prices") is False

    def test_get_capabilities_returns_frozenset(self, svc):
        svc.set_capability("trading", True)
        caps = svc.get_capabilities()
        assert isinstance(caps, frozenset)
        # Should be a snapshot — mutating the underlying state shouldn't
        # change the returned set.
        svc.set_capability("prices", True)
        assert "prices" not in caps

    def test_per_sender_capability_isolation(self, svc):
        svc.set_capability("trading", True, sender_id="alice")
        assert svc.get_capabilities(sender_id="alice") == frozenset({"trading"})
        assert svc.get_capabilities(sender_id="bob") == frozenset()

    def test_capabilities_module_constant(self):
        # Sanity check the public constant — the slash-command layer
        # iterates over this directly.
        ids = [cap_id for cap_id, _ in CAPABILITIES]
        assert "wallet" in ids
        assert "trading" in ids
        assert len(ids) == 10
        # Every ID must be unique.
        assert len(set(ids)) == 10


class TestStepTransitions:
    def test_advance_step_pushes_history(self, svc):
        svc.advance_step("pick_persona")
        svc.advance_step("pick_wallet")
        state = svc.back()
        assert state is not None
        assert state.step == "pick_persona"

    def test_skip_from_welcome(self, svc):
        state = svc.skip()
        assert state.step == "pick_persona"

    def test_skip_from_pick_persona(self, svc):
        svc.advance_step("pick_persona")
        state = svc.skip()
        assert state.step == "pick_wallet"

    def test_skip_from_pick_wallet(self, svc):
        svc.advance_step("pick_wallet")
        state = svc.skip()
        assert state.step == "pick_chain"

    def test_skip_from_pick_chain_to_done(self, svc):
        svc.advance_step("pick_chain")
        state = svc.skip()
        assert state.step == "done"
        assert state.complete

    def test_skip_at_done_is_noop(self, svc):
        svc.advance_step("done")
        state = svc.skip()
        assert state.step == "done"
        assert state.complete

    def test_skip_from_unknown_step_falls_through_to_done(self, svc):
        state = svc.get_state()
        # Force an unknown step that isn't in the canonical sequence.
        state.step = "totally-not-a-real-step"  # type: ignore[assignment]
        result = svc.skip()
        assert result.step == "done"
        assert result.complete


class TestBack:
    def test_back_with_empty_history(self, svc):
        assert svc.back() is None

    def test_back_pops_history(self, svc):
        svc.advance_step("pick_persona")
        result = svc.back()
        assert result is not None
        assert result.step == "welcome"
        assert result.complete is False

    def test_back_from_done_unsets_complete(self, svc):
        svc.advance_step("done")
        assert svc.get_state().complete
        # We are at "done" now. Push another step on top of it via advance_step,
        # then back, and we should be back at "done" → complete True.
        svc.advance_step("welcome")
        result = svc.back()
        assert result is not None
        assert result.step == "done"
        assert result.complete is True

    def test_back_to_welcome_clears_complete(self, svc):
        svc.advance_step("done")
        result = svc.back()
        assert result is not None
        # We went done → welcome (since welcome was on the history stack).
        assert result.step == "welcome"
        assert result.complete is False


class TestReonboard:
    def test_resets_state_capabilities_and_history(self, svc):
        svc.set_capability("trading", True)
        svc.advance_step("pick_persona")
        svc.advance_step("pick_wallet")

        fresh = svc.reonboard()
        assert fresh.step == "welcome"
        assert not fresh.complete
        assert svc.get_capabilities() == frozenset()
        # History should be cleared — back should now return None.
        assert svc.back() is None

    def test_clears_active_persona(self, svc):
        persona_svc = persona_module.get_persona_service()
        persona_svc.set_persona("degen")
        assert persona_svc.active_name == "degen"

        svc.reonboard()
        assert persona_svc.active_name is None


class TestSingleton:
    def test_singleton(self):
        a = get_onboarding_service()
        b = get_onboarding_service()
        assert a is b
