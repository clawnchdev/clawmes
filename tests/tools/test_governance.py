"""Tests for the ``governance`` tool + service."""

from __future__ import annotations

import json

import pytest

from clawmes.tools.governance import governance


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    from clawmes.policy import storage as policy_storage

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("TALLY_API_KEY", raising=False)
    policy_storage.save_policies([])


@pytest.fixture
def fake_post(monkeypatch):
    class FakeHttp:
        def __init__(self):
            self.calls: list[dict] = []
            self.responses: list = []

        def __call__(self, url, *, json=None, headers=None, timeout=30.0, **kw):
            self.calls.append({"url": url, "json": json, "headers": headers})
            if not self.responses:
                raise AssertionError("no fake response queued")
            response = self.responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response

    fake = FakeHttp()
    monkeypatch.setattr("clawmes.services.governance.http_post", fake)
    return fake


class TestProposalsSnapshot:
    def test_basic(self, fake_post):
        fake_post.responses.append(
            {"data": {"proposals": [{"id": "0x1", "title": "Increase fees", "state": "active"}]}}
        )
        out = json.loads(governance({"action": "proposals", "space": "aave.eth"}))
        assert "isError" not in out
        assert out["details"]["count"] == 1

    def test_default_state_active(self, fake_post):
        fake_post.responses.append({"data": {"proposals": []}})
        governance({"action": "proposals", "space": "aave.eth"})
        sent = fake_post.calls[0]["json"]
        assert sent["variables"]["state"] == "active"

    def test_explicit_state(self, fake_post):
        fake_post.responses.append({"data": {"proposals": []}})
        governance({"action": "proposals", "space": "aave.eth", "state": "closed"})
        sent = fake_post.calls[0]["json"]
        assert sent["variables"]["state"] == "closed"

    def test_api_error(self, fake_post):
        fake_post.responses.append(RuntimeError("network"))
        out = json.loads(governance({"action": "proposals", "space": "aave.eth"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "api_error"

    def test_rate_limited(self, fake_post):
        fake_post.responses.append(RuntimeError("HTTP 429"))
        out = json.loads(governance({"action": "proposals", "space": "aave.eth"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "rate_limited"

    def test_non_dict_response(self, fake_post):
        fake_post.responses.append("garbage")
        out = json.loads(governance({"action": "proposals", "space": "aave.eth"}))
        assert out["isError"] is True

    def test_graphql_errors(self, fake_post):
        fake_post.responses.append({"errors": [{"message": "bad query"}]})
        out = json.loads(governance({"action": "proposals", "space": "bad.eth"}))
        assert out["isError"] is True

    def test_graphql_not_found(self, fake_post):
        fake_post.responses.append({"errors": [{"message": "Space not found"}]})
        out = json.loads(governance({"action": "proposals", "space": "nonexistent.eth"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "not_found"


class TestProposalsTally:
    def test_no_api_key(self, fake_post):
        out = json.loads(governance({"action": "proposals", "backend": "tally"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "no_credentials"

    def test_with_api_key(self, monkeypatch, fake_post):
        monkeypatch.setenv("TALLY_API_KEY", "tally-test")
        fake_post.responses.append(
            {"data": {"organizations": {"nodes": [{"id": "1", "name": "Compound"}]}}}
        )
        out = json.loads(governance({"action": "proposals", "backend": "tally", "chain_id": 1}))
        assert "isError" not in out
        assert out["details"]["count"] == 1


class TestInfo:
    def test_basic(self, fake_post):
        fake_post.responses.append(
            {
                "data": {
                    "proposal": {
                        "id": "0x1",
                        "title": "Test",
                        "state": "active",
                        "space": {"name": "Aave"},
                    }
                }
            }
        )
        out = json.loads(governance({"action": "info", "proposal_id": "0x1"}))
        assert "isError" not in out

    def test_not_found(self, fake_post):
        fake_post.responses.append({"data": {"proposal": None}})
        out = json.loads(governance({"action": "info", "proposal_id": "0xdead"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "not_found"

    def test_tally_not_implemented(self):
        out = json.loads(governance({"action": "info", "backend": "tally", "proposal_id": "0x1"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "not_implemented"


class TestVote:
    def test_basic(self, fake_post):
        fake_post.responses.append({"id": "vote-id"})
        out = json.loads(
            governance(
                {
                    "action": "vote",
                    "payload": {
                        "address": "0x" + "a" * 40,
                        "msg": "{}",
                        "sig": "0x" + "b" * 130,
                    },
                }
            )
        )
        assert "isError" not in out

    def test_missing_payload(self, fake_post):
        out = json.loads(governance({"action": "vote"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "param_error"

    def test_payload_not_dict(self, fake_post):
        out = json.loads(governance({"action": "vote", "payload": "not a dict"}))
        assert out["isError"] is True

    def test_tally_vote_not_implemented(self):
        out = json.loads(governance({"action": "vote", "backend": "tally", "payload": {}}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "not_implemented"

    def test_vote_api_failure(self, fake_post):
        fake_post.responses.append(RuntimeError("network"))
        out = json.loads(governance({"action": "vote", "payload": {"x": 1}}))
        assert out["isError"] is True

    def test_vote_returns_non_dict_treated_as_submitted(self, fake_post):
        fake_post.responses.append("just-a-string-confirmation")
        out = json.loads(governance({"action": "vote", "payload": {"x": 1}}))
        # Service layer treats non-dict as submitted=True
        assert "isError" not in out


class TestInfoErrorPropagation:
    def test_rate_limited_propagates(self, fake_post):
        # info hits the snapshot_query path that can raise rate_limited
        fake_post.responses.append(RuntimeError("HTTP 429"))
        out = json.loads(governance({"action": "info", "proposal_id": "0x1"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "rate_limited"


class TestTallyServiceErrors:
    def test_rate_limited(self, monkeypatch, fake_post):
        monkeypatch.setenv("TALLY_API_KEY", "k")
        fake_post.responses.append(RuntimeError("HTTP 429"))
        out = json.loads(governance({"action": "proposals", "backend": "tally"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "rate_limited"

    def test_api_error(self, monkeypatch, fake_post):
        monkeypatch.setenv("TALLY_API_KEY", "k")
        fake_post.responses.append(RuntimeError("connection reset"))
        out = json.loads(governance({"action": "proposals", "backend": "tally"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "api_error"

    def test_non_dict_response(self, monkeypatch, fake_post):
        monkeypatch.setenv("TALLY_API_KEY", "k")
        fake_post.responses.append("garbage")
        out = json.loads(governance({"action": "proposals", "backend": "tally"}))
        assert out["isError"] is True

    def test_graphql_errors(self, monkeypatch, fake_post):
        monkeypatch.setenv("TALLY_API_KEY", "k")
        fake_post.responses.append({"errors": [{"message": "bad query"}]})
        out = json.loads(governance({"action": "proposals", "backend": "tally"}))
        assert out["isError"] is True


class TestDelegate:
    def test_not_implemented(self):
        out = json.loads(governance({"action": "delegate"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "not_implemented"


class TestRegister:
    def test_register_calls_ctx(self):
        from clawmes.tools import governance as gov_mod

        recorded = []

        class FakeCtx:
            def register_tool(self, **kw):
                recorded.append(kw)

        gov_mod.register(FakeCtx())
        assert len(recorded) == 1
        assert recorded[0]["name"] == "governance"
