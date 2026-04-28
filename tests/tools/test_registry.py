"""Tests for clawmes.tools.registry — decorators + register_with_ctx + gate."""

from __future__ import annotations

import json

import pytest

from clawmes.lib.params import ParamError
from clawmes.lib.tool_result import json_result
from clawmes.policy import storage as policy_storage
from clawmes.policy import usage_counter as usage_counter_module
from clawmes.policy.types import Policy
from clawmes.services import mode_service as mode_module
from clawmes.tools.registry import (
    WRITE_TOOL_NAMES,
    _extract_chain_id,
    _extract_value_wei,
    read_tool,
    register_with_ctx,
    write_tool,
)


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """HERMES_HOME isolation + reset all relevant singletons."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(mode_module, "_instance", None)
    monkeypatch.setattr(usage_counter_module, "_instance", None)
    # Make sure each test starts with no policies (empty list, not defaults)
    policy_storage.save_policies([])


SAMPLE_SCHEMA = {
    "type": "object",
    "properties": {"x": {"type": "string"}},
}


class TestWriteToolDecorator:
    def test_metadata_attached(self):
        @write_tool(
            name="t_write_meta",
            toolset="t",
            description="desc",
            schema=SAMPLE_SCHEMA,
            requires_env=["X"],
            emoji="🔧",
        )
        def fn(args, **kw):
            return json_result({"ok": True})

        meta = fn._clawmes_meta
        assert meta["name"] == "t_write_meta"
        assert meta["toolset"] == "t"
        assert meta["description"] == "desc"
        assert meta["schema"] is SAMPLE_SCHEMA
        assert meta["requires_env"] == ["X"]
        assert meta["emoji"] == "🔧"
        assert meta["is_write"] is True

    def test_added_to_write_set(self):
        @write_tool(
            name="t_in_set",
            toolset="t",
            description="d",
            schema=SAMPLE_SCHEMA,
        )
        def fn(args, **kw):
            return json_result({})

        assert "t_in_set" in WRITE_TOOL_NAMES

    def test_handler_runs_normal_path(self):
        @write_tool(name="t_run_ok", toolset="t", description="d", schema=SAMPLE_SCHEMA)
        def fn(args, **kw):
            return json_result({"ran": True})

        out = json.loads(fn({"x": "y"}))
        assert out["details"] == {"ran": True}

    def test_handler_param_error_converted(self):
        @write_tool(name="t_param_err", toolset="t", description="d", schema=SAMPLE_SCHEMA)
        def fn(args, **kw):
            raise ParamError("bad arg")

        out = json.loads(fn({}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "param_error"

    def test_handler_other_exception_converted(self):
        @write_tool(name="t_other_err", toolset="t", description="d", schema=SAMPLE_SCHEMA)
        def fn(args, **kw):
            raise RuntimeError("boom")

        out = json.loads(fn({}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "tool_error"


class TestReadToolDecorator:
    def test_metadata_marks_read(self):
        @read_tool(name="t_read_meta", toolset="t", description="d", schema=SAMPLE_SCHEMA)
        def fn(args, **kw):
            return json_result({})

        assert fn._clawmes_meta["is_write"] is False

    def test_not_in_write_set(self):
        @read_tool(name="t_not_in_set", toolset="t", description="d", schema=SAMPLE_SCHEMA)
        def fn(args, **kw):
            return json_result({})

        assert "t_not_in_set" not in WRITE_TOOL_NAMES

    def test_handler_runs(self):
        @read_tool(name="t_read_run", toolset="t", description="d", schema=SAMPLE_SCHEMA)
        def fn(args, **kw):
            return json_result({"ok": 1})

        out = json.loads(fn({}))
        assert out["details"]["ok"] == 1

    def test_param_error_converted(self):
        @read_tool(name="t_read_param_err", toolset="t", description="d", schema=SAMPLE_SCHEMA)
        def fn(args, **kw):
            raise ParamError("bad")

        out = json.loads(fn({}))
        assert out["details"]["error_code"] == "param_error"

    def test_other_exception_converted(self):
        @read_tool(name="t_read_other_err", toolset="t", description="d", schema=SAMPLE_SCHEMA)
        def fn(args, **kw):
            raise RuntimeError("boom")

        out = json.loads(fn({}))
        assert out["details"]["error_code"] == "tool_error"


class TestRegisterWithCtx:
    def test_passes_metadata_to_ctx(self):
        @write_tool(
            name="t_ctx",
            toolset="t",
            description="d",
            schema=SAMPLE_SCHEMA,
            emoji="🚀",
        )
        def fn(args, **kw):
            return json_result({})

        recorded = []

        class FakeCtx:
            def register_tool(self, **kw):
                recorded.append(kw)

        register_with_ctx(FakeCtx(), fn)
        assert recorded[0]["name"] == "t_ctx"
        assert recorded[0]["toolset"] == "t"
        assert recorded[0]["emoji"] == "🚀"
        assert recorded[0]["handler"] is fn

    def test_undecorated_function_raises(self):
        def naked(args, **kw):
            return json.dumps({})

        class FakeCtx:
            def register_tool(self, **kw):
                pass

        with pytest.raises(RuntimeError, match="not a clawmes tool"):
            register_with_ctx(FakeCtx(), naked)


# Gate stages -----------------------------------------------------------


class TestStage1ReadonlyMode:
    def test_readonly_blocks_writes(self):
        @write_tool(name="t_ro_write", toolset="t", description="d", schema=SAMPLE_SCHEMA)
        def fn(args, **kw):
            return json_result({"ran": True})

        mode_module.get_mode_service().set_mode("readonly")
        out = json.loads(fn({}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "readonly_mode"

    def test_normal_mode_allows(self):
        @write_tool(name="t_normal_write", toolset="t", description="d", schema=SAMPLE_SCHEMA)
        def fn(args, **kw):
            return json_result({"ran": True})

        out = json.loads(fn({}))
        assert "isError" not in out
        assert out["details"]["ran"] is True

    def test_danger_mode_allows(self):
        @write_tool(name="t_danger_write", toolset="t", description="d", schema=SAMPLE_SCHEMA)
        def fn(args, **kw):
            return json_result({"ran": True})

        mode_module.get_mode_service().set_mode("danger")
        out = json.loads(fn({}))
        assert "isError" not in out


class TestStage2AmountToWei:
    def test_amount_alone_triggers_value_gate(self):
        """Without explicit value_wei, an amount-only call should still
        trigger a value-quantitative gate. Previously the gate would
        skip amount and silently let large transfers through; the
        registry now extracts wei from amount for native transfers."""

        @write_tool(
            name="t_amount_gate",
            toolset="t",
            description="d",
            schema=SAMPLE_SCHEMA,
        )
        def fn(args, **kw):
            return json_result({"ran": True})

        policy_storage.save_policies(
            [
                Policy(
                    name="big-transfer",
                    decision="block",
                    applies_to_tools=("t_amount_gate",),
                    max_amount_wei=10**16,  # 0.01 ETH
                )
            ]
        )
        # 0.5 ETH > 0.01 ETH → block
        out = json.loads(fn({"amount": "0.5", "to": "0xabc"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "policy_block"

    def test_token_transfer_unknown_token_skips_amount_gate(self):
        """ERC-20 transfers with an unknown token — peek returns None,
        gate cannot quantify, tool runs. This is the fail-open path
        for tokens we've never seen — the alternative (block-on-
        uncertainty) would prevent every first-time token transfer."""

        @write_tool(
            name="t_amount_unknown_token",
            toolset="t",
            description="d",
            schema=SAMPLE_SCHEMA,
        )
        def fn(args, **kw):
            return json_result({"ran": True})

        policy_storage.save_policies(
            [
                Policy(
                    name="big-transfer",
                    decision="block",
                    applies_to_tools=("t_amount_unknown_token",),
                    max_amount_wei=10**16,
                )
            ]
        )
        out = json.loads(
            fn(
                {
                    "amount": "1000000",
                    "token": "0x" + "2" * 40,  # unknown
                    "to": "0xabc",
                }
            )
        )
        assert "isError" not in out

    def test_token_transfer_seeded_token_triggers_gate(self, monkeypatch):
        """ERC-20 transfers with a seeded token — peek returns 6
        (USDC), gate converts 1000 USDC -> 10^9 wei (well above 10^7
        threshold). Gate fires."""
        from clawmes.services import token_decimals as td_mod
        from clawmes.services.token_decimals import TokenDecimalsService

        svc = TokenDecimalsService()
        monkeypatch.setattr(td_mod, "_instance", svc)

        @write_tool(
            name="t_amount_seeded_token",
            toolset="t",
            description="d",
            schema=SAMPLE_SCHEMA,
        )
        def fn(args, **kw):
            return json_result({"ran": True})

        policy_storage.save_policies(
            [
                Policy(
                    name="big-transfer",
                    decision="block",
                    applies_to_tools=("t_amount_seeded_token",),
                    max_amount_wei=10**7,  # 10 USDC
                )
            ]
        )
        out = json.loads(
            fn(
                {
                    "amount": "1000",  # 1000 USDC = 10^9 base units > 10^7 threshold
                    "token": "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
                    "chain_id": 8453,
                    "to": "0xabc",
                }
            )
        )
        assert out["isError"] is True
        assert out["details"]["error_code"] == "policy_block"


class TestStage2PolicyBlock:
    def test_policy_block(self):
        @write_tool(name="t_pol_block", toolset="t", description="d", schema=SAMPLE_SCHEMA)
        def fn(args, **kw):
            return json_result({})

        policy_storage.save_policies(
            [
                Policy(
                    name="block-it",
                    decision="block",
                    applies_to_tools=("t_pol_block",),
                    description="for testing",
                )
            ]
        )
        out = json.loads(fn({}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "policy_block"
        assert "block-it" in out["content"][0]["text"]


class TestStage2PolicyConfirm:
    def test_first_call_returns_policy_hold(self):
        @write_tool(name="t_confirm_a", toolset="t", description="d", schema=SAMPLE_SCHEMA)
        def fn(args, **kw):
            return json_result({"ran": True})

        policy_storage.save_policies(
            [
                Policy(
                    name="confirm-it",
                    decision="confirm",
                    applies_to_tools=("t_confirm_a",),
                )
            ]
        )
        out = json.loads(fn({}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "policy_hold"
        # The hold message includes a nonce parameter for the LLM to retry with
        assert "policyConfirmationNonce" in out["content"][0]["text"]

    def test_retry_with_valid_nonce_proceeds(self):
        @write_tool(name="t_confirm_b", toolset="t", description="d", schema=SAMPLE_SCHEMA)
        def fn(args, **kw):
            return json_result({"ran": True})

        policy_storage.save_policies(
            [Policy(name="confirm-it", decision="confirm", applies_to_tools=("t_confirm_b",))]
        )

        # First call gets a nonce
        first = json.loads(fn({"to": "alice"}))
        assert first["details"]["error_code"] == "policy_hold"
        # Extract the nonce from the message (between quotes)
        text = first["content"][0]["text"]
        nonce_marker = 'policyConfirmationNonce="'
        start = text.index(nonce_marker) + len(nonce_marker)
        end = text.index('"', start)
        nonce = text[start:end]

        # Retry with that nonce — same args otherwise
        second = json.loads(fn({"to": "alice", "policyConfirmationNonce": nonce}))
        assert "isError" not in second
        assert second["details"]["ran"] is True

    def test_retry_with_wrong_nonce_returns_new_hold(self):
        @write_tool(name="t_confirm_c", toolset="t", description="d", schema=SAMPLE_SCHEMA)
        def fn(args, **kw):
            return json_result({})

        policy_storage.save_policies(
            [Policy(name="confirm-it", decision="confirm", applies_to_tools=("t_confirm_c",))]
        )
        out = json.loads(fn({"policyConfirmationNonce": "completely-bogus"}))
        assert out["details"]["error_code"] == "policy_hold"


class TestStage5RateLimitRecording:
    def test_successful_call_increments_counter(self):
        @write_tool(name="t_record_a", toolset="t", description="d", schema=SAMPLE_SCHEMA)
        def fn(args, **kw):
            return json_result({})

        policy_storage.save_policies([])  # ensure no policy interferes
        from clawmes.policy.usage_counter import get_usage_counter

        before = get_usage_counter().count("default", "t_record_a")
        fn({})
        after = get_usage_counter().count("default", "t_record_a")
        assert after == before + 1

    def test_failed_call_does_not_increment(self):
        @write_tool(name="t_record_b", toolset="t", description="d", schema=SAMPLE_SCHEMA)
        def fn(args, **kw):
            raise RuntimeError("simulated failure")

        from clawmes.policy.usage_counter import get_usage_counter

        before = get_usage_counter().count("default", "t_record_b")
        fn({})  # converts exception to error envelope
        after = get_usage_counter().count("default", "t_record_b")
        # The handler raised before stage 5 — counter not incremented
        assert after == before

    def test_blocked_call_does_not_increment(self):
        @write_tool(name="t_record_c", toolset="t", description="d", schema=SAMPLE_SCHEMA)
        def fn(args, **kw):
            return json_result({})

        policy_storage.save_policies(
            [Policy(name="block", decision="block", applies_to_tools=("t_record_c",))]
        )
        from clawmes.policy.usage_counter import get_usage_counter

        before = get_usage_counter().count("default", "t_record_c")
        fn({})
        after = get_usage_counter().count("default", "t_record_c")
        # Stage 2 returned before the handler ran — no record
        assert after == before


class TestUserIdScoping:
    def test_per_user_id_in_action_context(self):
        recorded = {}

        @write_tool(name="t_user_id", toolset="t", description="d", schema=SAMPLE_SCHEMA)
        def fn(args, **kw):
            recorded["user_id"] = kw.get("user_id")
            return json_result({})

        fn({}, user_id="alice")
        assert recorded["user_id"] == "alice"

    def test_default_user_when_missing(self):
        @write_tool(name="t_default_user", toolset="t", description="d", schema=SAMPLE_SCHEMA)
        def fn(args, **kw):
            return json_result({})

        # Policy with rate limit on this tool
        policy_storage.save_policies([])

        # Run twice without user_id; both should record under "default"
        fn({})
        fn({})
        from clawmes.policy.usage_counter import get_usage_counter

        assert get_usage_counter().count("default", "t_default_user") == 2


class TestExtractHelpers:
    def test_extract_chain_id_from_chain_id_key(self):
        assert _extract_chain_id({"chain_id": 8453}) == 8453

    def test_extract_chain_id_from_chain_key(self):
        assert _extract_chain_id({"chain": 8453}) == 8453

    def test_extract_chain_id_string(self):
        assert _extract_chain_id({"chain_id": "1"}) == 1

    def test_extract_chain_id_invalid_returns_none(self):
        assert _extract_chain_id({"chain_id": "not-a-number"}) is None

    def test_extract_chain_id_missing_returns_none(self):
        assert _extract_chain_id({}) is None
        assert _extract_chain_id(None) is None

    def test_extract_value_wei_from_value_wei(self):
        assert _extract_value_wei({"value_wei": 10**18}) == 10**18

    def test_extract_value_wei_from_amount_wei(self):
        assert _extract_value_wei({"amount_wei": 500}) == 500

    def test_extract_value_wei_string(self):
        assert _extract_value_wei({"value_wei": "100"}) == 100

    def test_extract_value_wei_invalid_falls_through(self):
        # Bad value at first key doesn't poison subsequent keys
        assert _extract_value_wei({"value_wei": "bad", "amount_wei": "200"}) == 200

    def test_extract_value_wei_missing_returns_none(self):
        assert _extract_value_wei({}) is None
        assert _extract_value_wei(None) is None
        assert _extract_value_wei({"unrelated": "x"}) is None

    def test_extract_value_wei_all_invalid_returns_none(self):
        # Every candidate key has a non-int value
        assert _extract_value_wei({"value_wei": "x", "amount_wei": "y"}) is None

    def test_extract_value_wei_from_amount_native(self):
        # Native transfer (no token) — convert via 18 decimals
        assert _extract_value_wei({"amount": "1.5", "to": "0xabc"}) == 15 * 10**17

    def test_extract_value_wei_from_amount_zero(self):
        # Zero is a valid native value; the gate should report 0, not None
        assert _extract_value_wei({"amount": "0", "to": "0xabc"}) == 0

    def test_extract_value_wei_token_present_skips_amount(self):
        # ERC-20 — decimals unknown at gate level, skip amount conversion
        assert (
            _extract_value_wei({"amount": "100", "token": "0x" + "2" * 40, "to": "0xabc"}) is None
        )

    def test_extract_value_wei_amount_takes_priority_below_explicit(self):
        # If both value_wei and amount are set, value_wei wins (it's
        # the more explicit signal)
        assert _extract_value_wei({"value_wei": 10**18, "amount": "999", "to": "0xabc"}) == 10**18

    def test_extract_value_wei_amount_invalid_returns_none(self):
        # Garbage amount falls through silently — gate just won't fire
        assert _extract_value_wei({"amount": "not-a-number", "to": "0xabc"}) is None

    def test_extract_value_wei_amount_negative_returns_none(self):
        # to_base_units rejects negatives; we treat as missing
        assert _extract_value_wei({"amount": "-1", "to": "0xabc"}) is None

    def test_extract_value_wei_amount_empty_string(self):
        assert _extract_value_wei({"amount": "", "to": "0xabc"}) is None

    def test_extract_value_wei_amount_with_empty_token(self):
        # Empty-string token is treated as 'no token' — native fallback fires
        assert _extract_value_wei({"amount": "1", "token": "", "to": "0xabc"}) == 10**18

    def test_extract_value_wei_erc20_uses_cached_decimals(self, monkeypatch):
        # ERC-20 with a seeded token (USDC on Base, 6 decimals).
        # Gate should convert 100 -> 100 * 10^6.
        from clawmes.services import token_decimals as td_mod
        from clawmes.tools import registry as registry_mod

        monkeypatch.setattr(td_mod, "_instance", None)
        # Force fresh service so seeds are loaded
        from clawmes.services.token_decimals import TokenDecimalsService

        svc = TokenDecimalsService()
        monkeypatch.setattr(td_mod, "_instance", svc)
        monkeypatch.setattr(
            registry_mod,
            "_extract_chain_id",
            lambda args: 8453,
        )
        wei = _extract_value_wei(
            {
                "amount": "100",
                "token": "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",  # USDC Base
                "to": "0xabc",
            }
        )
        assert wei == 100 * 10**6

    def test_extract_value_wei_erc20_unknown_token_returns_none(self, monkeypatch):
        # No seed, no prior fetch, no fallback — peek returns None,
        # gate skips quantitative evaluation. Crucially, no RPC call.
        from clawmes.services import token_decimals as td_mod
        from clawmes.services.token_decimals import TokenDecimalsService

        svc = TokenDecimalsService()
        monkeypatch.setattr(td_mod, "_instance", svc)

        # If anything tries to issue an RPC inside the gate, this would
        # fail (no rpc service configured). We rely on peek staying
        # non-blocking.
        result = _extract_value_wei(
            {
                "amount": "100",
                "token": "0x" + "f" * 40,  # unknown token
                "to": "0xabc",
            }
        )
        assert result is None

    def test_extract_value_wei_erc20_explicit_chain_id(self, monkeypatch):
        from clawmes.services import token_decimals as td_mod
        from clawmes.services.token_decimals import TokenDecimalsService

        svc = TokenDecimalsService()
        monkeypatch.setattr(td_mod, "_instance", svc)
        # USDC on Ethereum mainnet (chain 1)
        wei = _extract_value_wei(
            {
                "amount": "50",
                "token": "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
                "chain_id": 1,
                "to": "0xabc",
            }
        )
        assert wei == 50 * 10**6

    def test_extract_value_wei_erc20_uses_loose_fallback(self, monkeypatch):
        # If the loose-fallback tier has 18, peek returns 18 — gate
        # quantifies (with a possibly-wrong large value, which is fine
        # because over-quantifying causes more confirms not fewer).
        from clawmes.services import token_decimals as td_mod
        from clawmes.services.token_decimals import TokenDecimalsService

        svc = TokenDecimalsService()
        token = "0x" + "5" * 40
        svc._fallback[(8453, token)] = 18
        monkeypatch.setattr(td_mod, "_instance", svc)
        wei = _extract_value_wei(
            {
                "amount": "1",
                "token": token,
                "to": "0xabc",
            }
        )
        assert wei == 10**18
