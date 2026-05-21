"""Tests for clawmes.tools.clawnch_fees."""

from __future__ import annotations

import json

import pytest

from clawmes.services import clawnch as cl_mod
from clawmes.services.clawnch import ClawnchError
from clawmes.tools.clawnch_fees import clawnch_fees, register


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(cl_mod, "_instance", None)


class _FakeSvc:
    def __init__(self):
        self.my_launches_return: dict = {"launches": []}
        self.launch_return: dict = {"name": "X"}
        self.raise_on: Exception | None = None

    def get_my_launches(self):
        if self.raise_on:
            raise self.raise_on
        return self.my_launches_return

    def get_launch(self, token):
        if self.raise_on:
            raise self.raise_on
        return self.launch_return


@pytest.fixture
def fake_svc(monkeypatch):
    s = _FakeSvc()
    monkeypatch.setattr(
        "clawmes.services.clawnch.get_clawnch_service",
        lambda: s,
    )
    return s


class TestMyLaunches:
    def test_empty(self, fake_svc):
        out = json.loads(clawnch_fees({"action": "my_launches"}))
        assert "No launches" in out["content"][0]["text"]

    def test_with_launches(self, fake_svc):
        fake_svc.my_launches_return = {"launches": [{"id": 1}, {"id": 2}]}
        out = json.loads(clawnch_fees({"action": "my_launches"}))
        assert "2 launch" in out["content"][0]["text"]

    def test_with_tokens_key(self, fake_svc):
        # Some endpoints return `tokens` instead of `launches`
        fake_svc.my_launches_return = {"tokens": [{"id": 1}]}
        out = json.loads(clawnch_fees({"action": "my_launches"}))
        assert "1 launch" in out["content"][0]["text"]

    def test_non_list_value_default_summary(self, fake_svc):
        # Defensive: if upstream returns malformed data
        fake_svc.my_launches_return = {"launches": "not a list"}
        out = json.loads(clawnch_fees({"action": "my_launches"}))
        assert "retrieved" in out["content"][0]["text"]

    def test_clawnch_error(self, fake_svc):
        fake_svc.raise_on = ClawnchError("no_credentials", "no key")
        out = json.loads(clawnch_fees({"action": "my_launches"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "no_credentials"

    def test_unexpected_error(self, fake_svc):
        fake_svc.raise_on = RuntimeError("network")
        out = json.loads(clawnch_fees({"action": "my_launches"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "api_error"


class TestLaunchInfo:
    def test_requires_token(self, fake_svc):
        out = json.loads(clawnch_fees({"action": "launch_info"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "param_error"

    def test_basic(self, fake_svc):
        out = json.loads(clawnch_fees({"action": "launch_info", "token": "0xabc"}))
        assert out["details"] == {"name": "X"}

    def test_clawnch_error(self, fake_svc):
        fake_svc.raise_on = ClawnchError("not_found", "no token")
        out = json.loads(clawnch_fees({"action": "launch_info", "token": "0xabc"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "not_found"


class TestRegister:
    def test_registers_one_tool(self):
        captured = []

        class FakeCtx:
            def register_tool(self, **kw):
                captured.append(kw["name"])

        register(FakeCtx())
        assert captured == ["clawnch_fees"]
