"""Tests for clawmes.commands.onboarding (slash commands)."""

from __future__ import annotations

import pytest

from clawmes.commands import onboarding as onboarding_cmd
from clawmes.services import onboarding_service as ob_module
from clawmes.services import persona_service as persona_module


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(ob_module, "_instance", None)
    monkeypatch.setattr(persona_module, "_instance", None)


# --- /welcome -----------------------------------------------------------


class TestHandleWelcome:
    async def test_initial_state(self):
        out = await onboarding_cmd.handle_welcome("")
        assert "Step:         welcome" in out
        assert "Persona:      (not set)" in out
        assert "Capabilities: (none selected)" in out
        assert "Complete:     False" in out
        # Helpful nav hints are surfaced.
        assert "/professional" in out
        assert "/cap_" in out
        assert "/skip" in out

    async def test_after_persona_and_caps_set(self):
        persona_module.get_persona_service().set_persona("degen")
        ob_module.get_onboarding_service().set_capability("trading", True)
        ob_module.get_onboarding_service().set_capability("prices", True)

        out = await onboarding_cmd.handle_welcome("")
        assert "Persona:      degen" in out
        # Capabilities are sorted in the summary.
        assert "Capabilities: prices, trading" in out


# --- persona switches ---------------------------------------------------


class TestPersonaHandlers:
    @pytest.mark.parametrize(
        "name",
        ["professional", "degen", "chill", "technical", "mentor"],
    )
    async def test_sets_persona(self, name):
        handler = onboarding_cmd._make_persona_handler(name)
        out = await handler("")
        assert f"Persona set to {name}" in out
        assert persona_module.get_persona_service().active_name == name

    async def test_advances_step_from_welcome(self):
        handler = onboarding_cmd._make_persona_handler("degen")
        await handler("")
        state = ob_module.get_onboarding_service().get_state()
        # welcome → pick_wallet (skipping pick_persona since the user already chose).
        assert state.step == "pick_wallet"

    async def test_advances_step_from_pick_persona(self):
        ob_module.get_onboarding_service().advance_step("pick_persona")
        handler = onboarding_cmd._make_persona_handler("degen")
        await handler("")
        assert ob_module.get_onboarding_service().get_state().step == "pick_wallet"

    async def test_does_not_advance_when_past_persona_step(self):
        ob_module.get_onboarding_service().advance_step("pick_wallet")
        handler = onboarding_cmd._make_persona_handler("degen")
        await handler("")
        state = ob_module.get_onboarding_service().get_state()
        # Already past pick_persona — stays at pick_wallet.
        assert state.step == "pick_wallet"

    async def test_handler_persona_load_failure(self, monkeypatch):
        # Force persona_service.set_persona to return None (simulating
        # a removed snippet or registry corruption).
        monkeypatch.setattr(
            persona_module.PersonaService,
            "set_persona",
            lambda self, name: None,
        )
        handler = onboarding_cmd._make_persona_handler("degen")
        out = await handler("")
        assert "Failed to load 'degen' persona" in out

    async def test_records_chosen_persona_on_state(self):
        handler = onboarding_cmd._make_persona_handler("technical")
        await handler("")
        assert ob_module.get_onboarding_service().get_state().chosen_persona == "technical"


# --- capability toggles -------------------------------------------------


class TestCapabilityHandlers:
    async def test_toggle_off_to_on(self):
        handler = onboarding_cmd._make_capability_handler("trading")
        out = await handler("")
        assert "'trading' enabled" in out

    async def test_toggle_on_to_off(self):
        ob_module.get_onboarding_service().set_capability("trading", True)
        handler = onboarding_cmd._make_capability_handler("trading")
        out = await handler("")
        assert "'trading' disabled" in out

    @pytest.mark.parametrize("arg", ["on", "enable", "true", "yes", "1"])
    async def test_explicit_on(self, arg):
        handler = onboarding_cmd._make_capability_handler("trading")
        out = await handler(arg)
        assert "enabled" in out
        assert "trading" in ob_module.get_onboarding_service().get_capabilities()

    @pytest.mark.parametrize("arg", ["off", "disable", "false", "no", "0"])
    async def test_explicit_off(self, arg):
        ob_module.get_onboarding_service().set_capability("trading", True)
        handler = onboarding_cmd._make_capability_handler("trading")
        out = await handler(arg)
        assert "disabled" in out
        assert "trading" not in ob_module.get_onboarding_service().get_capabilities()

    async def test_unknown_arg_returns_error(self):
        handler = onboarding_cmd._make_capability_handler("trading")
        out = await handler("maybe-later")
        assert "Capability error" in out
        assert "maybe-later" in out

    async def test_all_capability_handlers_match_constant(self):
        # Every capability in the public CAPABILITIES tuple should
        # produce a working handler.
        for cap_id, _label in ob_module.CAPABILITIES:
            handler = onboarding_cmd._make_capability_handler(cap_id)
            out = await handler("on")
            assert f"'{cap_id}' enabled" in out


class TestParseToggle:
    def test_empty_returns_none(self):
        assert onboarding_cmd._parse_toggle("") is None
        assert onboarding_cmd._parse_toggle("   ") is None

    def test_on_tokens(self):
        for token in ("on", "enable", "enabled", "true", "yes", "y", "1"):
            assert onboarding_cmd._parse_toggle(token) is True
            assert onboarding_cmd._parse_toggle(token.upper()) is True

    def test_off_tokens(self):
        for token in ("off", "disable", "disabled", "false", "no", "n", "0"):
            assert onboarding_cmd._parse_toggle(token) is False

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown toggle"):
            onboarding_cmd._parse_toggle("kinda")


# --- flow controls ------------------------------------------------------


class TestSkip:
    async def test_advances_step(self):
        out = await onboarding_cmd.handle_skip("")
        assert "pick_persona" in out

    async def test_reports_complete_at_end(self):
        ob_module.get_onboarding_service().advance_step("pick_chain")
        out = await onboarding_cmd.handle_skip("")
        assert "Onboarding complete" in out


class TestBack:
    async def test_with_history(self):
        ob_module.get_onboarding_service().advance_step("pick_persona")
        out = await onboarding_cmd.handle_back("")
        assert "welcome" in out

    async def test_with_empty_history(self):
        out = await onboarding_cmd.handle_back("")
        assert "No previous step" in out
        assert "/reonboard" in out


class TestReonboard:
    async def test_resets_state(self):
        ob_module.get_onboarding_service().advance_step("pick_wallet")
        ob_module.get_onboarding_service().set_capability("trading", True)
        persona_module.get_persona_service().set_persona("degen")

        out = await onboarding_cmd.handle_reonboard("")
        assert "Current step: welcome" in out
        assert "Persona cleared" in out
        # Verify the side effects landed.
        ob = ob_module.get_onboarding_service()
        assert ob.get_state().step == "welcome"
        assert ob.get_capabilities() == frozenset()
        assert persona_module.get_persona_service().active_name is None


# --- registration -------------------------------------------------------


class TestRegister:
    def test_registers_all_19_commands(self):
        captured: list[str] = []

        class FakeCtx:
            def register_command(self, **kw):
                captured.append(kw["name"])

        onboarding_cmd.register(FakeCtx())

        expected = {
            "welcome",
            # 5 personas
            "professional",
            "degen",
            "chill",
            "technical",
            "mentor",
            # 10 capabilities
            "cap_wallet",
            "cap_prices",
            "cap_portfolio",
            "cap_trading",
            "cap_liquidity",
            "cap_launchpad",
            "cap_bridge",
            "cap_routing",
            "cap_clawnx",
            "cap_hummingbot",
            # 3 flow controls
            "skip",
            "back",
            "reonboard",
        }
        assert set(captured) == expected
        assert len(captured) == 19
