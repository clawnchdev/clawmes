"""Tests for the v0.11.0 ``/agent --ai`` LLM fallback."""

from __future__ import annotations

from typing import Any

import pytest

from clawmes.commands import agent_plan


@pytest.fixture(autouse=True)
def _clear_drafts():
    agent_plan._reset_for_tests()
    yield
    agent_plan._reset_for_tests()


@pytest.fixture
def fake_opengateway(monkeypatch):
    """Stub OpenGateway. Configure responses via ``state['response']``."""
    state: dict[str, Any] = {"response": None, "raises": None, "calls": []}

    class _FakeSvc:
        def chat_completion(self, messages, **kw):
            state["calls"].append(messages)
            if state["raises"]:
                raise state["raises"]
            return state["response"]

    import clawmes.services.opengateway as mod

    monkeypatch.setattr(mod, "get_opengateway_service", lambda: _FakeSvc())
    return state


# ── --ai flag parsing in handle_agent ─────────────────────────────


class TestAiFlagDispatch:
    async def test_no_ai_flag_uses_regex_only(self):
        # Without --ai, an off-template prompt fails with the standard
        # "couldn't parse" message and NO LLM hint mention isn't needed.
        out = await agent_plan.handle_agent("totally off-template gibberish")
        assert "Couldn't parse" in out

    async def test_ai_flag_strips_and_routes(self, fake_opengateway, monkeypatch):
        """--ai is removed from the prompt before regex parsing runs."""
        # Stub the gate so UNLIMITED passes.
        import clawmes.services.token_gate as tg

        monkeypatch.setattr(tg, "check_tier_or_error", lambda *a, **k: None)
        # Stub the OpenGateway error class so the except clause shape
        # is correct even though we never raise here.
        fake_opengateway["response"] = {"choices": [{"message": {"content": "claim my fees"}}]}
        out = await agent_plan.handle_agent("--ai do my fee thing pls")
        # The LLM "rewrote" the prompt to a known intent → parsed.
        assert "Plan parsed" in out

    async def test_ai_flag_blocked_when_not_unlimited(self, monkeypatch):
        """When the gate returns an error, --ai bails before calling the LLM."""
        import clawmes.services.token_gate as tg

        monkeypatch.setattr(
            tg,
            "check_tier_or_error",
            lambda *a, **k: "Clawmes Unlimited required for /agent --ai",
        )
        out = await agent_plan.handle_agent("--ai some prompt")
        assert "Clawmes Unlimited required" in out

    async def test_ai_flag_only(self, monkeypatch):
        """``/agent --ai`` with no prompt after stripping the flag → usage."""
        import clawmes.services.token_gate as tg

        monkeypatch.setattr(tg, "check_tier_or_error", lambda *a, **k: None)
        out = await agent_plan.handle_agent("--ai")
        # After stripping --ai, raw is empty → usage path.
        assert "Natural-language" in out


# ── _llm_extract direct ────────────────────────────────────────────


class TestLlmExtract:
    def test_no_failed_segments(self, fake_opengateway):
        out, still = agent_plan._llm_extract([])
        assert out == []
        assert still == []

    def test_successful_extraction(self, fake_opengateway):
        fake_opengateway["response"] = {"choices": [{"message": {"content": "claim my fees"}}]}
        out, still = agent_plan._llm_extract(["sweep my LP rewards"])
        assert len(out) == 1
        assert out[0]["command"] == "claim"
        assert still == []

    def test_llm_returns_null(self, fake_opengateway):
        fake_opengateway["response"] = {"choices": [{"message": {"content": "null"}}]}
        out, still = agent_plan._llm_extract(["not a trading intent"])
        assert out == []
        assert still == ["not a trading intent"]

    def test_llm_returns_empty(self, fake_opengateway):
        fake_opengateway["response"] = {"choices": [{"message": {"content": ""}}]}
        out, still = agent_plan._llm_extract(["x"])
        assert still == ["x"]

    def test_llm_returns_unparsable_intent(self, fake_opengateway):
        """LLM rewrites to something that still doesn't match the regex parser."""
        fake_opengateway["response"] = {
            "choices": [{"message": {"content": "make me coffee please"}}]
        }
        out, still = agent_plan._llm_extract(["x"])
        assert out == []
        assert still == ["x"]

    def test_llm_strips_quotes_and_backticks(self, fake_opengateway):
        fake_opengateway["response"] = {"choices": [{"message": {"content": "`claim my fees`"}}]}
        out, still = agent_plan._llm_extract(["sweep fees"])
        assert len(out) == 1

    def test_opengateway_raises(self, fake_opengateway):
        from clawmes.services.opengateway import OpenGatewayError

        fake_opengateway["raises"] = OpenGatewayError("upstream", "down")
        out, still = agent_plan._llm_extract(["x"])
        assert out == []
        assert still == ["x"]

    def test_generic_exception(self, fake_opengateway):
        fake_opengateway["raises"] = RuntimeError("kaboom")
        out, still = agent_plan._llm_extract(["x"])
        assert out == []
        assert still == ["x"]

    def test_import_failure_returns_failed(self, monkeypatch):
        """If the OpenGateway module can't be imported at all, return originals."""
        import builtins

        original_import = builtins.__import__

        def _block(name, *args, **kw):
            if name == "clawmes.services.opengateway":
                raise ImportError("no opengateway")
            return original_import(name, *args, **kw)

        monkeypatch.setattr(builtins, "__import__", _block)
        out, still = agent_plan._llm_extract(["x"])
        assert out == []
        assert still == ["x"]


# ── _extract_llm_text ──────────────────────────────────────────────


class TestExtractLlmText:
    def test_no_choices(self):
        assert agent_plan._extract_llm_text({}) == ""

    def test_empty_choices(self):
        assert agent_plan._extract_llm_text({"choices": []}) == ""

    def test_message_no_content(self):
        assert agent_plan._extract_llm_text({"choices": [{"message": {}}]}) == ""

    def test_content_not_string(self):
        assert agent_plan._extract_llm_text({"choices": [{"message": {"content": 123}}]}) == ""

    def test_success(self):
        out = agent_plan._extract_llm_text({"choices": [{"message": {"content": "hello"}}]})
        assert out == "hello"


# ── _cmd_parse with use_ai=True ────────────────────────────────────


class TestParseWithAi:
    def test_ai_recovers_unparsed_segment(self, fake_opengateway, monkeypatch):
        import clawmes.services.token_gate as tg

        monkeypatch.setattr(tg, "check_tier_or_error", lambda *a, **k: None)
        fake_opengateway["response"] = {"choices": [{"message": {"content": "claim my fees"}}]}
        out = agent_plan._cmd_parse("u", "sweep my LP rewards", use_ai=True)
        assert "Plan parsed" in out
        assert "u" in agent_plan._DRAFTS

    def test_ai_partial_recovery(self, fake_opengateway, monkeypatch):
        """One segment parses via regex, one via LLM, one fails completely."""
        import clawmes.services.token_gate as tg

        monkeypatch.setattr(tg, "check_tier_or_error", lambda *a, **k: None)

        # The LLM returns "claim my fees" for any segment we send.
        fake_opengateway["response"] = {"choices": [{"message": {"content": "claim my fees"}}]}
        # Prompt: "claim CLAWNCH" parses via regex, "sweep my LP rewards"
        # parses via LLM. Both succeed.
        out = agent_plan._cmd_parse("u", "claim CLAWNCH then sweep my LP rewards", use_ai=True)
        assert "Plan parsed" in out
        plan = agent_plan._DRAFTS["u"]
        assert len(plan) == 2

    def test_ai_all_segments_fail(self, fake_opengateway, monkeypatch):
        import clawmes.services.token_gate as tg

        monkeypatch.setattr(tg, "check_tier_or_error", lambda *a, **k: None)
        fake_opengateway["response"] = {"choices": [{"message": {"content": "null"}}]}
        out = agent_plan._cmd_parse("u", "completely random nonsense", use_ai=True)
        assert "Couldn't parse" in out
        assert "Clawmes Unlimited" in out  # hint mentions --ai path

    def test_no_ai_keeps_old_behavior(self):
        out = agent_plan._cmd_parse("u", "completely random nonsense", use_ai=False)
        assert "Couldn't parse" in out

    def test_ai_gate_blocks(self, monkeypatch):
        import clawmes.services.token_gate as tg

        monkeypatch.setattr(
            tg,
            "check_tier_or_error",
            lambda *a, **k: "Clawmes Unlimited required",
        )
        out = agent_plan._cmd_parse("u", "anything off-template", use_ai=True)
        assert "Clawmes Unlimited required" in out
