"""Tests for the /agent slash command (NL prompt → plan IR)."""

from __future__ import annotations

import pytest

from clawmes.commands import agent_plan


@pytest.fixture(autouse=True)
def _clear_drafts():
    agent_plan._reset_for_tests()
    yield
    agent_plan._reset_for_tests()


# ── _resolve_token_arg ──────────────────────────────────────────────


class TestResolveTokenArg:
    def test_known_symbol_clawnch(self):
        assert agent_plan._resolve_token_arg("CLAWNCH") == agent_plan._CLAWNCH_ADDR

    def test_known_symbol_dollar_sign(self):
        assert agent_plan._resolve_token_arg("$CLAWNCH") == agent_plan._CLAWNCH_ADDR

    def test_unknown_passes_through(self):
        out = agent_plan._resolve_token_arg("0xABC")
        assert out == "0xabc"


# ── _parse_one ──────────────────────────────────────────────────────


class TestParseOne:
    def test_dca_pattern(self):
        step = agent_plan._parse_one("DCA 0.001 ETH of CLAWNCH every 1h")
        assert step["command"] == "dca"
        assert "add" in step["args"]
        assert "0.001" in step["args"]
        assert "1h" in step["args"]

    def test_buy_recurring_uses_dca(self):
        step = agent_plan._parse_one("buy 0.01 eth of CLAWNCH every 4h")
        assert step["command"] == "dca"

    def test_buy_one_shot(self):
        step = agent_plan._parse_one("buy 0.01 eth of CLAWNCH")
        assert step["command"] == "buy"

    def test_copy_with_amount(self):
        step = agent_plan._parse_one("copy 0x" + "a" * 40 + " at 0.005 eth")
        assert step["command"] == "copy"
        assert "0.005" in step["args"]

    def test_copy_without_amount_default(self):
        step = agent_plan._parse_one("follow 0x" + "a" * 40)
        assert step["command"] == "copy"
        assert "0.001" in step["args"]  # default amount

    def test_claim_my_fees(self):
        step = agent_plan._parse_one("claim my fees")
        assert step["command"] == "claim"
        assert step["args"] == "all"

    def test_claim_all(self):
        step = agent_plan._parse_one("claim all")
        assert step["args"] == "all"

    def test_claim_fees(self):
        step = agent_plan._parse_one("claim fees")
        assert step["args"] == "all"

    def test_claim_one_token(self):
        step = agent_plan._parse_one("claim CLAWNCH")
        assert step["command"] == "claim"
        assert step["args"] == "clawnch"

    def test_burn(self):
        step = agent_plan._parse_one("burn 1,000,000 clawnch")
        assert step["command"] == "burn"
        assert step["args"] == "1000000"

    def test_burn_no_token_suffix(self):
        step = agent_plan._parse_one("burn 100000")
        assert step["args"] == "100000"

    def test_burn_underscores(self):
        step = agent_plan._parse_one("burn 1_000_000")
        assert step["args"] == "1000000"

    def test_leaderboard(self):
        step = agent_plan._parse_one("leaderboard")
        assert step["command"] == "leaderboard"
        assert step["args"] == ""

    def test_top_tokens(self):
        step = agent_plan._parse_one("top tokens")
        assert step["command"] == "leaderboard"

    def test_show_me_leaderboard(self):
        step = agent_plan._parse_one("show me leaderboard")
        assert step["command"] == "leaderboard"

    def test_top_launchers(self):
        step = agent_plan._parse_one("top launchers")
        assert step["command"] == "leaderboard"
        assert step["args"] == "launchers"

    def test_leaderboard_launchers(self):
        step = agent_plan._parse_one("leaderboard launchers")
        assert step["args"] == "launchers"

    def test_my_launches(self):
        step = agent_plan._parse_one("show my launches")
        assert step["command"] == "my_launches"

    def test_launches_short(self):
        step = agent_plan._parse_one("launches")
        assert step["command"] == "my_launches"

    def test_balance_question(self):
        step = agent_plan._parse_one("what's my balance")
        assert step["command"] == "balance"

    def test_balance_short(self):
        step = agent_plan._parse_one("balance")
        assert step["command"] == "balance"

    def test_unmatched_returns_none(self):
        assert agent_plan._parse_one("total gibberish here") is None


# ── _parse_prompt (multi-step) ──────────────────────────────────────


class TestParsePrompt:
    def test_single_segment(self):
        plan, errs = agent_plan._parse_prompt("DCA 0.001 ETH of CLAWNCH every 1h")
        assert len(plan) == 1
        assert errs == []

    def test_then_separator(self):
        plan, errs = agent_plan._parse_prompt(
            "DCA 0.001 ETH of CLAWNCH every 1h then claim my fees"
        )
        assert len(plan) == 2
        assert errs == []

    def test_comma_then_normalized(self):
        plan, errs = agent_plan._parse_prompt(
            "DCA 0.001 ETH of CLAWNCH every 1h, then claim my fees"
        )
        assert len(plan) == 2

    def test_bare_comma_NOT_split(self):
        # Bare commas inside numbers must NOT be treated as separators.
        plan, errs = agent_plan._parse_prompt("burn 1,000,000 CLAWNCH")
        assert len(plan) == 1
        assert plan[0]["args"] == "1000000"

    def test_mixed_success_failure(self):
        plan, errs = agent_plan._parse_prompt(
            "DCA 0.001 ETH of CLAWNCH every 1h then random nonsense"
        )
        assert len(plan) == 1
        assert errs == ["random nonsense"]

    def test_all_garbage(self):
        plan, errs = agent_plan._parse_prompt("total gibberish here")
        assert plan == []
        assert errs == ["total gibberish here"]

    def test_empty_prompt(self):
        plan, errs = agent_plan._parse_prompt("")
        assert plan == []
        assert errs == []


# ── _cmd_parse / _cmd_show / _cmd_cancel ───────────────────────────


class TestCmdParse:
    def test_all_parse_failure(self):
        out = agent_plan._cmd_parse("u", "total nonsense")
        assert "Couldn't parse" in out
        assert "/agent examples" in out

    def test_success_stores_draft(self):
        out = agent_plan._cmd_parse("u", "claim my fees")
        assert "Plan parsed" in out
        assert "claim" in out
        assert "u" in agent_plan._DRAFTS

    def test_mixed_partial_success(self):
        out = agent_plan._cmd_parse("u", "claim my fees then random garbage")
        assert "Plan parsed" in out
        assert "Could not parse" in out
        assert "random garbage" in out


class TestCmdShow:
    def test_empty(self):
        out = agent_plan._cmd_show("u")
        assert "No draft" in out

    def test_with_draft(self):
        agent_plan._cmd_parse("u", "claim my fees")
        out = agent_plan._cmd_show("u")
        assert "Draft for u" in out
        assert "claim" in out


class TestCmdCancel:
    def test_no_draft(self):
        assert "No draft" in agent_plan._cmd_cancel("u")

    def test_clears(self):
        agent_plan._cmd_parse("u", "claim my fees")
        out = agent_plan._cmd_cancel("u")
        assert "cancelled" in out.lower()
        assert "u" not in agent_plan._DRAFTS


# ── _dispatch_step + _cmd_confirm ──────────────────────────────────


class TestDispatch:
    """Spy-based tests for each dispatch branch.

    Each clawmes command's ``handle_*`` function is monkeypatched to a
    spy that records the args it received. We then construct a plan
    step, dispatch it, and verify the right command got the right args.
    """

    async def test_dispatch_dca(self, monkeypatch):
        captured: dict[str, str] = {}

        async def _fake(args, sender_id="default", **kw):
            captured["args"] = args
            captured["sender"] = sender_id
            return "dca result"

        import clawmes.commands.dca as mod

        monkeypatch.setattr(mod, "handle_dca", _fake)
        out = await agent_plan._dispatch_step({"command": "dca", "args": "add 0xabc 0.001 1h"}, "u")
        assert "dca result" in out
        assert captured["args"] == "add 0xabc 0.001 1h"
        assert captured["sender"] == "u"

    async def test_dispatch_buy(self, monkeypatch):
        async def _fake(args, sender_id="default", **kw):
            return f"buy {args}"

        import clawmes.commands.buy as mod

        monkeypatch.setattr(mod, "handle_buy", _fake)
        out = await agent_plan._dispatch_step({"command": "buy", "args": "CLAWNCH 0.01"}, "u")
        assert "buy CLAWNCH 0.01" in out

    async def test_dispatch_copy(self, monkeypatch):
        async def _fake(args, sender_id="default", **kw):
            return f"copy {args}"

        import clawmes.commands.copy as mod

        monkeypatch.setattr(mod, "handle_copy", _fake)
        out = await agent_plan._dispatch_step({"command": "copy", "args": "add 0xabc 0.001"}, "u")
        assert "copy add" in out

    async def test_dispatch_claim(self, monkeypatch):
        async def _fake(args, sender_id="default", **kw):
            return f"claim {args}"

        import clawmes.commands.claim as mod

        monkeypatch.setattr(mod, "handle_claim", _fake)
        out = await agent_plan._dispatch_step({"command": "claim", "args": "all"}, "u")
        assert "claim all" in out

    async def test_dispatch_burn(self, monkeypatch):
        async def _fake(args, sender_id="default", **kw):
            return f"burn {args}"

        import clawmes.commands.burn as mod

        monkeypatch.setattr(mod, "handle_burn", _fake)
        out = await agent_plan._dispatch_step({"command": "burn", "args": "1000000"}, "u")
        assert "burn 1000000" in out

    async def test_dispatch_leaderboard(self, monkeypatch):
        async def _fake(args, **kw):
            return f"lb {args}"

        import clawmes.commands.leaderboard as mod

        monkeypatch.setattr(mod, "handle_leaderboard", _fake)
        out = await agent_plan._dispatch_step({"command": "leaderboard", "args": ""}, "u")
        assert "lb" in out

    async def test_dispatch_my_launches(self, monkeypatch):
        async def _fake(args, **kw):
            return f"ml {args}"

        import clawmes.commands.my_launches as mod

        monkeypatch.setattr(mod, "handle_my_launches", _fake)
        out = await agent_plan._dispatch_step({"command": "my_launches", "args": ""}, "u")
        assert "ml" in out

    async def test_dispatch_balance(self, monkeypatch):
        async def _fake(args, **kw):
            return f"bal {args}"

        import clawmes.commands.balance as mod

        monkeypatch.setattr(mod, "handle_balance", _fake)
        out = await agent_plan._dispatch_step({"command": "balance", "args": ""}, "u")
        assert "bal" in out


class TestCmdConfirm:
    async def test_no_draft(self):
        out = await agent_plan._cmd_confirm("u")
        assert "No draft to confirm" in out

    async def test_executes_each_step(self, monkeypatch):
        # Stub each command handler so confirm runs end-to-end.
        async def _fake_claim(args, sender_id="default", **kw):
            return "claim ok\nextra detail line"

        async def _fake_burn(args, sender_id="default", **kw):
            return "burn ok"

        import clawmes.commands.burn as burn_mod
        import clawmes.commands.claim as claim_mod

        monkeypatch.setattr(claim_mod, "handle_claim", _fake_claim)
        monkeypatch.setattr(burn_mod, "handle_burn", _fake_burn)

        agent_plan._cmd_parse("u", "claim my fees then burn 1000000")
        out = await agent_plan._cmd_confirm("u")
        assert "Executing 2 step(s)" in out
        assert "claim ok" in out
        assert "burn ok" in out
        # Draft cleared after execution.
        assert "u" not in agent_plan._DRAFTS

    async def test_draft_cleared_after_confirm(self, monkeypatch):
        async def _fake(args, sender_id="default", **kw):
            return "done"

        import clawmes.commands.claim as claim_mod

        monkeypatch.setattr(claim_mod, "handle_claim", _fake)
        agent_plan._cmd_parse("u", "claim my fees")
        await agent_plan._cmd_confirm("u")
        # Subsequent confirm should report no draft.
        out2 = await agent_plan._cmd_confirm("u")
        assert "No draft" in out2


# ── handle_agent (dispatch) ────────────────────────────────────────


class TestHandleAgent:
    async def test_empty_shows_usage(self):
        out = await agent_plan.handle_agent("")
        assert "Natural-language plan compiler" in out

    async def test_show_dispatch(self):
        out = await agent_plan.handle_agent("show")
        assert "No draft" in out

    async def test_confirm_dispatch(self):
        out = await agent_plan.handle_agent("confirm")
        assert "No draft" in out

    async def test_cancel_dispatch(self):
        out = await agent_plan.handle_agent("cancel")
        assert "No draft" in out

    async def test_examples_dispatch(self):
        out = await agent_plan.handle_agent("examples")
        assert "Supported phrasings" in out

    async def test_prompt_dispatch(self):
        out = await agent_plan.handle_agent("claim my fees")
        assert "Plan parsed" in out

    async def test_record_swallows(self, monkeypatch):
        import clawmes.services.command_history as ch

        def _boom(*a, **k):
            raise RuntimeError("hist broken")

        monkeypatch.setattr(ch, "record_command_call", _boom)
        out = await agent_plan.handle_agent("")
        assert "Natural-language" in out


# ── register ───────────────────────────────────────────────────────


class TestRegister:
    def test_register_wires_command(self):
        registered: list[dict] = []

        class Ctx:
            def register_command(self, **kwargs):
                registered.append(kwargs)

        agent_plan.register(Ctx())
        assert len(registered) == 1
        assert registered[0]["name"] == "agent"
