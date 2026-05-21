"""Tests for the ``a2a_call`` tool (JSON-RPC 2.0 agent-to-agent client)."""

from __future__ import annotations

import json

import pytest

from clawmes.tools import a2a_call as a2a_mod
from clawmes.tools.a2a_call import a2a_call, register


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    from clawmes.policy import storage as policy_storage

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    policy_storage.save_policies([])


@pytest.fixture
def fake_http(monkeypatch):
    class FakeHttp:
        get_responses: list = []
        post_responses: list = []
        post_calls: list = []
        get_calls: list = []

        def get(self, url, **kw):
            self.get_calls.append({"url": url, **kw})
            if not self.get_responses:
                raise AssertionError("no fake get response queued")
            r = self.get_responses.pop(0)
            if isinstance(r, Exception):
                raise r
            return r

        def post(self, url, *, json=None, **kw):
            self.post_calls.append({"url": url, "json": json, **kw})
            if not self.post_responses:
                raise AssertionError("no fake post response queued")
            r = self.post_responses.pop(0)
            if isinstance(r, Exception):
                raise r
            return r

    fake = FakeHttp()
    monkeypatch.setattr(a2a_mod, "http_get", fake.get)
    monkeypatch.setattr(a2a_mod, "http_post", fake.post)
    return fake


# --- discover -----------------------------------------------------------


class TestDiscover:
    def test_basic(self, fake_http):
        fake_http.get_responses.append(
            {
                "name": "BV-7X Oracle",
                "description": "Autonomous BTC prediction agent",
                "skills": ["get_market_context", "get_signal_summary"],
            }
        )
        out = json.loads(a2a_call({"action": "discover", "agent_url": "https://bv7x.ai"}))
        assert "BV-7X Oracle" in out["content"][0]["text"]
        assert "2" in out["content"][0]["text"]  # 2 skills
        assert "get_market_context" in out["content"][0]["text"]
        assert fake_http.get_calls[0]["url"] == ("https://bv7x.ai/.well-known/agent-card.json")

    def test_skills_as_dicts(self, fake_http):
        fake_http.get_responses.append(
            {
                "name": "Peer",
                "skills": [{"id": "skill_a"}, {"name": "skill_b"}, {"x": "y"}],
            }
        )
        out = json.loads(a2a_call({"action": "discover", "agent_url": "https://bv7x.ai"}))
        assert "skill_a" in out["content"][0]["text"]
        assert "skill_b" in out["content"][0]["text"]
        assert "(unnamed)" in out["content"][0]["text"]

    def test_capabilities_field_fallback(self, fake_http):
        # Some agents use 'capabilities' instead of 'skills'.
        fake_http.get_responses.append({"name": "Peer", "capabilities": ["cap_a", "cap_b"]})
        out = json.loads(a2a_call({"action": "discover", "agent_url": "https://bv7x.ai"}))
        assert "cap_a" in out["content"][0]["text"]

    def test_http_failure(self, fake_http):
        fake_http.get_responses.append(RuntimeError("DNS failure"))
        out = json.loads(a2a_call({"action": "discover", "agent_url": "https://bv7x.ai"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "api_error"
        assert "DNS failure" in out["content"][0]["text"]

    def test_non_dict_response(self, fake_http):
        fake_http.get_responses.append("not a dict")
        out = json.loads(a2a_call({"action": "discover", "agent_url": "https://bv7x.ai"}))
        assert out["details"]["error_code"] == "api_error"


# --- send_task ----------------------------------------------------------


class TestSendTask:
    def test_basic(self, fake_http):
        fake_http.post_responses.append(
            {
                "jsonrpc": "2.0",
                "id": "1",
                "result": {"regime": "BULL", "confidence": 0.7},
            }
        )
        out = json.loads(
            a2a_call(
                {
                    "action": "send_task",
                    "agent_url": "https://bv7x.ai",
                    "skill": "get_market_context",
                }
            )
        )
        assert out["details"]["result"]["regime"] == "BULL"
        body = fake_http.post_calls[0]["json"]
        assert body["jsonrpc"] == "2.0"
        assert body["method"] == "tasks/send"
        assert body["params"]["skill"] == "get_market_context"

    def test_with_params(self, fake_http):
        fake_http.post_responses.append({"jsonrpc": "2.0", "id": "1", "result": {}})
        a2a_call(
            {
                "action": "send_task",
                "agent_url": "https://bv7x.ai",
                "skill": "x",
                "params": {"foo": "bar"},
            }
        )
        body = fake_http.post_calls[0]["json"]
        assert body["params"]["foo"] == "bar"

    def test_custom_task_id(self, fake_http):
        fake_http.post_responses.append({"jsonrpc": "2.0", "id": "abc", "result": {}})
        a2a_call(
            {
                "action": "send_task",
                "agent_url": "https://bv7x.ai",
                "skill": "x",
                "task_id": "abc",
            }
        )
        body = fake_http.post_calls[0]["json"]
        assert body["id"] == "abc"

    def test_custom_task_path(self, fake_http):
        fake_http.post_responses.append({"jsonrpc": "2.0", "id": "1", "result": {}})
        a2a_call(
            {
                "action": "send_task",
                "agent_url": "https://bv7x.ai",
                "skill": "x",
                "task_path": "/a2a/tasks/send",
            }
        )
        assert fake_http.post_calls[0]["url"] == "https://bv7x.ai/a2a/tasks/send"

    def test_task_path_without_leading_slash(self, fake_http):
        fake_http.post_responses.append({"jsonrpc": "2.0", "id": "1", "result": {}})
        a2a_call(
            {
                "action": "send_task",
                "agent_url": "https://bv7x.ai",
                "skill": "x",
                "task_path": "a2a/tasks/send",
            }
        )
        assert fake_http.post_calls[0]["url"] == "https://bv7x.ai/a2a/tasks/send"

    def test_missing_skill(self):
        out = json.loads(a2a_call({"action": "send_task", "agent_url": "https://bv7x.ai"}))
        assert out["details"]["error_code"] == "param_error"

    def test_bad_params_type(self):
        out = json.loads(
            a2a_call(
                {
                    "action": "send_task",
                    "agent_url": "https://bv7x.ai",
                    "skill": "x",
                    "params": "not-a-dict",
                }
            )
        )
        assert out["details"]["error_code"] == "param_error"

    def test_http_post_failure(self, fake_http):
        fake_http.post_responses.append(RuntimeError("connection reset"))
        out = json.loads(
            a2a_call(
                {
                    "action": "send_task",
                    "agent_url": "https://bv7x.ai",
                    "skill": "x",
                }
            )
        )
        assert out["details"]["error_code"] == "api_error"

    def test_jsonrpc_error_envelope(self, fake_http):
        fake_http.post_responses.append(
            {
                "jsonrpc": "2.0",
                "id": "1",
                "error": {"code": -32601, "message": "Method not found"},
            }
        )
        out = json.loads(
            a2a_call(
                {
                    "action": "send_task",
                    "agent_url": "https://bv7x.ai",
                    "skill": "unknown",
                }
            )
        )
        assert out["isError"] is True
        assert "Method not found" in out["content"][0]["text"]

    def test_non_dict_post_response(self, fake_http):
        fake_http.post_responses.append("not a dict")
        out = json.loads(
            a2a_call(
                {
                    "action": "send_task",
                    "agent_url": "https://bv7x.ai",
                    "skill": "x",
                }
            )
        )
        assert out["details"]["error_code"] == "api_error"

    def test_result_keys_in_summary(self, fake_http):
        fake_http.post_responses.append(
            {
                "jsonrpc": "2.0",
                "id": "1",
                "result": {"regime": "BULL", "confidence": 0.7},
            }
        )
        out = json.loads(
            a2a_call(
                {
                    "action": "send_task",
                    "agent_url": "https://bv7x.ai",
                    "skill": "x",
                }
            )
        )
        # Summary should mention the result keys.
        assert "regime" in out["content"][0]["text"]


# --- guards / dispatch --------------------------------------------------


class TestGuards:
    def test_malformed_url(self):
        out = json.loads(a2a_call({"action": "discover", "agent_url": "not-a-url"}))
        assert out["details"]["error_code"] == "param_error"

    def test_missing_action(self):
        out = json.loads(a2a_call({"agent_url": "https://bv7x.ai"}))
        assert out["details"]["error_code"] == "param_error"

    def test_missing_agent_url(self):
        out = json.loads(a2a_call({"action": "discover"}))
        assert out["details"]["error_code"] == "param_error"


# --- registration ------------------------------------------------------


class TestRegister:
    def test_register(self):
        captured = []

        class FakeCtx:
            def register_tool(self, **kw):
                captured.append(kw)

        register(FakeCtx())
        assert len(captured) == 1
        assert captured[0]["name"] == "a2a_call"
