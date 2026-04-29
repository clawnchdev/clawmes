"""Tests for permit2, compound_action, manage_orders."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from clawmes.wallet.state import WalletState

OWNER = "0x" + "a" * 40
TOKEN = "0x" + "b" * 40
SPENDER = "0x" + "c" * 40


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    from clawmes.policy import storage as policy_storage
    from clawmes.services import rpc as rpc_mod
    from clawmes.services import wallet as wallet_mod

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(wallet_mod, "_instance", None)
    monkeypatch.setattr(rpc_mod, "_instance", None)
    policy_storage.save_policies([])


@pytest.fixture
def connected(monkeypatch):
    state = WalletState.for_chain(mode="local", address=OWNER, chain_id=1)
    monkeypatch.setattr("clawmes.tools.permit2.get_wallet_state", lambda: state)
    return state


@pytest.fixture
def fake_mode(monkeypatch):
    from clawmes.services import wallet as wallet_mod

    mode = MagicMock()
    mode.send_transaction.return_value = "0x" + "f" * 64
    mode.sign_typed_data_v4.return_value = "0x" + "b" * 130
    svc = MagicMock()
    svc.active_mode = mode
    monkeypatch.setattr(wallet_mod, "get_wallet_service", lambda: svc)
    return mode


# --- permit2 ---


class TestPermit2Sign:
    def test_no_wallet(self, monkeypatch):
        monkeypatch.setattr(
            "clawmes.tools.permit2.get_wallet_state",
            lambda: WalletState.disconnected(),
        )
        from clawmes.tools.permit2 import permit2

        out = json.loads(
            permit2(
                {
                    "action": "sign",
                    "token": TOKEN,
                    "spender": SPENDER,
                    "amount": "100",
                }
            )
        )
        assert out["isError"] is True

    def test_basic_sign(self, connected, fake_mode):
        from clawmes.tools.permit2 import permit2

        out = json.loads(
            permit2(
                {
                    "action": "sign",
                    "token": TOKEN,
                    "spender": SPENDER,
                    "amount": "1000",
                }
            )
        )
        assert "isError" not in out
        assert out["details"]["signature"] == "0x" + "b" * 130

    def test_unlimited(self, connected, fake_mode):
        from clawmes.tools.permit2 import permit2

        out = json.loads(
            permit2(
                {
                    "action": "sign",
                    "token": TOKEN,
                    "spender": SPENDER,
                    "amount": "unlimited",
                }
            )
        )
        assert "isError" not in out
        # uint160 max
        assert out["details"]["amount"] == str((1 << 160) - 1)

    def test_invalid_token(self, connected, fake_mode):
        from clawmes.tools.permit2 import permit2

        out = json.loads(
            permit2(
                {
                    "action": "sign",
                    "token": "0xshort",
                    "spender": SPENDER,
                    "amount": "1",
                }
            )
        )
        assert out["isError"] is True

    def test_invalid_spender(self, connected, fake_mode):
        from clawmes.tools.permit2 import permit2

        out = json.loads(
            permit2(
                {
                    "action": "sign",
                    "token": TOKEN,
                    "spender": "bad",
                    "amount": "1",
                }
            )
        )
        assert out["isError"] is True

    def test_bad_amount(self, connected, fake_mode):
        from clawmes.tools.permit2 import permit2

        out = json.loads(
            permit2(
                {
                    "action": "sign",
                    "token": TOKEN,
                    "spender": SPENDER,
                    "amount": "garbage",
                }
            )
        )
        assert out["isError"] is True

    def test_no_active_mode(self, connected, monkeypatch):
        from clawmes.services import wallet as wallet_mod

        svc = MagicMock()
        svc.active_mode = None
        monkeypatch.setattr(wallet_mod, "get_wallet_service", lambda: svc)
        from clawmes.tools.permit2 import permit2

        out = json.loads(
            permit2(
                {
                    "action": "sign",
                    "token": TOKEN,
                    "spender": SPENDER,
                    "amount": "1",
                }
            )
        )
        assert out["isError"] is True

    def test_sign_raises(self, connected, fake_mode):
        fake_mode.sign_typed_data_v4.side_effect = RuntimeError("rejected")
        from clawmes.tools.permit2 import permit2

        out = json.loads(
            permit2(
                {
                    "action": "sign",
                    "token": TOKEN,
                    "spender": SPENDER,
                    "amount": "1",
                }
            )
        )
        assert out["isError"] is True
        assert out["details"]["error_code"] == "send_failed"

    def test_explicit_expiration(self, connected, fake_mode):
        from clawmes.tools.permit2 import permit2

        out = json.loads(
            permit2(
                {
                    "action": "sign",
                    "token": TOKEN,
                    "spender": SPENDER,
                    "amount": "1",
                    "expiration": 9999999999,
                }
            )
        )
        assert "isError" not in out
        assert out["details"]["expiration"] == 9999999999


class TestPermit2Revoke:
    def test_basic(self, connected, fake_mode):
        from clawmes.tools.permit2 import permit2

        out = json.loads(permit2({"action": "revoke", "token": TOKEN, "spender": SPENDER}))
        assert "isError" not in out
        kwargs = fake_mode.send_transaction.call_args.kwargs
        # Permit2 contract address (canonical)
        assert kwargs["to"].lower() == "0x000000000022d473030f116ddee9f6b43ac78ba3"
        # approve selector
        assert kwargs["data"].startswith("0x87517c45")

    def test_invalid_token(self, connected, fake_mode):
        from clawmes.tools.permit2 import permit2

        out = json.loads(permit2({"action": "revoke", "token": "0xs", "spender": SPENDER}))
        assert out["isError"] is True

    def test_invalid_spender(self, connected, fake_mode):
        from clawmes.tools.permit2 import permit2

        out = json.loads(permit2({"action": "revoke", "token": TOKEN, "spender": "bad"}))
        assert out["isError"] is True

    def test_no_active_mode(self, connected, monkeypatch):
        from clawmes.services import wallet as wallet_mod

        svc = MagicMock()
        svc.active_mode = None
        monkeypatch.setattr(wallet_mod, "get_wallet_service", lambda: svc)
        from clawmes.tools.permit2 import permit2

        out = json.loads(permit2({"action": "revoke", "token": TOKEN, "spender": SPENDER}))
        assert out["isError"] is True

    def test_send_failure(self, connected, fake_mode):
        fake_mode.send_transaction.side_effect = RuntimeError("rejected")
        from clawmes.tools.permit2 import permit2

        out = json.loads(permit2({"action": "revoke", "token": TOKEN, "spender": SPENDER}))
        assert out["isError"] is True


class TestPermit2List:
    def test_basic(self, connected, monkeypatch):
        from clawmes.services import rpc as rpc_mod

        svc = MagicMock()
        # uint160 amount + uint48 expiration + uint48 nonce, packed as
        # 3 × 32-byte slots
        body = format(1000, "064x") + format(9999999999, "064x") + format(0, "064x")
        svc.eth_call.return_value = "0x" + body
        monkeypatch.setattr(rpc_mod, "_instance", svc)

        from clawmes.tools.permit2 import permit2

        out = json.loads(permit2({"action": "list", "token": TOKEN, "spender": SPENDER}))
        assert "isError" not in out
        assert out["details"]["amount"] == "1000"
        assert out["details"]["expiration"] == 9999999999

    def test_short_response(self, connected, monkeypatch):
        from clawmes.services import rpc as rpc_mod

        svc = MagicMock()
        svc.eth_call.return_value = "0x1234"
        monkeypatch.setattr(rpc_mod, "_instance", svc)

        from clawmes.tools.permit2 import permit2

        out = json.loads(permit2({"action": "list", "token": TOKEN, "spender": SPENDER}))
        assert out["isError"] is True

    def test_rpc_error(self, connected, monkeypatch):
        from clawmes.services import rpc as rpc_mod
        from clawmes.services.rpc import RpcError

        svc = MagicMock()
        svc.eth_call.side_effect = RpcError(-32000, "no node", method="eth_call")
        monkeypatch.setattr(rpc_mod, "_instance", svc)
        from clawmes.tools.permit2 import permit2

        out = json.loads(permit2({"action": "list", "token": TOKEN, "spender": SPENDER}))
        assert out["isError"] is True


# --- compound_action ---


class TestCompoundAction:
    def test_create(self, monkeypatch):
        from clawmes.plans import scheduler as sched_mod

        fake = MagicMock()
        fake.create_plan.return_value = {"plan_id": "p1"}
        monkeypatch.setattr(sched_mod, "_instance", fake)

        from clawmes.tools.compound_action import compound_action

        out = json.loads(compound_action({"action": "create", "plan": "DCA $100/week"}))
        assert "isError" not in out

    def test_validate(self, monkeypatch):
        from clawmes.plans import scheduler as sched_mod

        fake = MagicMock()
        fake.validate_plan.return_value = {"valid": True}
        monkeypatch.setattr(sched_mod, "_instance", fake)

        from clawmes.tools.compound_action import compound_action

        out = json.loads(compound_action({"action": "validate", "plan": "DCA"}))
        assert "isError" not in out

    def test_dry_run(self, monkeypatch):
        from clawmes.plans import scheduler as sched_mod

        fake = MagicMock()
        fake.dry_run.return_value = {"steps": []}
        monkeypatch.setattr(sched_mod, "_instance", fake)

        from clawmes.tools.compound_action import compound_action

        out = json.loads(compound_action({"action": "dry_run", "plan": "DCA"}))
        assert "isError" not in out

    def test_cancel(self, monkeypatch):
        from clawmes.plans import scheduler as sched_mod

        fake = MagicMock()
        fake.cancel_plan.return_value = {"cancelled": True}
        monkeypatch.setattr(sched_mod, "_instance", fake)

        from clawmes.tools.compound_action import compound_action

        out = json.loads(compound_action({"action": "cancel", "plan_id": "p1"}))
        assert "isError" not in out

    def test_list(self, monkeypatch):
        from clawmes.plans import scheduler as sched_mod

        fake = MagicMock()
        fake.list_plans.return_value = []
        monkeypatch.setattr(sched_mod, "_instance", fake)

        from clawmes.tools.compound_action import compound_action

        out = json.loads(compound_action({"action": "list"}))
        assert "isError" not in out

    def test_logs(self, monkeypatch):
        from clawmes.plans import scheduler as sched_mod

        fake = MagicMock()
        fake.get_plan_logs.return_value = []
        monkeypatch.setattr(sched_mod, "_instance", fake)

        from clawmes.tools.compound_action import compound_action

        out = json.loads(compound_action({"action": "logs", "plan_id": "p1"}))
        assert "isError" not in out

    def test_scheduler_attribute_missing(self, monkeypatch):
        from clawmes.plans import scheduler as sched_mod

        fake = MagicMock(spec=[])  # No methods at all
        monkeypatch.setattr(sched_mod, "_instance", fake)

        from clawmes.tools.compound_action import compound_action

        out = json.loads(compound_action({"action": "list"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "not_implemented"

    def test_scheduler_raises(self, monkeypatch):
        from clawmes.plans import scheduler as sched_mod

        fake = MagicMock()
        fake.list_plans.side_effect = RuntimeError("backend down")
        monkeypatch.setattr(sched_mod, "_instance", fake)

        from clawmes.tools.compound_action import compound_action

        out = json.loads(compound_action({"action": "list"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "scheduler_error"

    def test_scheduler_not_available(self, monkeypatch):
        # Force ImportError on the scheduler import

        from clawmes.tools import compound_action as ca_mod

        monkeypatch.setattr(ca_mod, "_get_scheduler", lambda: None)

        from clawmes.tools.compound_action import compound_action

        out = json.loads(compound_action({"action": "list"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "not_available"

    def test_get_scheduler_import_error(self, monkeypatch):
        # Cover the ImportError branch in _get_scheduler
        import builtins

        import clawmes.tools.compound_action as ca_mod

        original = builtins.__import__

        def fake_import(name, *a, **kw):
            if name == "clawmes.plans.scheduler":
                raise ImportError("simulated")
            return original(name, *a, **kw)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        assert ca_mod._get_scheduler() is None


# --- manage_orders ---


class TestManageOrders:
    def test_list_empty(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        from clawmes.tools.manage_orders import manage_orders

        out = json.loads(manage_orders({"action": "list"}))
        assert "isError" not in out
        assert out["details"]["count"] == 0

    def test_create_limit_buy(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        from clawmes.tools.manage_orders import manage_orders

        out = json.loads(
            manage_orders(
                {
                    "action": "limit_buy",
                    "token": TOKEN,
                    "amount": "1",
                    "trigger_price": "1500",
                }
            )
        )
        assert "isError" not in out

        # List should now show the order
        out2 = json.loads(manage_orders({"action": "list"}))
        assert out2["details"]["count"] == 1

    def test_create_limit_sell(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        from clawmes.tools.manage_orders import manage_orders

        out = json.loads(
            manage_orders(
                {
                    "action": "limit_sell",
                    "token": TOKEN,
                    "amount": "1",
                    "trigger_price": "5000",
                }
            )
        )
        assert "isError" not in out

    def test_create_stop(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        from clawmes.tools.manage_orders import manage_orders

        out = json.loads(
            manage_orders(
                {
                    "action": "stop",
                    "token": TOKEN,
                    "amount": "1",
                    "trigger_price": "1000",
                }
            )
        )
        assert "isError" not in out

    def test_create_trailing(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        from clawmes.tools.manage_orders import manage_orders

        out = json.loads(
            manage_orders(
                {
                    "action": "trailing",
                    "token": TOKEN,
                    "amount": "1",
                    "trail_pct": 0.05,
                }
            )
        )
        assert "isError" not in out

    def test_create_dca(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        from clawmes.tools.manage_orders import manage_orders

        out = json.loads(
            manage_orders(
                {
                    "action": "dca",
                    "token": TOKEN,
                    "amount": "1000",
                    "chunks": 10,
                    "interval_seconds": 86400,
                }
            )
        )
        assert "isError" not in out

    def test_cancel(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        from clawmes.tools.manage_orders import manage_orders

        # Create then cancel
        create_out = json.loads(
            manage_orders({"action": "limit_buy", "token": TOKEN, "amount": "1"})
        )
        order_id = create_out["details"]["id"]

        cancel_out = json.loads(manage_orders({"action": "cancel", "order_id": order_id}))
        assert "isError" not in cancel_out

    def test_cancel_not_found(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        from clawmes.tools.manage_orders import manage_orders

        out = json.loads(manage_orders({"action": "cancel", "order_id": "no-such"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "not_found"

    def test_list_skips_corrupt(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        from clawmes.tools.manage_orders import _orders_dir, manage_orders

        d = _orders_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / "corrupt.json").write_text("not-json", encoding="utf-8")

        out = json.loads(manage_orders({"action": "list"}))
        # Corrupt file silently skipped
        assert out["details"]["count"] == 0

    def test_cancel_unlink_failure(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        from clawmes.tools.manage_orders import _orders_dir, manage_orders

        d = _orders_dir()
        d.mkdir(parents=True, exist_ok=True)
        path = d / "test.json"
        path.write_text(json.dumps({"id": "test"}))

        # Stub Path.unlink to raise
        def bad_unlink(self, *args, **kwargs):
            raise OSError("permission denied")

        monkeypatch.setattr(type(path), "unlink", bad_unlink)
        out = json.loads(manage_orders({"action": "cancel", "order_id": "test"}))
        assert out["isError"] is True

    def test_create_storage_failure(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        # Patch Path.write_text to raise
        from pathlib import Path

        from clawmes.tools.manage_orders import manage_orders

        def bad_write(self, *args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(Path, "write_text", bad_write)
        out = json.loads(manage_orders({"action": "limit_buy", "token": TOKEN, "amount": "1"}))
        assert out["isError"] is True


# --- registers ---


class TestRegister:
    @pytest.mark.parametrize(
        "module_path,name",
        [
            ("clawmes.tools.permit2", "permit2"),
            ("clawmes.tools.compound_action", "compound_action"),
            ("clawmes.tools.manage_orders", "manage_orders"),
        ],
    )
    def test_register(self, module_path, name):
        import importlib

        mod = importlib.import_module(module_path)
        recorded = []

        class FakeCtx:
            def register_tool(self, **kw):
                recorded.append(kw)

        mod.register(FakeCtx())
        assert recorded[0]["name"] == name
