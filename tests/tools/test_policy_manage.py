"""Tests for the ``policy_manage`` tool."""

from __future__ import annotations

import json
import time

import pytest

from clawmes.policy import confirm_store as confirm_store_mod
from clawmes.policy import storage as policy_storage
from clawmes.policy import usage_counter as usage_counter_mod
from clawmes.policy.types import Policy
from clawmes.tools import policy_manage as policy_manage_mod
from clawmes.tools.policy_manage import policy_manage, register


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """HERMES_HOME isolation + reset module singletons that hold state."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    # Fresh in-memory confirm-store + usage counter per test.
    monkeypatch.setattr(confirm_store_mod, "store", confirm_store_mod.ConfirmStore())
    monkeypatch.setattr(policy_manage_mod, "confirm_store", confirm_store_mod.store)
    monkeypatch.setattr(usage_counter_mod, "_instance", None)
    # Wipe persisted policies — defaults are written on first load otherwise.
    policy_storage.save_policies([])


def _call(action: str, **extra) -> dict:
    """Invoke the tool and return the parsed JSON envelope."""
    payload = {"action": action, **extra}
    return json.loads(policy_manage(payload))


def _details(result: dict) -> dict:
    return result.get("details") or {}


# --- list -----------------------------------------------------------------


class TestList:
    def test_empty(self):
        out = _call("list")
        assert "isError" not in out
        d = _details(out)
        assert d["active_count"] == 0
        assert d["disabled_count"] == 0
        assert d["active"] == []
        assert d["disabled"] == []

    def test_with_active(self):
        policy_storage.save_policies(
            [
                Policy(name="p1", decision="block", applies_to_tools=("transfer",)),
                Policy(name="p2", decision="confirm", applies_to_tools=("defi_swap",)),
            ]
        )
        out = _call("list")
        d = _details(out)
        assert d["active_count"] == 2
        names = [p["name"] for p in d["active"]]
        assert names == ["p1", "p2"]

    def test_with_disabled(self, tmp_path):
        # Seed a disabled side-car.
        from clawmes.tools.policy_manage import _save_disabled

        _save_disabled([Policy(name="off", decision="block")])
        out = _call("list")
        d = _details(out)
        assert d["disabled_count"] == 1
        assert d["disabled"][0]["name"] == "off"

    def test_summary_truncates_long_lists(self):
        # Cover the truncation branches (> 10 active, > 5 disabled).
        policy_storage.save_policies([Policy(name=f"a{i}", decision="confirm") for i in range(12)])
        from clawmes.tools.policy_manage import _save_disabled

        _save_disabled([Policy(name=f"d{i}", decision="block") for i in range(7)])
        out = _call("list")
        text = out["content"][0]["text"]
        assert "+2 more" in text
        assert "+2 more disabled" in text


# --- get ------------------------------------------------------------------


class TestGet:
    def test_active(self):
        policy_storage.save_policies(
            [Policy(name="rate-confirm", decision="confirm", max_per_hour=5)]
        )
        out = _call("get", policyId="rate-confirm")
        d = _details(out)
        assert d["status"] == "active"
        assert d["policy"]["max_per_hour"] == 5

    def test_disabled(self):
        from clawmes.tools.policy_manage import _save_disabled

        _save_disabled([Policy(name="paused", decision="block")])
        out = _call("get", policyId="paused")
        d = _details(out)
        assert d["status"] == "disabled"

    def test_not_found(self):
        out = _call("get", policyId="ghost")
        assert out.get("isError") is True
        assert _details(out)["error_code"] == "not_found"

    def test_missing_id(self):
        out = _call("get")
        assert out.get("isError") is True
        assert _details(out)["error_code"] == "param_error"


# --- propose + confirm ----------------------------------------------------


class TestProposeAndConfirm:
    def _propose_args(self, **overrides):
        base = {
            "name": "block-large-transfers",
            "decision": "block",
            "applies_to_tools": ["transfer"],
            "max_amount_wei": 1_000_000_000_000_000_000,
            "description": "Block transfers over 1 ETH",
        }
        base.update(overrides)
        return base

    def test_basic_round_trip(self):
        proposed = _call("propose", **self._propose_args())
        assert proposed["details"]["status"] == "proposed"
        nonce = proposed["details"]["nonce"]

        confirmed = _call(
            "confirm",
            policyConfirmationNonce=nonce,
            **self._propose_args(),
        )
        assert confirmed["details"]["status"] == "active"

        # Should now appear in list.
        listed = _call("list")
        names = [p["name"] for p in listed["details"]["active"]]
        assert "block-large-transfers" in names

    def test_propose_missing_required(self):
        out = _call("propose", name="x")  # no decision
        assert _details(out)["error_code"] == "param_error"

    def test_propose_conflict_with_active(self):
        policy_storage.save_policies([Policy(name="dup", decision="block")])
        out = _call("propose", name="dup", decision="confirm")
        assert _details(out)["error_code"] == "conflict"
        assert "Use action=revise" in out["content"][0]["text"]

    def test_propose_conflict_with_disabled(self):
        from clawmes.tools.policy_manage import _save_disabled

        _save_disabled([Policy(name="dup", decision="block")])
        out = _call("propose", name="dup", decision="confirm")
        assert _details(out)["error_code"] == "conflict"
        assert "action=enable" in out["content"][0]["text"]

    def test_confirm_missing_nonce(self):
        out = _call("confirm", **self._propose_args())
        assert _details(out)["error_code"] == "param_error"

    def test_confirm_bad_param(self):
        # Confirm with no name → builder raises ParamError before nonce check.
        out = _call("confirm", policyConfirmationNonce="x", decision="block")
        assert _details(out)["error_code"] == "param_error"

    def test_confirm_invalid_nonce(self):
        # Skip propose, jump straight to confirm with a fabricated nonce.
        out = _call(
            "confirm",
            policyConfirmationNonce="not-a-real-nonce",
            **self._propose_args(),
        )
        assert _details(out)["error_code"] == "confirm_failed"

    def test_confirm_fingerprint_mismatch(self):
        proposed = _call("propose", **self._propose_args())
        nonce = proposed["details"]["nonce"]
        # Same name+decision but different applies_to_tools → fingerprint differs.
        out = _call(
            "confirm",
            policyConfirmationNonce=nonce,
            **self._propose_args(applies_to_tools=["defi_swap"]),
        )
        assert _details(out)["error_code"] == "confirm_failed"

    def test_confirm_concurrent_creation(self):
        proposed = _call("propose", **self._propose_args())
        nonce = proposed["details"]["nonce"]
        # Sneak the same-named policy in between propose and confirm.
        policy_storage.save_policies([Policy(name="block-large-transfers", decision="confirm")])
        out = _call(
            "confirm",
            policyConfirmationNonce=nonce,
            **self._propose_args(),
        )
        assert _details(out)["error_code"] == "conflict"

    def test_propose_with_empty_filters(self):
        # Catch-all rule (no applies_to_tools, no chain_ids, no thresholds).
        proposed = _call(
            "propose",
            name="catch-all-block",
            decision="block",
        )
        assert proposed["details"]["status"] == "proposed"
        text = proposed["content"][0]["text"]
        assert "all write tools" in text
        assert "Chains: all" in text


# --- revise ---------------------------------------------------------------


class TestRevise:
    def setup_method(self):
        policy_storage.save_policies(
            [
                Policy(
                    name="r1",
                    decision="confirm",
                    applies_to_tools=("transfer",),
                    chain_ids=(1,),
                    max_amount_wei=1_000,
                    max_per_hour=5,
                    description="orig",
                )
            ]
        )

    def test_change_decision(self):
        out = _call("revise", policyId="r1", decision="block")
        assert out["details"]["policy"]["decision"] == "block"

    def test_change_applies_to_tools(self):
        out = _call("revise", policyId="r1", applies_to_tools=["defi_swap"])
        assert out["details"]["policy"]["applies_to_tools"] == ["defi_swap"]

    def test_change_chain_ids(self):
        out = _call("revise", policyId="r1", chain_ids=[8453, 42161])
        assert out["details"]["policy"]["chain_ids"] == [8453, 42161]

    def test_change_max_amount_wei(self):
        out = _call("revise", policyId="r1", max_amount_wei=999)
        assert out["details"]["policy"]["max_amount_wei"] == 999

    def test_change_max_per_hour(self):
        out = _call("revise", policyId="r1", max_per_hour=100)
        assert out["details"]["policy"]["max_per_hour"] == 100

    def test_change_description(self):
        out = _call("revise", policyId="r1", description="updated rationale")
        assert out["details"]["policy"]["description"] == "updated rationale"

    def test_change_name(self):
        out = _call("revise", policyId="r1", name="r1-new")
        assert out["details"]["policy"]["name"] == "r1-new"

    def test_preserves_unchanged_fields(self):
        # Only change description — everything else should stay.
        out = _call("revise", policyId="r1", description="just touching the description")
        p = out["details"]["policy"]
        assert p["decision"] == "confirm"
        assert p["applies_to_tools"] == ["transfer"]
        assert p["chain_ids"] == [1]
        assert p["max_amount_wei"] == 1_000
        assert p["max_per_hour"] == 5

    def test_not_found(self):
        out = _call("revise", policyId="missing", description="x")
        assert _details(out)["error_code"] == "not_found"

    def test_param_error_invalid_decision(self):
        out = _call("revise", policyId="r1", decision="not-a-valid-decision")
        assert _details(out)["error_code"] == "param_error"

    def test_missing_policy_id(self):
        out = _call("revise", description="x")
        assert _details(out)["error_code"] == "param_error"


# --- disable / enable / delete --------------------------------------------


class TestDisable:
    def test_success(self):
        policy_storage.save_policies([Policy(name="p", decision="block")])
        out = _call("disable", policyId="p")
        assert out["details"]["status"] == "disabled"
        # Active list should now be empty
        assert policy_storage.load_policies() == []

    def test_not_found(self):
        out = _call("disable", policyId="ghost")
        assert _details(out)["error_code"] == "not_found"


class TestEnable:
    def test_success(self):
        from clawmes.tools.policy_manage import _save_disabled

        _save_disabled([Policy(name="off", decision="block")])
        out = _call("enable", policyId="off")
        assert out["details"]["status"] == "active"
        assert any(p.name == "off" for p in policy_storage.load_policies())

    def test_not_found(self):
        out = _call("enable", policyId="ghost")
        assert _details(out)["error_code"] == "not_found"


class TestDelete:
    def test_from_active(self):
        policy_storage.save_policies([Policy(name="p", decision="block")])
        out = _call("delete", policyId="p")
        assert out["details"]["from"] == "active"
        assert policy_storage.load_policies() == []

    def test_from_disabled(self):
        from clawmes.tools.policy_manage import _load_disabled, _save_disabled

        _save_disabled([Policy(name="off", decision="block")])
        out = _call("delete", policyId="off")
        assert out["details"]["from"] == "disabled"
        assert _load_disabled() == []

    def test_not_found(self):
        out = _call("delete", policyId="nope")
        assert _details(out)["error_code"] == "not_found"


# --- evaluate -------------------------------------------------------------


class TestEvaluate:
    def test_allow_when_no_policies(self):
        out = _call("evaluate", toolName="defi_swap")
        d = out["details"]["decision"]
        assert d["kind"] == "allow"
        assert d["policy_name"] == ""

    def test_block_decision(self):
        policy_storage.save_policies(
            [
                Policy(
                    name="block-swap",
                    decision="block",
                    applies_to_tools=("defi_swap",),
                )
            ]
        )
        out = _call("evaluate", toolName="defi_swap")
        d = out["details"]["decision"]
        assert d["kind"] == "block"
        assert d["policy_name"] == "block-swap"

    def test_confirm_with_amount(self):
        policy_storage.save_policies(
            [
                Policy(
                    name="confirm-large-transfer",
                    decision="confirm",
                    applies_to_tools=("transfer",),
                    max_amount_wei=10_000,
                )
            ]
        )
        out = _call(
            "evaluate",
            toolName="transfer",
            value_wei=20_000,
        )
        assert out["details"]["decision"]["kind"] == "confirm"

    def test_with_chain_id(self):
        policy_storage.save_policies(
            [
                Policy(
                    name="block-on-ethereum",
                    decision="block",
                    applies_to_tools=("defi_swap",),
                    chain_ids=(1,),
                )
            ]
        )
        # Wrong chain → allow
        out = _call("evaluate", toolName="defi_swap", chain_id=8453)
        assert out["details"]["decision"]["kind"] == "allow"
        # Right chain → block
        out = _call("evaluate", toolName="defi_swap", chain_id=1)
        assert out["details"]["decision"]["kind"] == "block"

    def test_with_user_id(self):
        # Just exercises the user_id pass-through.
        out = _call("evaluate", toolName="defi_swap", user_id="alice")
        assert out["details"]["input"]["user_id"] == "alice"

    def test_missing_tool_name(self):
        out = _call("evaluate")
        assert _details(out)["error_code"] == "param_error"


# --- usage ----------------------------------------------------------------


class TestUsage:
    def test_no_filter_no_recorded(self):
        out = _call("usage")
        d = out["details"]
        assert d["user_id"] == "default"
        # No invocations recorded → all counts are 0
        assert all(n == 0 for n in d["tool_counts"].values())
        assert "(no recorded invocations)" in out["content"][0]["text"]

    def test_no_filter_with_recorded(self):
        usage_counter_mod.get_usage_counter().record("default", "defi_swap")
        usage_counter_mod.get_usage_counter().record("default", "defi_swap")
        usage_counter_mod.get_usage_counter().record("default", "transfer")
        out = _call("usage")
        counts = out["details"]["tool_counts"]
        assert counts["defi_swap"] == 2
        assert counts["transfer"] == 1
        assert "defi_swap: 2" in out["content"][0]["text"]

    def test_with_policy_filter(self):
        usage_counter_mod.get_usage_counter().record("default", "defi_swap")
        policy_storage.save_policies(
            [
                Policy(
                    name="rate-swaps",
                    decision="confirm",
                    applies_to_tools=("defi_swap",),
                    max_per_hour=10,
                )
            ]
        )
        out = _call("usage", policyId="rate-swaps")
        d = out["details"]
        assert d["policy"] == "rate-swaps"
        assert d["tool_counts"] == {"defi_swap": 1}

    def test_with_policy_filter_catch_all(self):
        # Catch-all policy (applies_to_tools empty) → returns counts for
        # all known tools.
        policy_storage.save_policies([Policy(name="catch-all", decision="confirm")])
        out = _call("usage", policyId="catch-all")
        # Should have an entry for every category tool.
        assert "transfer" in out["details"]["tool_counts"]
        assert "defi_swap" in out["details"]["tool_counts"]

    def test_with_policy_filter_disabled_match(self):
        # Disabled policies are also searched.
        from clawmes.tools.policy_manage import _save_disabled

        _save_disabled(
            [
                Policy(
                    name="off-rate",
                    decision="confirm",
                    applies_to_tools=("transfer",),
                )
            ]
        )
        out = _call("usage", policyId="off-rate")
        assert out["details"]["policy"] == "off-rate"
        assert "transfer" in out["details"]["tool_counts"]

    def test_with_policy_filter_no_tools_summary(self):
        # Catch-all without any tools → applies_to_tools is empty → tools
        # falls back to all known tools, so the "(policy applies to no tools)"
        # branch only fires when the policy explicitly has applies_to_tools
        # set but with no tools — not reachable through the normal API.
        # Exercise the helper directly to cover the branch.
        from clawmes.tools.policy_manage import _format_usage_summary

        text = _format_usage_summary("policy-x", "user", {})
        assert "(policy applies to no tools)" in text

    def test_with_policy_filter_not_found(self):
        out = _call("usage", policyId="nope")
        assert _details(out)["error_code"] == "not_found"

    def test_with_user_id(self):
        usage_counter_mod.get_usage_counter().record("alice", "transfer")
        out = _call("usage", user_id="alice")
        assert out["details"]["tool_counts"]["transfer"] == 1


# --- categories -----------------------------------------------------------


class TestCategories:
    def test_basic(self):
        out = _call("categories")
        cats = out["details"]["categories"]
        assert "wallet" in cats
        assert "trading" in cats
        assert "transfer" in cats["wallet"]
        assert "defi_swap" in cats["trading"]

    def test_summary_truncates_long_category_lists(self):
        out = _call("categories")
        text = out["content"][0]["text"]
        # launchpad has 6 items; the summary trims to 5 + "..."
        assert "..." in text


# --- helpers (direct coverage of branches not reachable via the tool surface) ---


class TestDecodeHelpers:
    def test_int_or_none_passthrough(self):
        from clawmes.tools.policy_manage import _int_or_none

        assert _int_or_none(None) is None
        assert _int_or_none(42) == 42
        assert _int_or_none("17") == 17
        assert _int_or_none("not-a-number") is None
        assert _int_or_none([1, 2, 3]) is None

    def test_decode_policy_rejects_non_dict(self):
        from clawmes.tools.policy_manage import _decode_policy

        assert _decode_policy("not-a-dict") is None
        assert _decode_policy(None) is None
        assert _decode_policy([1, 2]) is None

    def test_decode_policy_rejects_missing_name(self):
        from clawmes.tools.policy_manage import _decode_policy

        assert _decode_policy({"decision": "block"}) is None

    def test_decode_policy_rejects_missing_decision(self):
        from clawmes.tools.policy_manage import _decode_policy

        assert _decode_policy({"name": "p"}) is None

    def test_decode_policy_accepts_full(self):
        from clawmes.tools.policy_manage import _decode_policy

        p = _decode_policy(
            {
                "name": "x",
                "decision": "block",
                "applies_to_tools": ["transfer"],
                "chain_ids": [1, 8453],
                "max_amount_wei": 1000,
                "max_per_hour": 5,
                "description": "test",
            }
        )
        assert p is not None
        assert p.name == "x"
        assert p.applies_to_tools == ("transfer",)
        assert p.chain_ids == (1, 8453)

    def test_decode_policy_handles_bad_chain_ids(self):
        from clawmes.tools.policy_manage import _decode_policy

        # tuple() of a non-iterable triggers TypeError → returns None.
        result = _decode_policy({"name": "x", "decision": "block", "chain_ids": 12345})
        assert result is None

    def test_all_tool_names_dedupes(self):
        from clawmes.tools.policy_manage import _all_tool_names

        names = _all_tool_names()
        # Every tool from _TOOL_CATEGORIES should appear exactly once.
        assert len(names) == len(set(names))
        assert "transfer" in names


class TestLoadDisabledSideCar:
    def test_missing_file_returns_empty(self):
        from clawmes.tools.policy_manage import _disabled_path, _load_disabled

        # By default the side-car doesn't exist.
        assert not _disabled_path().exists()
        assert _load_disabled() == []

    def test_malformed_json_returns_empty(self):
        from clawmes.tools.policy_manage import _disabled_path, _load_disabled

        path = _disabled_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{ not valid json", encoding="utf-8")
        assert _load_disabled() == []

    def test_non_list_returns_empty(self):
        from clawmes.tools.policy_manage import _disabled_path, _load_disabled

        path = _disabled_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"oops": "not a list"}', encoding="utf-8")
        assert _load_disabled() == []

    def test_list_with_bad_entries_skips_them(self):
        from clawmes.tools.policy_manage import _disabled_path, _load_disabled

        path = _disabled_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                [
                    "not-a-dict",
                    {"name": "good", "decision": "block"},
                    {"missing_decision": True},
                ]
            ),
            encoding="utf-8",
        )
        out = _load_disabled()
        assert len(out) == 1
        assert out[0].name == "good"


# --- registration --------------------------------------------------------


class TestRegister:
    def test_register_pushes_into_ctx(self):
        captured: list[dict] = []

        class FakeCtx:
            def register_tool(self, **kw):
                captured.append(kw)

        register(FakeCtx())
        assert len(captured) == 1
        assert captured[0]["name"] == "policy_manage"
        assert captured[0]["toolset"] == "clawmes-policy"


# --- invalid action ------------------------------------------------------


class TestInvalidAction:
    def test_unknown_action(self):
        out = json.loads(policy_manage({"action": "explode"}))
        assert _details(out)["error_code"] == "param_error"

    def test_missing_action(self):
        out = json.loads(policy_manage({}))
        assert _details(out)["error_code"] == "param_error"


# --- confirm_store TTL boundary -----------------------------------------


class TestConfirmStoreInteraction:
    def test_expired_nonce_rejected(self, monkeypatch):
        # Force the store's TTL to zero so any consume sees an expired entry.
        short_lived = confirm_store_mod.ConfirmStore(ttl_seconds=0)
        monkeypatch.setattr(confirm_store_mod, "store", short_lived)
        monkeypatch.setattr(policy_manage_mod, "confirm_store", short_lived)

        proposed = _call(
            "propose",
            name="block-x",
            decision="block",
            applies_to_tools=["transfer"],
        )
        nonce = proposed["details"]["nonce"]
        # Give monotonic clock at least a microsecond to advance past the
        # 0-second TTL.
        time.sleep(0.01)
        out = _call(
            "confirm",
            policyConfirmationNonce=nonce,
            name="block-x",
            decision="block",
            applies_to_tools=["transfer"],
        )
        assert _details(out)["error_code"] == "confirm_failed"
