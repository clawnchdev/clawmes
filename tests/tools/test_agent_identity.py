"""Tests for the ``agent_identity`` tool."""

from __future__ import annotations

import json

import pytest

from clawmes.services import identity as id_mod
from clawmes.tools.agent_identity import agent_identity, register


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    from clawmes.policy import storage as policy_storage

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(id_mod, "_instance", None)
    policy_storage.save_policies([])


def _call(action, **kw):
    payload = {"action": action, **kw}
    return json.loads(agent_identity(payload))


# --- show --------------------------------------------------------------


class TestShow:
    def test_empty(self):
        out = _call("show")
        assert out["details"]["status"] == "no_identity"

    def test_after_create(self):
        _call("create")
        out = _call("show")
        assert out["details"]["status"] == "active"
        assert out["details"]["did"].startswith("did:key:z")


# --- create ------------------------------------------------------------


class TestCreate:
    def test_basic(self):
        out = _call("create")
        assert out["details"]["status"] == "created"
        assert out["details"]["did"].startswith("did:key:z")

    def test_refuses_overwrite_by_default(self):
        _call("create")
        out = _call("create")
        assert out["details"]["error_code"] == "conflict"

    def test_overwrite_force(self):
        first = _call("create")
        second = _call("create", overwrite=True)
        assert second["details"]["status"] == "created"
        assert first["details"]["did"] != second["details"]["did"]


# --- sign --------------------------------------------------------------


class TestSign:
    def test_basic_message(self):
        _call("create")
        out = _call("sign", message="hello")
        assert "signature_hex" in out["details"]
        assert len(out["details"]["signature_hex"]) == 128  # 64 bytes hex

    def test_message_hex(self):
        _call("create")
        out = _call("sign", message_hex="deadbeef")
        assert "signature_hex" in out["details"]

    def test_message_hex_wins_over_message(self):
        _call("create")
        # The signatures should differ — message_hex takes precedence.
        sig_hex = _call("sign", message_hex="deadbeef")["details"]["signature_hex"]
        sig_text = _call("sign", message="deadbeef")["details"]["signature_hex"]
        assert sig_hex != sig_text

    def test_no_message_returns_error(self):
        _call("create")
        out = _call("sign")
        assert out["details"]["error_code"] == "param_error"

    def test_bad_message_hex(self):
        _call("create")
        out = _call("sign", message_hex="not-hex-at-all")
        assert out["details"]["error_code"] == "param_error"

    def test_no_identity_returns_error(self):
        out = _call("sign", message="hello")
        assert out["details"]["error_code"] == "no_identity"


# --- verify ------------------------------------------------------------


class TestVerify:
    def test_valid_signature(self):
        create_out = _call("create")
        pubkey = create_out["details"]["public_key_hex"]
        sign_out = _call("sign", message="hello")
        sig = sign_out["details"]["signature_hex"]

        verify_out = _call(
            "verify",
            message="hello",
            public_key_hex=pubkey,
            signature_hex=sig,
        )
        assert verify_out["details"]["valid"] is True

    def test_invalid_signature(self):
        create_out = _call("create")
        pubkey = create_out["details"]["public_key_hex"]
        sign_out = _call("sign", message="hello")
        sig = sign_out["details"]["signature_hex"]

        verify_out = _call(
            "verify",
            message="tampered",
            public_key_hex=pubkey,
            signature_hex=sig,
        )
        assert verify_out["details"]["valid"] is False

    def test_missing_pubkey(self):
        _call("create")
        out = _call("verify", message="hi", signature_hex="00" * 64)
        assert out["details"]["error_code"] == "param_error"

    def test_missing_signature(self):
        _call("create")
        out = _call("verify", message="hi", public_key_hex="00" * 32)
        assert out["details"]["error_code"] == "param_error"

    def test_bad_signature_hex(self):
        _call("create")
        out = _call(
            "verify",
            message="hi",
            public_key_hex="00" * 32,
            signature_hex="not-hex",
        )
        assert out["details"]["error_code"] == "param_error"


# --- did_encode --------------------------------------------------------


class TestDidEncode:
    def test_basic(self):
        out = _call("did_encode", public_key_hex="00" * 32)
        assert out["details"]["did"].startswith("did:key:z")

    def test_missing_pubkey(self):
        out = _call("did_encode")
        assert out["details"]["error_code"] == "param_error"

    def test_bad_hex(self):
        out = _call("did_encode", public_key_hex="not-hex")
        assert out["details"]["error_code"] == "param_error"

    def test_wrong_length(self):
        out = _call("did_encode", public_key_hex="00" * 16)
        assert out["details"]["error_code"] == "param_error"


# --- guards / dispatch --------------------------------------------------


class TestDispatch:
    def test_missing_action(self):
        out = json.loads(agent_identity({}))
        assert out["details"]["error_code"] == "param_error"

    def test_unknown_action(self):
        out = json.loads(agent_identity({"action": "explode"}))
        assert out["details"]["error_code"] == "param_error"


# --- registration ------------------------------------------------------


class TestRegister:
    def test_register_pushes_tool(self):
        captured = []

        class FakeCtx:
            def register_tool(self, **kw):
                captured.append(kw)

        register(FakeCtx())
        assert len(captured) == 1
        assert captured[0]["name"] == "agent_identity"
        assert captured[0]["toolset"] == "clawmes-identity"
