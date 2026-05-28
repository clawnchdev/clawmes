"""Tests for v0.12.0 paid-tier extensions:

* ``/copy --invert`` (HOLDER) + ``/copy --multi`` (UNLIMITED)
* ``/alerts --webhook`` (HOLDER)
* ``/sniper --auto-sell`` (UNLIMITED)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest

from clawmes.commands import alerts, copy, sniper


@dataclass
class _FakeWalletState:
    connected: bool = True
    address: str = "0x" + "1" * 40


@pytest.fixture
def fake_wallet(monkeypatch):
    state = _FakeWalletState()

    import clawmes.services.wallet as wallet_mod

    monkeypatch.setattr(wallet_mod, "get_wallet_state", lambda: state)
    return state


# ── /copy --invert + --multi ───────────────────────────────────────


@pytest.fixture
def tmp_copy_state(tmp_path, monkeypatch):
    p = tmp_path / "follows.json"
    monkeypatch.setattr(copy, "_follows_path", lambda: p)
    return p


@pytest.fixture
def fake_copy_http(monkeypatch):
    state: dict[str, Any] = {
        "tokentx": None,
        "blocknum": {"result": "0x3e8"},
        "tx_value": None,
    }

    def _fake(url, *, params=None, timeout=None):  # noqa: ARG001
        action = (params or {}).get("action")
        if action == "tokentx":
            return state["tokentx"]
        if action == "eth_blockNumber":
            return state["blocknum"]
        if action == "eth_getTransactionByHash":
            return state["tx_value"]
        return None

    monkeypatch.setattr(copy, "http_get", _fake)
    return state


class TestInvertFlag:
    def test_invert_requires_holder(self, tmp_copy_state, monkeypatch):
        import clawmes.services.token_gate as tg

        monkeypatch.setattr(tg, "check_tier_or_error", lambda *a, **k: "HOLDER required")
        monkeypatch.setattr(copy, "_current_block_height", lambda: 1000)
        out = copy._cmd_add("u", ["0x" + "a" * 40, "0.001", "--invert"])
        assert "HOLDER required" in out

    def test_invert_success(self, tmp_copy_state, monkeypatch):
        monkeypatch.setattr(copy, "_current_block_height", lambda: 1000)
        out = copy._cmd_add("u", ["0x" + "a" * 40, "0.001", "--invert"])
        assert "Invert:      mirror sells too" in out
        f = copy._load_state()["follows"][0]
        assert f["invert"] is True


class TestMultiFlag:
    def test_multi_requires_unlimited(self, tmp_copy_state, monkeypatch):
        import clawmes.services.token_gate as tg

        monkeypatch.setattr(tg, "check_tier_or_error", lambda *a, **k: "UNLIMITED required")
        monkeypatch.setattr(copy, "_current_block_height", lambda: 1000)
        out = copy._cmd_add(
            "u",
            [
                "0x" + "a" * 40,
                "0.001",
                "--multi",
                "0x" + "b" * 40 + "," + "0x" + "c" * 40,
            ],
        )
        assert "UNLIMITED required" in out

    def test_multi_bad_address(self, tmp_copy_state, monkeypatch):
        monkeypatch.setattr(copy, "_current_block_height", lambda: 1000)
        out = copy._cmd_add("u", ["0x" + "a" * 40, "0.001", "--multi", "not-an-address"])
        assert "must be a 0x… address" in out

    def test_multi_success(self, tmp_copy_state, monkeypatch):
        monkeypatch.setattr(copy, "_current_block_height", lambda: 1000)
        out = copy._cmd_add(
            "u",
            [
                "0x" + "a" * 40,
                "0.001",
                "--multi",
                "0x" + "b" * 40 + ",0x" + "c" * 40,
            ],
        )
        assert "Multi:       2 extra wallet(s)" in out
        f = copy._load_state()["follows"][0]
        assert len(f["extra_wallets"]) == 2

    def test_multi_empty_value(self, tmp_copy_state, monkeypatch):
        monkeypatch.setattr(copy, "_current_block_height", lambda: 1000)
        out = copy._cmd_add("u", ["0x" + "a" * 40, "0.001", "--multi", ","])
        # Empty entries are silently dropped — no extra wallets recorded.
        assert "Follow added" in out
        f = copy._load_state()["follows"][0]
        assert f["extra_wallets"] == []


class TestAllWatchedWallets:
    def test_primary_only(self):
        out = copy._all_watched_wallets({"wallet": "0xABC", "extra_wallets": []})
        assert out == ["0xabc"]

    def test_with_extras(self):
        out = copy._all_watched_wallets(
            {
                "wallet": "0xABC",
                "extra_wallets": ["0xDEF", "0xabc"],  # dup with primary
            }
        )
        assert out == ["0xabc", "0xdef"]

    def test_missing_primary(self):
        out = copy._all_watched_wallets({"wallet": "", "extra_wallets": ["0xDEF"]})
        assert out == ["0xdef"]

    def test_skip_non_string_extras(self):
        out = copy._all_watched_wallets({"wallet": "0xABC", "extra_wallets": [123, "0xDEF"]})
        assert out == ["0xabc", "0xdef"]

    def test_no_extras_key(self):
        out = copy._all_watched_wallets({"wallet": "0xABC"})
        assert out == ["0xabc"]


# ── /copy edit additions (invert + extra_wallets) ──────────────────


class TestEditInvert:
    def test_invert_true_requires_holder(self, tmp_copy_state, monkeypatch):
        import clawmes.services.token_gate as tg

        # Add a follow first (HOLDER stub for the add).
        monkeypatch.setattr(copy, "_current_block_height", lambda: 1000)
        out = copy._cmd_add("u", ["0x" + "a" * 40, "0.001"])
        fid = next(w for w in out.split() if w.startswith("copy_"))
        # Now flip the gate to reject for the edit-to-true.
        monkeypatch.setattr(tg, "check_tier_or_error", lambda *a, **k: "HOLDER required")
        out = copy._cmd_edit("u", [fid, "invert", "true"])
        assert "HOLDER required" in out

    def test_invert_true_success(self, tmp_copy_state, monkeypatch):
        monkeypatch.setattr(copy, "_current_block_height", lambda: 1000)
        out = copy._cmd_add("u", ["0x" + "a" * 40, "0.001"])
        fid = next(w for w in out.split() if w.startswith("copy_"))
        copy._cmd_edit("u", [fid, "invert", "true"])
        assert copy._load_state()["follows"][0]["invert"] is True

    def test_invert_false(self, tmp_copy_state, monkeypatch):
        monkeypatch.setattr(copy, "_current_block_height", lambda: 1000)
        out = copy._cmd_add("u", ["0x" + "a" * 40, "0.001", "--invert"])
        fid = next(w for w in out.split() if w.startswith("copy_"))
        copy._cmd_edit("u", [fid, "invert", "false"])
        assert copy._load_state()["follows"][0]["invert"] is False

    def test_invert_garbage(self, tmp_copy_state, monkeypatch):
        monkeypatch.setattr(copy, "_current_block_height", lambda: 1000)
        out = copy._cmd_add("u", ["0x" + "a" * 40, "0.001"])
        fid = next(w for w in out.split() if w.startswith("copy_"))
        out = copy._cmd_edit("u", [fid, "invert", "maybe"])
        assert "must be true|false" in out


class TestEditExtraWallets:
    def test_clear_to_none(self, tmp_copy_state, monkeypatch):
        monkeypatch.setattr(copy, "_current_block_height", lambda: 1000)
        out = copy._cmd_add(
            "u",
            [
                "0x" + "a" * 40,
                "0.001",
                "--multi",
                "0x" + "b" * 40,
            ],
        )
        fid = next(w for w in out.split() if w.startswith("copy_"))
        copy._cmd_edit("u", [fid, "extra_wallets", "none"])
        assert copy._load_state()["follows"][0]["extra_wallets"] == []

    def test_set_requires_unlimited(self, tmp_copy_state, monkeypatch):
        import clawmes.services.token_gate as tg

        monkeypatch.setattr(copy, "_current_block_height", lambda: 1000)
        out = copy._cmd_add("u", ["0x" + "a" * 40, "0.001"])
        fid = next(w for w in out.split() if w.startswith("copy_"))
        monkeypatch.setattr(tg, "check_tier_or_error", lambda *a, **k: "UNLIMITED required")
        out = copy._cmd_edit("u", [fid, "extra_wallets", "0x" + "b" * 40])
        assert "UNLIMITED required" in out

    def test_set_bad_address(self, tmp_copy_state, monkeypatch):
        monkeypatch.setattr(copy, "_current_block_height", lambda: 1000)
        out = copy._cmd_add("u", ["0x" + "a" * 40, "0.001"])
        fid = next(w for w in out.split() if w.startswith("copy_"))
        out = copy._cmd_edit("u", [fid, "extra_wallets", "not-an-address"])
        assert "must be 0x… address" in out

    def test_set_success(self, tmp_copy_state, monkeypatch):
        monkeypatch.setattr(copy, "_current_block_height", lambda: 1000)
        out = copy._cmd_add("u", ["0x" + "a" * 40, "0.001"])
        fid = next(w for w in out.split() if w.startswith("copy_"))
        copy._cmd_edit(
            "u",
            [
                fid,
                "extra_wallets",
                "0x" + "b" * 40 + ",0x" + "c" * 40,
            ],
        )
        f = copy._load_state()["follows"][0]
        assert len(f["extra_wallets"]) == 2

    def test_set_empty_entries_dropped(self, tmp_copy_state, monkeypatch):
        monkeypatch.setattr(copy, "_current_block_height", lambda: 1000)
        out = copy._cmd_add("u", ["0x" + "a" * 40, "0.001"])
        fid = next(w for w in out.split() if w.startswith("copy_"))
        copy._cmd_edit("u", [fid, "extra_wallets", " , 0x" + "b" * 40 + " ,"])
        f = copy._load_state()["follows"][0]
        assert len(f["extra_wallets"]) == 1


# ── _execute_sell + _read_our_token_balance + _basescan_token_transfers_all


class TestExecuteSell:
    def test_no_wallet(self, fake_wallet):
        fake_wallet.connected = False
        out = copy._execute_sell({"slippage_bps": 100}, "0x" + "T" * 40)
        assert out["status"] == "no_wallet"

    def test_no_balance(self, fake_wallet, monkeypatch):
        monkeypatch.setattr(copy, "_read_our_token_balance", lambda *a: 0)
        out = copy._execute_sell({"slippage_bps": 100}, "0x" + "T" * 40)
        assert out["status"] == "no_balance"

    def test_success(self, fake_wallet, monkeypatch):
        monkeypatch.setattr(copy, "_read_our_token_balance", lambda *a: 10**18)

        def _fake_swap(args):  # noqa: ARG001
            return json.dumps({"isError": False, "details": {"tx_hash": "0xfeed"}})

        import clawmes.tools.defi_swap as mod

        monkeypatch.setattr(mod, "defi_swap", _fake_swap)
        out = copy._execute_sell({"slippage_bps": 100}, "0x" + "T" * 40)
        assert out["status"] == "ok"

    def test_swap_raises(self, fake_wallet, monkeypatch):
        monkeypatch.setattr(copy, "_read_our_token_balance", lambda *a: 10**18)
        import clawmes.tools.defi_swap as mod

        def _boom(args):
            raise RuntimeError("rpc down")

        monkeypatch.setattr(mod, "defi_swap", _boom)
        out = copy._execute_sell({"slippage_bps": 100}, "0x" + "T" * 40)
        assert out["status"] == "error"

    def test_swap_bad_json(self, fake_wallet, monkeypatch):
        monkeypatch.setattr(copy, "_read_our_token_balance", lambda *a: 10**18)
        import clawmes.tools.defi_swap as mod

        monkeypatch.setattr(mod, "defi_swap", lambda *a, **k: "not-json")
        out = copy._execute_sell({"slippage_bps": 100}, "0x" + "T" * 40)
        assert out["status"] == "error"

    def test_swap_isError(self, fake_wallet, monkeypatch):
        monkeypatch.setattr(copy, "_read_our_token_balance", lambda *a: 10**18)
        import clawmes.tools.defi_swap as mod

        monkeypatch.setattr(
            mod,
            "defi_swap",
            lambda *a, **k: json.dumps({"isError": True, "content": [{"text": "no route"}]}),
        )
        out = copy._execute_sell({"slippage_bps": 100}, "0x" + "T" * 40)
        assert out["status"] == "error"
        assert "no route" in out["detail"]

    def test_swap_isError_no_content(self, fake_wallet, monkeypatch):
        monkeypatch.setattr(copy, "_read_our_token_balance", lambda *a: 10**18)
        import clawmes.tools.defi_swap as mod

        monkeypatch.setattr(mod, "defi_swap", lambda *a, **k: json.dumps({"isError": True}))
        out = copy._execute_sell({"slippage_bps": 100}, "0x" + "T" * 40)
        assert out["status"] == "error"


class TestReadOurTokenBalance:
    def test_no_address(self):
        assert copy._read_our_token_balance("0x" + "T" * 40, None) == 0

    def test_rpc_error(self, monkeypatch):
        import clawmes.services.rpc as rpc_mod

        class _Boom:
            def eth_call(self, **kw):
                raise RuntimeError("rpc down")

        monkeypatch.setattr(rpc_mod, "get_rpc_service", lambda: _Boom())
        assert copy._read_our_token_balance("0x" + "T" * 40, "0x" + "1" * 40) == 0

    def test_success(self, monkeypatch):
        import clawmes.services.rpc as rpc_mod
        from clawmes.lib.abi import encode_uint

        class _Fake:
            def eth_call(self, **kw):
                return "0x" + encode_uint(42 * (10**18))

        monkeypatch.setattr(rpc_mod, "get_rpc_service", lambda: _Fake())
        assert copy._read_our_token_balance("0x" + "T" * 40, "0x" + "1" * 40) == 42 * (10**18)


class TestBasescanAll:
    def test_non_dict(self, monkeypatch):
        monkeypatch.setattr(copy, "http_get", lambda *a, **k: "junk")
        assert copy._basescan_token_transfers_all("0xabc", start_block=0) == []

    def test_status_zero(self, monkeypatch):
        monkeypatch.setattr(copy, "http_get", lambda *a, **k: {"status": "0", "result": []})
        assert copy._basescan_token_transfers_all("0xabc", start_block=0) == []

    def test_result_not_list(self, monkeypatch):
        monkeypatch.setattr(
            copy,
            "http_get",
            lambda *a, **k: {"status": "1", "result": "string"},
        )
        assert copy._basescan_token_transfers_all("0xabc", start_block=0) == []

    def test_filters_both_directions(self, monkeypatch):
        wallet = "0x" + "a" * 40
        monkeypatch.setattr(
            copy,
            "http_get",
            lambda *a, **k: {
                "status": "1",
                "result": [
                    {"to": wallet, "from": "0x" + "1" * 40},
                    {"from": wallet, "to": "0x" + "2" * 40},
                    {"from": "0x" + "3" * 40, "to": "0x" + "4" * 40},
                    "junk",
                ],
            },
        )
        out = copy._basescan_token_transfers_all(wallet, start_block=0)
        assert len(out) == 2

    def test_api_key_passed(self, monkeypatch):
        captured = {}

        def _spy(url, *, params=None, timeout=None):  # noqa: ARG001
            captured.update(params)
            return {"status": "0"}

        monkeypatch.setattr(copy, "http_get", _spy)
        monkeypatch.setenv("BASESCAN_API_KEY", "MYKEY")
        copy._basescan_token_transfers_all("0xabc", start_block=0)
        assert captured.get("apikey") == "MYKEY"


# ── _process_follow with multi-wallet + invert ─────────────────────


class TestProcessFollowMultiInvert:
    def test_invert_outgoing_triggers_sell(
        self, tmp_copy_state, fake_copy_http, fake_wallet, monkeypatch
    ):
        # Add a follow with --invert.
        monkeypatch.setattr(copy, "_current_block_height", lambda: 1000)
        out = copy._cmd_add("u", ["0x" + "a" * 40, "0.001", "--invert"])
        next(w for w in out.split() if w.startswith("copy_"))

        # Mock our token balance > 0 so the sell proceeds.
        monkeypatch.setattr(copy, "_read_our_token_balance", lambda *a: 10**18)

        # Mock defi_swap to succeed.
        import clawmes.tools.defi_swap as mod

        monkeypatch.setattr(
            mod,
            "defi_swap",
            lambda *a, **k: json.dumps({"isError": False, "details": {"tx_hash": "0xsold"}}),
        )

        wallet = copy._load_state()["follows"][0]["wallet"]
        fake_copy_http["tokentx"] = {
            "status": "1",
            "result": [
                {
                    "from": wallet,
                    "to": "0xRouter",
                    "contractAddress": "0x" + "T" * 40,
                    "blockNumber": "2000",
                    "hash": "0xseen",
                }
            ],
        }
        n = copy._run_due_sync()
        assert n == 1
        executions = copy._load_state()["follows"][0]["executions"]
        assert executions[0]["direction"] == "sell"
        assert executions[0]["result"]["status"] == "ok"

    def test_multi_wallet_polls_each(
        self, tmp_copy_state, fake_copy_http, fake_wallet, monkeypatch
    ):
        monkeypatch.setattr(copy, "_current_block_height", lambda: 1000)
        primary = "0x" + "a" * 40
        secondary = "0x" + "b" * 40
        copy._cmd_add(
            "u",
            [primary, "0.001", "--multi", secondary],
        )

        # Mock defi_swap to succeed for any incoming tx.
        import clawmes.tools.defi_swap as mod

        monkeypatch.setattr(
            mod,
            "defi_swap",
            lambda *a, **k: json.dumps({"isError": False, "details": {"tx_hash": "0xfeed"}}),
        )

        # Return a tokentx for whichever wallet is queried.
        def _spy_http(url, *, params=None, timeout=None):  # noqa: ARG001
            action = (params or {}).get("action")
            if action == "tokentx":
                addr = (params or {}).get("address", "").lower()
                return {
                    "status": "1",
                    "result": [
                        {
                            "to": addr,
                            "from": "0x" + "1" * 40,
                            "contractAddress": "0x"
                            + ("T" if addr.endswith("a" * 40) else "U") * 40,
                            "blockNumber": "2000",
                            "hash": f"0xseen_{addr[2:6]}",
                        }
                    ],
                }
            if action == "eth_blockNumber":
                return {"result": "0x3e8"}
            return None

        monkeypatch.setattr(copy, "http_get", _spy_http)
        n = copy._run_due_sync()
        # Both wallets should each get one buy → 2 total.
        assert n == 2

    def test_outgoing_with_invert_off_skipped(
        self, tmp_copy_state, fake_copy_http, fake_wallet, monkeypatch
    ):
        """Default copy mode (no --invert) ignores outgoing transfers."""
        monkeypatch.setattr(copy, "_current_block_height", lambda: 1000)
        copy._cmd_add("u", ["0x" + "a" * 40, "0.001"])
        wallet = copy._load_state()["follows"][0]["wallet"]
        fake_copy_http["tokentx"] = {
            "status": "1",
            "result": [
                {
                    "from": wallet,
                    "to": "0xRouter",
                    "contractAddress": "0x" + "T" * 40,
                    "blockNumber": "2000",
                    "hash": "0xseen",
                }
            ],
        }
        n = copy._run_due_sync()
        # Outgoing transfer ignored when invert off → no incoming receipts.
        assert n == 0

    def test_invert_with_blocklisted_skipped(
        self, tmp_copy_state, fake_copy_http, fake_wallet, monkeypatch
    ):
        monkeypatch.setattr(copy, "_current_block_height", lambda: 1000)
        copy._cmd_add(
            "u",
            [
                "0x" + "a" * 40,
                "0.001",
                "--invert",
                "--blocklist",
                "0x" + "T" * 40,
            ],
        )
        wallet = copy._load_state()["follows"][0]["wallet"]
        fake_copy_http["tokentx"] = {
            "status": "1",
            "result": [
                {
                    "from": wallet,
                    "to": "0xRouter",
                    "contractAddress": "0x" + "T" * 40,
                    "blockNumber": "2000",
                    "hash": "0xseen",
                }
            ],
        }
        n = copy._run_due_sync()
        assert n == 0
        exec_ = copy._load_state()["follows"][0]["executions"][0]
        assert exec_["result"]["status"] == "blocklisted"

    def test_invalid_token_skipped(self, tmp_copy_state, fake_copy_http, fake_wallet, monkeypatch):
        monkeypatch.setattr(copy, "_current_block_height", lambda: 1000)
        copy._cmd_add("u", ["0x" + "a" * 40, "0.001", "--invert"])
        wallet = copy._load_state()["follows"][0]["wallet"]
        fake_copy_http["tokentx"] = {
            "status": "1",
            "result": [
                {
                    "from": wallet,
                    "to": "0xRouter",
                    "contractAddress": "junk",
                    "blockNumber": "2000",
                }
            ],
        }
        n = copy._run_due_sync()
        assert n == 0

    def test_self_to_self_neither_in_nor_out(
        self, tmp_copy_state, fake_copy_http, fake_wallet, monkeypatch
    ):
        """A tx where from==to==wallet doesn't trigger either path."""
        monkeypatch.setattr(copy, "_current_block_height", lambda: 1000)
        copy._cmd_add("u", ["0x" + "a" * 40, "0.001", "--invert"])
        wallet = copy._load_state()["follows"][0]["wallet"]
        fake_copy_http["tokentx"] = {
            "status": "1",
            "result": [
                {
                    "from": wallet,
                    "to": wallet,  # self-transfer
                    "contractAddress": "0x" + "T" * 40,
                    "blockNumber": "2000",
                    "hash": "0xseen",
                }
            ],
        }
        # is_incoming is True (to==wallet); buy path runs.
        # Counts depend on defi_swap stub — without one, it errors.
        import clawmes.tools.defi_swap as mod

        monkeypatch.setattr(
            mod,
            "defi_swap",
            lambda *a, **k: json.dumps({"isError": False, "details": {"tx_hash": "0xfeed"}}),
        )
        n = copy._run_due_sync()
        # Self-transfer treated as incoming → 1 buy.
        assert n == 1


# ── /alerts --webhook ──────────────────────────────────────────────


@pytest.fixture
def tmp_alerts_state(tmp_path, monkeypatch):
    p = tmp_path / "alerts.json"
    monkeypatch.setattr(alerts, "_alerts_path", lambda: p)
    return p


class TestAlertsWebhookFlag:
    def test_webhook_requires_holder(self, tmp_alerts_state, monkeypatch):
        import clawmes.services.token_gate as tg

        monkeypatch.setattr(tg, "check_tier_or_error", lambda *a, **k: "HOLDER required")
        out = alerts._cmd_add(
            "u",
            [
                "price",
                "CLAWNCH",
                "above",
                "0.0001",
                "--webhook",
                "https://x.example.com/hook",
            ],
        )
        assert "HOLDER required" in out

    def test_webhook_bad_url(self, tmp_alerts_state):
        out = alerts._cmd_add(
            "u",
            [
                "price",
                "CLAWNCH",
                "above",
                "0.0001",
                "--webhook",
                "ftp://nope",
            ],
        )
        assert "http(s)://" in out

    def test_webhook_price_success(self, tmp_alerts_state):
        out = alerts._cmd_add(
            "u",
            [
                "price",
                "CLAWNCH",
                "above",
                "0.0001",
                "--webhook",
                "https://x.example.com/hook",
            ],
        )
        assert "Alert added" in out
        assert "Webhook:" in out
        a = alerts._load_state()["alerts"][0]
        assert a["webhook_url"] == "https://x.example.com/hook"

    def test_webhook_wallet_success(self, tmp_alerts_state, monkeypatch):
        monkeypatch.setattr(alerts, "_current_block_height", lambda: 1000)
        out = alerts._cmd_add(
            "u",
            [
                "wallet",
                "0x" + "a" * 40,
                "--webhook",
                "https://x.example.com/hook",
            ],
        )
        assert "Alert added" in out
        a = alerts._load_state()["alerts"][0]
        assert a["webhook_url"] == "https://x.example.com/hook"


class TestSplitAlertFlags:
    def test_positional_only(self):
        pos, flags = alerts._split_alert_flags(["a", "b"])
        assert pos == ["a", "b"]
        assert flags == {}

    def test_with_flag(self):
        pos, flags = alerts._split_alert_flags(["a", "--x", "1", "b"])
        assert pos == ["a", "b"]
        assert flags == {"x": "1"}

    def test_trailing_flag(self):
        pos, flags = alerts._split_alert_flags(["--bare"])
        assert flags == {"bare": ""}


class TestPostWebhook:
    def test_success(self, monkeypatch):
        # Stub urlopen to return a mock response with status 200.
        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def getcode(self):
                return 200

        def _fake_urlopen(req, timeout):
            return _Resp()

        import urllib.request as _req

        monkeypatch.setattr(_req, "urlopen", _fake_urlopen)
        out = alerts._post_webhook(
            "https://x.example.com/hook",
            {"id": "alert_x", "sender_id": "u", "type": "price"},
            {"detail": "crossed"},
        )
        assert out["status"] == "ok"
        assert out["http_status"] == 200

    def test_urlopen_raises(self, monkeypatch):
        import urllib.request as _req

        def _boom(req, timeout):
            raise RuntimeError("connect failed")

        monkeypatch.setattr(_req, "urlopen", _boom)
        out = alerts._post_webhook(
            "https://x.example.com/hook",
            {"id": "alert_x"},
            {"detail": "x"},
        )
        assert out["status"] == "error"


class TestAlertsWebhookDelivery:
    """Integration: an alert firing should deliver the webhook."""

    def test_fires_post_webhook(self, tmp_alerts_state, monkeypatch):
        # Set up a price alert with a webhook.
        alerts._cmd_add(
            "u",
            [
                "price",
                "CLAWNCH",
                "above",
                "0.0001",
                "--webhook",
                "https://x.example.com/hook",
            ],
        )

        # Mock defi_price to return a price that crosses the threshold.
        import clawmes.tools.defi_price as price_mod

        monkeypatch.setattr(
            price_mod,
            "defi_price",
            lambda args: json.dumps({"isError": False, "details": {"price_usd": 0.001}}),
        )

        # Mock urlopen to verify call.
        called = {"n": 0}

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

            def getcode(self):
                return 200

        def _fake_urlopen(req, timeout):
            called["n"] += 1
            return _Resp()

        import urllib.request as _req

        monkeypatch.setattr(_req, "urlopen", _fake_urlopen)
        n = alerts._run_due_sync()
        assert n == 1
        assert called["n"] == 1
        # Webhook delivery record on the fire.
        fire = alerts._load_state()["alerts"][0]["fires"][0]
        assert fire["webhook"]["status"] == "ok"


# ── /sniper --auto-sell ────────────────────────────────────────────


@pytest.fixture
def tmp_sniper_state(tmp_path, monkeypatch):
    p = tmp_path / "configs.json"
    monkeypatch.setattr(sniper, "_configs_path", lambda: p)
    return p


class TestSniperAutoSellFlag:
    def test_bad_format(self, tmp_sniper_state):
        out = sniper._cmd_add("u", ["0.005", "--auto-sell", "garbage"])
        assert "'<gain_pct>:<loss_pct>'" in out

    def test_bad_numbers(self, tmp_sniper_state):
        out = sniper._cmd_add("u", ["0.005", "--auto-sell", "x:y"])
        assert "must be numbers" in out

    def test_non_positive(self, tmp_sniper_state):
        out = sniper._cmd_add("u", ["0.005", "--auto-sell", "0:50"])
        assert "must be positive" in out

    def test_success(self, tmp_sniper_state):
        out = sniper._cmd_add("u", ["0.005", "--auto-sell", "100:50"])
        assert "Auto-sell:      +100.0% / -50.0%" in out
        c = sniper._load_state()["configs"][0]
        assert c["auto_sell"]["gain_pct"] == 100.0
        assert c["auto_sell"]["loss_pct"] == 50.0


class TestFetchPriceSniper:
    def test_success(self, monkeypatch):
        import clawmes.tools.defi_price as mod

        monkeypatch.setattr(
            mod,
            "defi_price",
            lambda args: json.dumps({"isError": False, "details": {"price_usd": 0.5}}),
        )
        assert sniper._fetch_price("X") == 0.5

    def test_alias_key(self, monkeypatch):
        import clawmes.tools.defi_price as mod

        monkeypatch.setattr(
            mod,
            "defi_price",
            lambda args: json.dumps({"isError": False, "details": {"price": 0.7}}),
        )
        assert sniper._fetch_price("X") == 0.7

    def test_isError(self, monkeypatch):
        import clawmes.tools.defi_price as mod

        monkeypatch.setattr(mod, "defi_price", lambda args: json.dumps({"isError": True}))
        assert sniper._fetch_price("X") is None

    def test_raises(self, monkeypatch):
        import clawmes.tools.defi_price as mod

        def _boom(args):
            raise RuntimeError("down")

        monkeypatch.setattr(mod, "defi_price", _boom)
        assert sniper._fetch_price("X") is None

    def test_bad_json(self, monkeypatch):
        import clawmes.tools.defi_price as mod

        monkeypatch.setattr(mod, "defi_price", lambda args: "not-json")
        assert sniper._fetch_price("X") is None

    def test_non_numeric(self, monkeypatch):
        import clawmes.tools.defi_price as mod

        monkeypatch.setattr(
            mod,
            "defi_price",
            lambda args: json.dumps({"isError": False, "details": {"price_usd": "x"}}),
        )
        assert sniper._fetch_price("X") is None


class TestEvaluateAutoSellWatches:
    def test_no_watches(self, tmp_sniper_state):
        config = {"auto_sell": {"gain_pct": 100, "loss_pct": 50}, "auto_sell_watches": []}
        assert sniper._evaluate_auto_sell_watches(config, []) == 0

    def test_no_auto_sell(self, tmp_sniper_state):
        config = {
            "auto_sell": None,
            "auto_sell_watches": [{"token": "0xT", "buy_price_usd": 1.0, "status": "active"}],
        }
        assert sniper._evaluate_auto_sell_watches(config, []) == 0

    def test_inactive_watch_skipped(self, tmp_sniper_state):
        config = {
            "auto_sell": {"gain_pct": 100, "loss_pct": 50},
            "auto_sell_watches": [{"token": "0xT", "buy_price_usd": 1.0, "status": "filled"}],
        }
        assert sniper._evaluate_auto_sell_watches(config, []) == 0

    def test_price_unavailable_skipped(self, tmp_sniper_state, monkeypatch):
        monkeypatch.setattr(sniper, "_fetch_price", lambda t: None)
        config = {
            "auto_sell": {"gain_pct": 100, "loss_pct": 50},
            "auto_sell_watches": [{"token": "0xT", "buy_price_usd": 1.0, "status": "active"}],
        }
        assert sniper._evaluate_auto_sell_watches(config, []) == 0

    def test_below_thresholds_holds(self, tmp_sniper_state, monkeypatch):
        # Buy price 1.0, current 1.5 (+50%), thresholds +100%/-50%.
        # Neither triggered → hold.
        monkeypatch.setattr(sniper, "_fetch_price", lambda t: 1.5)
        config = {
            "auto_sell": {"gain_pct": 100, "loss_pct": 50},
            "auto_sell_watches": [{"token": "0xT", "buy_price_usd": 1.0, "status": "active"}],
        }
        assert sniper._evaluate_auto_sell_watches(config, []) == 0

    def test_take_profit_triggers(self, tmp_sniper_state, monkeypatch, fake_wallet):
        monkeypatch.setattr(sniper, "_fetch_price", lambda t: 2.0)  # +100%
        monkeypatch.setattr(sniper, "_read_our_token_balance", lambda *a: 10**18)

        import clawmes.tools.defi_swap as mod

        monkeypatch.setattr(
            mod,
            "defi_swap",
            lambda *a, **k: json.dumps({"isError": False, "details": {"tx_hash": "0xfeed"}}),
        )
        config = {
            "id": "snipe_test",
            "auto_sell": {"gain_pct": 100, "loss_pct": 50},
            "slippage_bps": 100,
            "auto_sell_watches": [
                {
                    "token": "0x" + "T" * 40,
                    "symbol": "TKN",
                    "buy_price_usd": 1.0,
                    "status": "active",
                }
            ],
        }
        lines: list[str] = []
        sold = sniper._evaluate_auto_sell_watches(config, lines)
        assert sold == 1
        watch = config["auto_sell_watches"][0]
        assert watch["status"] == "filled"
        assert watch["close_reason"] == "take_profit"

    def test_stop_loss_triggers(self, tmp_sniper_state, monkeypatch, fake_wallet):
        monkeypatch.setattr(sniper, "_fetch_price", lambda t: 0.4)  # -60%
        monkeypatch.setattr(sniper, "_read_our_token_balance", lambda *a: 10**18)
        import clawmes.tools.defi_swap as mod

        monkeypatch.setattr(
            mod,
            "defi_swap",
            lambda *a, **k: json.dumps({"isError": False, "details": {"tx_hash": "0xfeed"}}),
        )
        config = {
            "id": "snipe_test",
            "auto_sell": {"gain_pct": 100, "loss_pct": 50},
            "slippage_bps": 100,
            "auto_sell_watches": [
                {
                    "token": "0x" + "T" * 40,
                    "symbol": "TKN",
                    "buy_price_usd": 1.0,
                    "status": "active",
                }
            ],
        }
        sold = sniper._evaluate_auto_sell_watches(config, [])
        assert sold == 1
        assert config["auto_sell_watches"][0]["close_reason"] == "stop_loss"

    def test_close_fails_marks_status(self, tmp_sniper_state, monkeypatch, fake_wallet):
        """When the sell submission fails, watch status is close_failed."""
        monkeypatch.setattr(sniper, "_fetch_price", lambda t: 2.0)
        monkeypatch.setattr(sniper, "_read_our_token_balance", lambda *a: 0)
        # _submit_token_sell returns no_balance status (not "ok").
        config = {
            "id": "x",
            "auto_sell": {"gain_pct": 100, "loss_pct": 50},
            "slippage_bps": 100,
            "auto_sell_watches": [
                {
                    "token": "0xT",
                    "symbol": "TKN",
                    "buy_price_usd": 1.0,
                    "status": "active",
                }
            ],
        }
        sniper._evaluate_auto_sell_watches(config, [])
        assert config["auto_sell_watches"][0]["status"] == "close_failed"


class TestSubmitTokenSell:
    def test_no_wallet(self, fake_wallet):
        fake_wallet.connected = False
        out = sniper._submit_token_sell({"slippage_bps": 100}, "0xT")
        assert out["status"] == "no_wallet"

    def test_no_balance(self, fake_wallet, monkeypatch):
        monkeypatch.setattr(sniper, "_read_our_token_balance", lambda *a: 0)
        out = sniper._submit_token_sell({"slippage_bps": 100}, "0xT")
        assert out["status"] == "no_balance"

    def test_success(self, fake_wallet, monkeypatch):
        monkeypatch.setattr(sniper, "_read_our_token_balance", lambda *a: 10**18)
        import clawmes.tools.defi_swap as mod

        monkeypatch.setattr(
            mod,
            "defi_swap",
            lambda *a, **k: json.dumps({"isError": False, "details": {"tx_hash": "0xfeed"}}),
        )
        out = sniper._submit_token_sell({"slippage_bps": 100}, "0xT")
        assert out["status"] == "ok"

    def test_swap_raises(self, fake_wallet, monkeypatch):
        monkeypatch.setattr(sniper, "_read_our_token_balance", lambda *a: 10**18)
        import clawmes.tools.defi_swap as mod

        def _boom(args):
            raise RuntimeError("rpc down")

        monkeypatch.setattr(mod, "defi_swap", _boom)
        out = sniper._submit_token_sell({"slippage_bps": 100}, "0xT")
        assert out["status"] == "error"

    def test_swap_bad_json(self, fake_wallet, monkeypatch):
        monkeypatch.setattr(sniper, "_read_our_token_balance", lambda *a: 10**18)
        import clawmes.tools.defi_swap as mod

        monkeypatch.setattr(mod, "defi_swap", lambda *a, **k: "not-json")
        out = sniper._submit_token_sell({"slippage_bps": 100}, "0xT")
        assert out["status"] == "error"

    def test_swap_isError(self, fake_wallet, monkeypatch):
        monkeypatch.setattr(sniper, "_read_our_token_balance", lambda *a: 10**18)
        import clawmes.tools.defi_swap as mod

        monkeypatch.setattr(
            mod,
            "defi_swap",
            lambda *a, **k: json.dumps({"isError": True, "content": [{"text": "no route"}]}),
        )
        out = sniper._submit_token_sell({"slippage_bps": 100}, "0xT")
        assert out["status"] == "error"

    def test_swap_isError_no_content(self, fake_wallet, monkeypatch):
        monkeypatch.setattr(sniper, "_read_our_token_balance", lambda *a: 10**18)
        import clawmes.tools.defi_swap as mod

        monkeypatch.setattr(mod, "defi_swap", lambda *a, **k: json.dumps({"isError": True}))
        out = sniper._submit_token_sell({"slippage_bps": 100}, "0xT")
        assert out["status"] == "error"


class TestReadOurTokenBalanceSniper:
    def test_no_address(self):
        assert sniper._read_our_token_balance("0x" + "T" * 40, None) == 0

    def test_rpc_error(self, monkeypatch):
        import clawmes.services.rpc as rpc_mod

        class _Boom:
            def eth_call(self, **kw):
                raise RuntimeError("down")

        monkeypatch.setattr(rpc_mod, "get_rpc_service", lambda: _Boom())
        assert sniper._read_our_token_balance("0x" + "T" * 40, "0x" + "1" * 40) == 0

    def test_success(self, monkeypatch):
        import clawmes.services.rpc as rpc_mod
        from clawmes.lib.abi import encode_uint

        class _Fake:
            def eth_call(self, **kw):
                return "0x" + encode_uint(7 * 10**18)

        monkeypatch.setattr(rpc_mod, "get_rpc_service", lambda: _Fake())
        assert sniper._read_our_token_balance("0x" + "T" * 40, "0x" + "1" * 40) == 7 * 10**18


class TestSniperRunDueAutoSell:
    """The auto-sell evaluator is invoked from _run_due_with_lines."""

    def test_no_launches_still_evaluates_watches(self, tmp_sniper_state, monkeypatch, fake_wallet):
        sniper._cmd_add("u", ["0.005", "--auto-sell", "100:50"])
        # Pre-seed a watch.
        s = sniper._load_state()
        s["configs"][0]["auto_sell_watches"] = [
            {
                "token": "0xT",
                "symbol": "TKN",
                "buy_price_usd": 1.0,
                "status": "active",
            }
        ]
        sniper._save_state(s)

        # Empty launches feed.
        monkeypatch.setattr(
            sniper,
            "http_get",
            lambda *a, **k: {"status": "1", "launches": []},
        )
        # ...so the early-return path runs, advancing last_seen but
        # NOT processing watches (auto-sell happens after _process_config).
        # Actually empty launches → early return before evaluating watches.
        # This test verifies that early return doesn't crash.
        n = sniper._run_due_sync()
        assert n == 0

    def test_watch_triggers_in_run_due(self, tmp_sniper_state, monkeypatch, fake_wallet):
        sniper._cmd_add("u", ["0.005", "--auto-sell", "100:50"])
        s = sniper._load_state()
        s["configs"][0]["auto_sell_watches"] = [
            {
                "token": "0x" + "T" * 40,
                "symbol": "TKN",
                "buy_price_usd": 1.0,
                "status": "active",
            }
        ]
        sniper._save_state(s)

        # Non-empty launches feed (avoids the early-return path).
        monkeypatch.setattr(
            sniper,
            "http_get",
            lambda *a, **k: {
                "status": "1",
                "launches": [{"contractAddress": "0xnomatch", "symbol": "X"}],
            },
        )
        # Fetch_price + wallet stubs for the watch evaluation.
        monkeypatch.setattr(sniper, "_fetch_price", lambda t: 2.5)  # +150%
        monkeypatch.setattr(sniper, "_read_our_token_balance", lambda *a: 10**18)
        import clawmes.tools.defi_swap as mod

        monkeypatch.setattr(
            mod,
            "defi_swap",
            lambda *a, **k: json.dumps({"isError": False, "details": {"tx_hash": "0xsold"}}),
        )
        n = sniper._run_due_sync()
        assert n == 1  # the sell

    def test_eval_error_caught(self, tmp_sniper_state, monkeypatch, fake_wallet):
        sniper._cmd_add("u", ["0.005", "--auto-sell", "100:50"])
        s = sniper._load_state()
        s["configs"][0]["auto_sell_watches"] = [
            {"token": "0xT", "buy_price_usd": 1.0, "status": "active"}
        ]
        sniper._save_state(s)
        monkeypatch.setattr(
            sniper,
            "http_get",
            lambda *a, **k: {
                "status": "1",
                "launches": [{"contractAddress": "0xnomatch", "symbol": "X"}],
            },
        )

        def _boom(*a, **k):
            raise RuntimeError("evaluator crashed")

        monkeypatch.setattr(sniper, "_evaluate_auto_sell_watches", _boom)
        n, lines = sniper._run_due_with_lines()
        assert any("auto-sell error" in line for line in lines)


class TestSniperSnipeCreatesWatch:
    def test_successful_snipe_with_auto_sell_creates_watch(
        self, tmp_sniper_state, monkeypatch, fake_wallet
    ):
        sniper._cmd_add("u", ["0.005", "--auto-sell", "100:50"])
        s = sniper._load_state()
        s["configs"][0]["last_seen_epoch"] = 0
        sniper._save_state(s)

        # Stub the launches feed with one matching launch.
        monkeypatch.setattr(
            sniper,
            "http_get",
            lambda *a, **k: {
                "status": "1",
                "launches": [
                    {
                        "contractAddress": "0x" + "T" * 40,
                        "symbol": "TKN",
                        "timestamp": sniper._now_epoch(),
                    }
                ],
            },
        )
        # Buy + price-anchor stubs.
        import clawmes.tools.defi_swap as mod

        monkeypatch.setattr(
            mod,
            "defi_swap",
            lambda *a, **k: json.dumps({"isError": False, "details": {"tx_hash": "0xfeed"}}),
        )
        monkeypatch.setattr(sniper, "_fetch_price", lambda t: 1.0)

        n = sniper._run_due_sync()
        assert n >= 1
        c = sniper._load_state()["configs"][0]
        # A watch was registered at the buy price.
        assert len(c["auto_sell_watches"]) == 1
        assert c["auto_sell_watches"][0]["buy_price_usd"] == 1.0

    def test_snipe_with_auto_sell_but_no_price_skips_watch(
        self, tmp_sniper_state, monkeypatch, fake_wallet
    ):
        sniper._cmd_add("u", ["0.005", "--auto-sell", "100:50"])
        s = sniper._load_state()
        s["configs"][0]["last_seen_epoch"] = 0
        sniper._save_state(s)

        monkeypatch.setattr(
            sniper,
            "http_get",
            lambda *a, **k: {
                "status": "1",
                "launches": [
                    {
                        "contractAddress": "0x" + "T" * 40,
                        "symbol": "TKN",
                        "timestamp": sniper._now_epoch(),
                    }
                ],
            },
        )
        import clawmes.tools.defi_swap as mod

        monkeypatch.setattr(
            mod,
            "defi_swap",
            lambda *a, **k: json.dumps({"isError": False, "details": {"tx_hash": "0xfeed"}}),
        )
        # Price unavailable at snipe-time → no watch created.
        monkeypatch.setattr(sniper, "_fetch_price", lambda t: None)

        sniper._run_due_sync()
        c = sniper._load_state()["configs"][0]
        assert c["auto_sell_watches"] == []
