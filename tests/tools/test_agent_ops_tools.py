"""Tests for the 4 agent-ops tools: molten, clawnx, hummingbot, wayfinder."""

from __future__ import annotations

import json

import pytest


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    from clawmes.policy import storage as policy_storage

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    for k in (
        "MOLTEN_API_KEY",
        "CLAWNX_API_KEY",
        "HUMMINGBOT_API_KEY",
        "HUMMINGBOT_GATEWAY_URL",
        "WAYFINDER_API_KEY",
    ):
        monkeypatch.delenv(k, raising=False)
    policy_storage.save_policies([])


def _stub(monkeypatch, module_path: str, attr: str, response):
    def fake(*args, **kwargs):
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(f"{module_path}.{attr}", fake)


# --- molten --------------------------------------------------------------


class TestMolten:
    def test_no_key(self):
        from clawmes.tools.molten import molten

        out = json.loads(molten({"action": "search", "query": "x"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "no_credentials"

    def test_post(self, monkeypatch):
        monkeypatch.setenv("MOLTEN_API_KEY", "k")
        _stub(monkeypatch, "clawmes.tools.molten", "http_post", {"id": "t1"})
        from clawmes.tools.molten import molten

        out = json.loads(molten({"action": "post", "text": "hi"}))
        assert "isError" not in out

    def test_search(self, monkeypatch):
        monkeypatch.setenv("MOLTEN_API_KEY", "k")
        _stub(monkeypatch, "clawmes.tools.molten", "http_get", {"tweets": []})
        from clawmes.tools.molten import molten

        out = json.loads(molten({"action": "search", "query": "ethereum"}))
        assert "isError" not in out

    def test_mention(self, monkeypatch):
        monkeypatch.setenv("MOLTEN_API_KEY", "k")
        _stub(monkeypatch, "clawmes.tools.molten", "http_get", {"mentions": []})
        from clawmes.tools.molten import molten

        out = json.loads(molten({"action": "mention"}))
        assert "isError" not in out

    def test_dm(self, monkeypatch):
        monkeypatch.setenv("MOLTEN_API_KEY", "k")
        _stub(monkeypatch, "clawmes.tools.molten", "http_post", {"id": "d1"})
        from clawmes.tools.molten import molten

        out = json.loads(molten({"action": "dm", "to_user": "alice", "text": "hi"}))
        assert "isError" not in out

    def test_api_error(self, monkeypatch):
        monkeypatch.setenv("MOLTEN_API_KEY", "k")
        _stub(
            monkeypatch,
            "clawmes.tools.molten",
            "http_get",
            RuntimeError("network"),
        )
        from clawmes.tools.molten import molten

        out = json.loads(molten({"action": "mention"}))
        assert out["isError"] is True


# --- clawnx --------------------------------------------------------------


class TestClawnx:
    def test_no_key(self):
        from clawmes.tools.clawnx import clawnx

        out = json.loads(clawnx({"action": "list"}))
        assert out["isError"] is True

    def test_match(self, monkeypatch):
        monkeypatch.setenv("CLAWNX_API_KEY", "k")
        _stub(monkeypatch, "clawmes.tools.clawnx", "http_post", {"agents": []})
        from clawmes.tools.clawnx import clawnx

        out = json.loads(clawnx({"action": "match", "criteria": {"skill": "swap"}}))
        assert "isError" not in out

    def test_match_non_dict_criteria(self, monkeypatch):
        monkeypatch.setenv("CLAWNX_API_KEY", "k")
        _stub(monkeypatch, "clawmes.tools.clawnx", "http_post", {"agents": []})
        from clawmes.tools.clawnx import clawnx

        out = json.loads(clawnx({"action": "match", "criteria": "not-a-dict"}))
        # Falls back to empty dict; still succeeds
        assert "isError" not in out

    def test_list(self, monkeypatch):
        monkeypatch.setenv("CLAWNX_API_KEY", "k")
        _stub(monkeypatch, "clawmes.tools.clawnx", "http_get", {"agents": []})
        from clawmes.tools.clawnx import clawnx

        out = json.loads(clawnx({"action": "list"}))
        assert "isError" not in out

    def test_request(self, monkeypatch):
        monkeypatch.setenv("CLAWNX_API_KEY", "k")
        _stub(monkeypatch, "clawmes.tools.clawnx", "http_post", {"req_id": "r1"})
        from clawmes.tools.clawnx import clawnx

        out = json.loads(clawnx({"action": "request", "agent_id": "a1", "payload": {}}))
        assert "isError" not in out

    def test_request_non_dict_payload(self, monkeypatch):
        monkeypatch.setenv("CLAWNX_API_KEY", "k")
        _stub(monkeypatch, "clawmes.tools.clawnx", "http_post", {"ok": True})
        from clawmes.tools.clawnx import clawnx

        out = json.loads(clawnx({"action": "request", "agent_id": "a1", "payload": "x"}))
        assert "isError" not in out

    def test_accept(self, monkeypatch):
        monkeypatch.setenv("CLAWNX_API_KEY", "k")
        _stub(monkeypatch, "clawmes.tools.clawnx", "http_post", {"ok": True})
        from clawmes.tools.clawnx import clawnx

        out = json.loads(clawnx({"action": "accept", "request_id": "r1"}))
        assert "isError" not in out

    def test_api_error(self, monkeypatch):
        monkeypatch.setenv("CLAWNX_API_KEY", "k")
        _stub(
            monkeypatch,
            "clawmes.tools.clawnx",
            "http_get",
            RuntimeError("network"),
        )
        from clawmes.tools.clawnx import clawnx

        out = json.loads(clawnx({"action": "list"}))
        assert out["isError"] is True


# --- hummingbot ----------------------------------------------------------


class TestHummingbot:
    def test_status(self, monkeypatch):
        _stub(
            monkeypatch,
            "clawmes.tools.hummingbot",
            "http_get",
            {"running": []},
        )
        from clawmes.tools.hummingbot import hummingbot

        out = json.loads(hummingbot({"action": "status"}))
        assert "isError" not in out

    def test_strategies(self, monkeypatch):
        _stub(
            monkeypatch,
            "clawmes.tools.hummingbot",
            "http_get",
            {"templates": []},
        )
        from clawmes.tools.hummingbot import hummingbot

        out = json.loads(hummingbot({"action": "strategies"}))
        assert "isError" not in out

    def test_pnl(self, monkeypatch):
        _stub(monkeypatch, "clawmes.tools.hummingbot", "http_get", {"total": 0})
        from clawmes.tools.hummingbot import hummingbot

        out = json.loads(hummingbot({"action": "pnl"}))
        assert "isError" not in out

    def test_start(self, monkeypatch):
        _stub(
            monkeypatch,
            "clawmes.tools.hummingbot",
            "http_post",
            {"started": True},
        )
        from clawmes.tools.hummingbot import hummingbot

        out = json.loads(
            hummingbot(
                {
                    "action": "start",
                    "strategy_id": "pmm-1",
                    "config": {"market": "ETH/USDC"},
                }
            )
        )
        assert "isError" not in out

    def test_start_non_dict_config(self, monkeypatch):
        _stub(monkeypatch, "clawmes.tools.hummingbot", "http_post", {"ok": True})
        from clawmes.tools.hummingbot import hummingbot

        out = json.loads(hummingbot({"action": "start", "strategy_id": "p", "config": "garbage"}))
        assert "isError" not in out

    def test_stop(self, monkeypatch):
        _stub(monkeypatch, "clawmes.tools.hummingbot", "http_post", {"ok": True})
        from clawmes.tools.hummingbot import hummingbot

        out = json.loads(hummingbot({"action": "stop", "strategy_id": "p"}))
        assert "isError" not in out

    def test_with_api_key(self, monkeypatch):
        monkeypatch.setenv("HUMMINGBOT_API_KEY", "k")
        _stub(monkeypatch, "clawmes.tools.hummingbot", "http_get", {"running": []})
        from clawmes.tools.hummingbot import hummingbot

        out = json.loads(hummingbot({"action": "status"}))
        assert "isError" not in out

    def test_custom_gateway(self, monkeypatch):
        monkeypatch.setenv("HUMMINGBOT_GATEWAY_URL", "http://custom:9999")
        _stub(monkeypatch, "clawmes.tools.hummingbot", "http_get", {"running": []})
        from clawmes.tools.hummingbot import hummingbot

        out = json.loads(hummingbot({"action": "status"}))
        assert "isError" not in out

    def test_api_error(self, monkeypatch):
        _stub(
            monkeypatch,
            "clawmes.tools.hummingbot",
            "http_get",
            RuntimeError("connection refused"),
        )
        from clawmes.tools.hummingbot import hummingbot

        out = json.loads(hummingbot({"action": "status"}))
        assert out["isError"] is True


# --- wayfinder -----------------------------------------------------------


class TestWayfinder:
    def test_no_key(self):
        from clawmes.tools.wayfinder import wayfinder

        out = json.loads(wayfinder({"action": "route"}))
        assert out["isError"] is True

    def test_route(self, monkeypatch):
        monkeypatch.setenv("WAYFINDER_API_KEY", "k")
        _stub(monkeypatch, "clawmes.tools.wayfinder", "http_post", {"path": []})
        from clawmes.tools.wayfinder import wayfinder

        out = json.loads(
            wayfinder(
                {
                    "action": "route",
                    "from_chain": 1,
                    "from_token": "ETH",
                    "to_chain": 8453,
                    "to_token": "USDC",
                    "amount": "0.1",
                }
            )
        )
        assert "isError" not in out

    def test_compare(self, monkeypatch):
        monkeypatch.setenv("WAYFINDER_API_KEY", "k")
        _stub(monkeypatch, "clawmes.tools.wayfinder", "http_post", {"routes": []})
        from clawmes.tools.wayfinder import wayfinder

        out = json.loads(wayfinder({"action": "compare"}))
        assert "isError" not in out

    def test_optimize(self, monkeypatch):
        monkeypatch.setenv("WAYFINDER_API_KEY", "k")
        _stub(monkeypatch, "clawmes.tools.wayfinder", "http_post", {"plan": {}})
        from clawmes.tools.wayfinder import wayfinder

        out = json.loads(
            wayfinder(
                {
                    "action": "optimize",
                    "constraints": {"max_steps": 3},
                }
            )
        )
        assert "isError" not in out

    def test_optimize_non_dict_constraints(self, monkeypatch):
        monkeypatch.setenv("WAYFINDER_API_KEY", "k")
        _stub(monkeypatch, "clawmes.tools.wayfinder", "http_post", {"ok": True})
        from clawmes.tools.wayfinder import wayfinder

        out = json.loads(wayfinder({"action": "optimize", "constraints": "garbage"}))
        assert "isError" not in out

    def test_api_error(self, monkeypatch):
        monkeypatch.setenv("WAYFINDER_API_KEY", "k")
        _stub(
            monkeypatch,
            "clawmes.tools.wayfinder",
            "http_post",
            RuntimeError("network"),
        )
        from clawmes.tools.wayfinder import wayfinder

        out = json.loads(wayfinder({"action": "route"}))
        assert out["isError"] is True


# --- registers ---


class TestRegister:
    @pytest.mark.parametrize(
        "module_path,name",
        [
            ("clawmes.tools.molten", "molten"),
            ("clawmes.tools.clawnx", "clawnx"),
            ("clawmes.tools.hummingbot", "hummingbot"),
            ("clawmes.tools.wayfinder", "wayfinder"),
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
