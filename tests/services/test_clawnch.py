"""Tests for clawmes.services.clawnch — Clawnch launchpad HTTP client."""

from __future__ import annotations

import pytest

from clawmes.services import clawnch as cl_mod
from clawmes.services.clawnch import (
    ClawnchError,
    ClawnchService,
    get_clawnch_service,
)

ADDR = "0x" + "a" * 40


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(cl_mod, "_instance", None)
    monkeypatch.delenv("CLAWNCH_API_KEY", raising=False)
    monkeypatch.delenv("CLAWNCH_BASE_URL", raising=False)
    monkeypatch.delenv("CLAWNCH_BYPASS_RECIPIENT", raising=False)
    monkeypatch.delenv("CLAWNCH_BYPASS_FEE_ETH", raising=False)


@pytest.fixture
def svc():
    return ClawnchService()


@pytest.fixture
def svc_with_key(monkeypatch):
    monkeypatch.setenv("CLAWNCH_API_KEY", "test-key")
    s = ClawnchService()
    s.start()
    return s


# ──────────────────────────────────────────────────────────────────────
#  Lifecycle
# ──────────────────────────────────────────────────────────────────────


class TestLifecycle:
    def test_start_unauthenticated_logs_warning(self, svc):
        svc.start()  # warning logged; should not raise
        assert svc.health()["status"] == "unauthenticated"

    def test_start_authenticated(self, monkeypatch, svc):
        monkeypatch.setenv("CLAWNCH_API_KEY", "test-key")
        svc.start()
        assert svc.health()["status"] == "authenticated"

    def test_base_url_override(self, monkeypatch, svc):
        monkeypatch.setenv("CLAWNCH_BASE_URL", "https://staging.clawn.ch/")
        svc.start()
        # Trailing slash stripped
        assert svc.health()["base_url"] == "https://staging.clawn.ch"

    def test_stop_clears_key(self, svc_with_key):
        svc_with_key.stop()
        assert svc_with_key.health()["status"] == "unauthenticated"


# ──────────────────────────────────────────────────────────────────────
#  Agent registration
# ──────────────────────────────────────────────────────────────────────


class TestRegisterAgent:
    def test_requires_name(self, svc):
        with pytest.raises(ClawnchError) as exc_info:
            svc.register_agent(name="", wallet=ADDR, description="x")
        assert exc_info.value.code == "bad_request"

    def test_requires_wallet(self, svc):
        with pytest.raises(ClawnchError) as exc_info:
            svc.register_agent(name="x", wallet="", description="x")
        assert exc_info.value.code == "bad_request"

    def test_requires_description(self, svc):
        with pytest.raises(ClawnchError) as exc_info:
            svc.register_agent(name="x", wallet=ADDR, description="")
        assert exc_info.value.code == "bad_request"

    def test_register_calls_api(self, svc, monkeypatch):
        captured: list[tuple[str, dict]] = []

        def _post(url, json, headers, timeout):  # noqa: A002
            captured.append((url, json))
            return {"registrationId": "rid", "challenge": "c", "message": "m"}

        monkeypatch.setattr("clawmes.services.clawnch.http_post", _post)
        svc.start()
        result = svc.register_agent(name="agent", wallet=ADDR, description="desc")
        assert result["registrationId"] == "rid"
        assert captured[0][0].endswith("/api/agents/register")
        # Unauthenticated endpoint — no auth header sent
        body = captured[0][1]
        assert body == {"name": "agent", "wallet": ADDR, "description": "desc"}


class TestVerifyAgent:
    def test_requires_registration_id(self, svc):
        with pytest.raises(ClawnchError) as exc_info:
            svc.verify_agent(registration_id="", signature="0xsig")
        assert exc_info.value.code == "bad_request"

    def test_requires_signature(self, svc):
        with pytest.raises(ClawnchError) as exc_info:
            svc.verify_agent(registration_id="rid", signature="")
        assert exc_info.value.code == "bad_request"

    def test_verify_calls_api(self, svc, monkeypatch):
        def _post(url, json, headers, timeout):  # noqa: A002
            return {"apiKey": "key-x", "agentId": "agent-x", "wallet": ADDR}

        monkeypatch.setattr("clawmes.services.clawnch.http_post", _post)
        svc.start()
        result = svc.verify_agent(registration_id="rid", signature="0xsig")
        assert result["apiKey"] == "key-x"


# ──────────────────────────────────────────────────────────────────────
#  start_deploy
# ──────────────────────────────────────────────────────────────────────


class TestStartDeploy:
    def test_requires_api_key(self, svc):
        svc.start()  # no key
        with pytest.raises(ClawnchError) as exc_info:
            svc.start_deploy(token_params={"name": "x", "symbol": "X"})
        assert exc_info.value.code == "no_credentials"

    def test_requires_name(self, svc_with_key):
        with pytest.raises(ClawnchError) as exc_info:
            svc_with_key.start_deploy(token_params={"symbol": "X"})
        assert exc_info.value.code == "bad_request"

    def test_requires_symbol(self, svc_with_key):
        with pytest.raises(ClawnchError) as exc_info:
            svc_with_key.start_deploy(token_params={"name": "x"})
        assert exc_info.value.code == "bad_request"

    def test_stamps_source_clawmes(self, svc_with_key, monkeypatch):
        captured: list[dict] = []

        def _post(url, json, headers, timeout):  # noqa: A002
            captured.append(json)
            return {
                "challengeId": "cid",
                "message": "m",
                "nonce": "n",
                "contractAddress": "0x42",
                "storageSlot": "0x00",
                "deadline": "2030-01-01",
            }

        monkeypatch.setattr("clawmes.services.clawnch.http_post", _post)
        svc_with_key.start_deploy(token_params={"name": "x", "symbol": "X"})
        body = captured[0]
        assert body["tokenParams"]["source"] == "clawmes"

    def test_does_not_overwrite_user_source(self, svc_with_key, monkeypatch):
        captured: list[dict] = []

        def _post(url, json, headers, timeout):  # noqa: A002
            captured.append(json)
            return {
                "challengeId": "cid",
                "message": "m",
                "nonce": "n",
                "contractAddress": "0x42",
                "storageSlot": "0x00",
                "deadline": "2030",
            }

        monkeypatch.setattr("clawmes.services.clawnch.http_post", _post)
        svc_with_key.start_deploy(token_params={"name": "x", "symbol": "X", "source": "custom-tag"})
        assert captured[0]["tokenParams"]["source"] == "custom-tag"

    def test_includes_bypass_tx(self, svc_with_key, monkeypatch):
        captured: list[dict] = []

        def _post(url, json, headers, timeout):  # noqa: A002
            captured.append(json)
            return {
                "challengeId": "cid",
                "message": "m",
                "nonce": "n",
                "contractAddress": "0x42",
                "storageSlot": "0x00",
                "deadline": "2030",
            }

        monkeypatch.setattr("clawmes.services.clawnch.http_post", _post)
        svc_with_key.start_deploy(
            token_params={"name": "x", "symbol": "X"},
            bypass_tx_hash="0xdeadbeef",
        )
        assert captured[0]["bypassTxHash"] == "0xdeadbeef"


# ──────────────────────────────────────────────────────────────────────
#  solve_challenge
# ──────────────────────────────────────────────────────────────────────


def _good_challenge() -> dict:
    return {
        "challengeId": "cid",
        "message": "Clawnch deploy challenge: deadbeef",
        "nonce": "abc123",
        "contractAddress": "0x4200000000000000000000000000000000000006",
        "storageSlot": "0x0000000000000000000000000000000000000000000000000000000000000003",
        "deadline": "2030",
    }


class _FakeWalletMode:
    name = "fake"

    def __init__(self, signature="0x" + "ab" * 65):
        self._signature = signature
        self.signed_messages: list[str] = []

    def sign_personal_message(self, message):
        self.signed_messages.append(message)
        return self._signature


class _FakeWalletSvc:
    def __init__(self, mode):
        self.active_mode = mode


class _FakeRpc:
    def __init__(self, storage_value):
        self._sv = storage_value
        self.calls: list[tuple] = []

    def _call(self, chain_id, method, params):
        self.calls.append((chain_id, method, params))
        return self._sv


class TestSolveChallenge:
    def test_missing_fields_raise(self, svc):
        with pytest.raises(ClawnchError) as exc_info:
            svc.solve_challenge({})
        assert exc_info.value.code == "bad_request"

    def test_signs_reads_proof(self, svc, monkeypatch):
        wallet = _FakeWalletMode()
        monkeypatch.setattr(
            "clawmes.services.wallet.get_wallet_service",
            lambda: _FakeWalletSvc(wallet),
        )
        monkeypatch.setattr(
            "clawmes.services.rpc.get_rpc_service",
            lambda: _FakeRpc("0xff"),
        )
        result = svc.solve_challenge(_good_challenge())
        assert result["signature"].startswith("0x")
        # storage value padded to 32 bytes
        assert result["storageValue"].startswith("0x")
        assert len(result["storageValue"]) == 66
        # proof is 32-byte keccak
        assert result["proof"].startswith("0x")
        assert len(result["proof"]) == 66
        # wallet was asked to sign the challenge message
        assert wallet.signed_messages == [_good_challenge()["message"]]

    def test_no_wallet_raises(self, svc, monkeypatch):
        monkeypatch.setattr(
            "clawmes.services.wallet.get_wallet_service",
            lambda: _FakeWalletSvc(None),
        )
        with pytest.raises(ClawnchError) as exc_info:
            svc.solve_challenge(_good_challenge())
        assert exc_info.value.code == "no_credentials"

    def test_wallet_sign_raises_translated(self, svc, monkeypatch):
        class _BadMode:
            def sign_personal_message(self, message):
                raise RuntimeError("hardware wallet disconnected")

        monkeypatch.setattr(
            "clawmes.services.wallet.get_wallet_service",
            lambda: _FakeWalletSvc(_BadMode()),
        )
        with pytest.raises(ClawnchError) as exc_info:
            svc.solve_challenge(_good_challenge())
        assert exc_info.value.code == "api_error"

    def test_rpc_raises_translated(self, svc, monkeypatch):
        monkeypatch.setattr(
            "clawmes.services.wallet.get_wallet_service",
            lambda: _FakeWalletSvc(_FakeWalletMode()),
        )

        class _BoomRpc:
            def _call(self, *a, **kw):
                raise RuntimeError("RPC down")

        monkeypatch.setattr(
            "clawmes.services.rpc.get_rpc_service",
            lambda: _BoomRpc(),
        )
        with pytest.raises(ClawnchError) as exc_info:
            svc.solve_challenge(_good_challenge())
        assert exc_info.value.code == "api_error"

    def test_rpc_non_string_translated(self, svc, monkeypatch):
        monkeypatch.setattr(
            "clawmes.services.wallet.get_wallet_service",
            lambda: _FakeWalletSvc(_FakeWalletMode()),
        )

        class _WeirdRpc:
            def _call(self, *a, **kw):
                return 42  # not a string

        monkeypatch.setattr(
            "clawmes.services.rpc.get_rpc_service",
            lambda: _WeirdRpc(),
        )
        with pytest.raises(ClawnchError) as exc_info:
            svc.solve_challenge(_good_challenge())
        assert exc_info.value.code == "api_error"

    def test_storage_no_0x_prefix(self, svc, monkeypatch):
        monkeypatch.setattr(
            "clawmes.services.wallet.get_wallet_service",
            lambda: _FakeWalletSvc(_FakeWalletMode()),
        )
        # Some RPCs return without 0x prefix.
        monkeypatch.setattr(
            "clawmes.services.rpc.get_rpc_service",
            lambda: _FakeRpc("ff"),  # no 0x
        )
        result = svc.solve_challenge(_good_challenge())
        assert result["storageValue"].startswith("0x")


# ──────────────────────────────────────────────────────────────────────
#  confirm_deploy
# ──────────────────────────────────────────────────────────────────────


class TestConfirmDeploy:
    def test_requires_api_key(self, svc):
        svc.start()
        with pytest.raises(ClawnchError) as exc_info:
            svc.confirm_deploy(
                challenge_id="c",
                solution={"signature": "x", "storageValue": "x", "proof": "x"},
                token_params={"name": "x", "symbol": "X"},
            )
        assert exc_info.value.code == "no_credentials"

    def test_requires_challenge_id(self, svc_with_key):
        with pytest.raises(ClawnchError) as exc_info:
            svc_with_key.confirm_deploy(
                challenge_id="",
                solution={"signature": "x"},
                token_params={"name": "x", "symbol": "X"},
            )
        assert exc_info.value.code == "bad_request"

    def test_requires_solution(self, svc_with_key):
        with pytest.raises(ClawnchError) as exc_info:
            svc_with_key.confirm_deploy(
                challenge_id="c",
                solution={},
                token_params={"name": "x", "symbol": "X"},
            )
        assert exc_info.value.code == "bad_request"

    def test_calls_confirm_endpoint(self, svc_with_key, monkeypatch):
        captured: list[tuple] = []

        def _post(url, json, headers, timeout):  # noqa: A002
            captured.append((url, json, headers))
            return {"success": True, "txHash": "0xtx", "tokenAddress": "0xtok"}

        monkeypatch.setattr("clawmes.services.clawnch.http_post", _post)
        result = svc_with_key.confirm_deploy(
            challenge_id="c",
            solution={"signature": "s", "storageValue": "v", "proof": "p"},
            token_params={"name": "x", "symbol": "X"},
        )
        assert result["txHash"] == "0xtx"
        url, body, headers = captured[0]
        assert url.endswith("/api/deploy/confirm")
        assert headers["Authorization"] == "Bearer test-key"
        assert body["tokenParams"]["source"] == "clawmes"


# ──────────────────────────────────────────────────────────────────────
#  deploy (convenience)
# ──────────────────────────────────────────────────────────────────────


class TestDeployConvenience:
    def test_end_to_end_happy(self, svc_with_key, monkeypatch):
        # /api/deploy returns a challenge, then /api/deploy/confirm returns success
        responses = [
            {
                "challengeId": "cid",
                "message": "msg",
                "nonce": "nonce",
                "contractAddress": "0x4200000000000000000000000000000000000006",
                "storageSlot": "0x00",
                "deadline": "2030",
            },
            {"success": True, "txHash": "0xtx", "tokenAddress": "0xtok"},
        ]

        def _post(url, json, headers, timeout):  # noqa: A002
            return responses.pop(0)

        monkeypatch.setattr("clawmes.services.clawnch.http_post", _post)
        monkeypatch.setattr(
            "clawmes.services.wallet.get_wallet_service",
            lambda: _FakeWalletSvc(_FakeWalletMode()),
        )
        monkeypatch.setattr(
            "clawmes.services.rpc.get_rpc_service",
            lambda: _FakeRpc("0xff"),
        )
        result = svc_with_key.deploy(token_params={"name": "x", "symbol": "X"})
        assert result["success"] is True
        assert result["tokenAddress"] == "0xtok"


# ──────────────────────────────────────────────────────────────────────
#  reads
# ──────────────────────────────────────────────────────────────────────


class TestReads:
    def test_get_my_launches_requires_key(self, svc):
        svc.start()
        with pytest.raises(ClawnchError) as exc_info:
            svc.get_my_launches()
        assert exc_info.value.code == "no_credentials"

    def test_get_my_launches_calls_api(self, svc_with_key, monkeypatch):
        def _get(url, headers, timeout):
            return {"launches": []}

        monkeypatch.setattr("clawmes.services.clawnch.http_get", _get)
        result = svc_with_key.get_my_launches()
        assert result == {"launches": []}

    def test_get_launch_requires_address(self, svc):
        with pytest.raises(ClawnchError) as exc_info:
            svc.get_launch("")
        assert exc_info.value.code == "bad_request"

    def test_get_launch_calls_api(self, svc, monkeypatch):
        captured: list[str] = []

        def _get(url, headers, timeout):
            captured.append(url)
            return {"token": "0xabc"}

        monkeypatch.setattr("clawmes.services.clawnch.http_get", _get)
        svc.start()
        result = svc.get_launch("0xabc")
        assert result == {"token": "0xabc"}
        assert "address=0xabc" in captured[0]

    def test_get_launch_unauthed_works_without_key(self, svc, monkeypatch):
        captured: list[dict] = []

        def _get(url, headers, timeout):
            captured.append(headers)
            return {"token": "0xabc"}

        monkeypatch.setattr("clawmes.services.clawnch.http_get", _get)
        svc.start()  # no key
        svc.get_launch("0xabc")
        # No Authorization header sent
        assert "Authorization" not in captured[0]

    def test_get_bypass_recipient_default(self, svc):
        info = svc.get_bypass_recipient()
        assert info["recipient"].startswith("0x")
        assert info["fee_eth"] == "0.001"

    def test_get_bypass_recipient_env_override(self, monkeypatch, svc):
        monkeypatch.setenv("CLAWNCH_BYPASS_RECIPIENT", "0xabc")
        monkeypatch.setenv("CLAWNCH_BYPASS_FEE_ETH", "0.01")
        info = svc.get_bypass_recipient()
        assert info["recipient"] == "0xabc"
        assert info["fee_eth"] == "0.01"


# ──────────────────────────────────────────────────────────────────────
#  Error reclassification
# ──────────────────────────────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        return self._body


class _HTTPErr(Exception):
    def __init__(self, response):
        super().__init__("http error")
        self.response = response


class TestErrorReclassify:
    def _trigger(self, svc, exc):
        def _post(url, json, headers, timeout):  # noqa: A002
            raise exc

        return _post

    def test_429_to_rate_limited(self, svc_with_key, monkeypatch):
        exc = _HTTPErr(_FakeResponse(429, {"error": "slow down", "code": "RATE_LIMITED"}))
        monkeypatch.setattr("clawmes.services.clawnch.http_post", self._trigger(svc_with_key, exc))
        with pytest.raises(ClawnchError) as exc_info:
            svc_with_key.start_deploy(token_params={"name": "x", "symbol": "X"})
        assert exc_info.value.code == "rate_limited"

    def test_400_to_bad_request(self, svc_with_key, monkeypatch):
        exc = _HTTPErr(_FakeResponse(400, {"error": "malformed"}))
        monkeypatch.setattr("clawmes.services.clawnch.http_post", self._trigger(svc_with_key, exc))
        with pytest.raises(ClawnchError) as exc_info:
            svc_with_key.start_deploy(token_params={"name": "x", "symbol": "X"})
        assert exc_info.value.code == "bad_request"

    def test_401_to_no_credentials(self, svc_with_key, monkeypatch):
        exc = _HTTPErr(_FakeResponse(401, {"error": "bad key"}))
        monkeypatch.setattr("clawmes.services.clawnch.http_post", self._trigger(svc_with_key, exc))
        with pytest.raises(ClawnchError) as exc_info:
            svc_with_key.start_deploy(token_params={"name": "x", "symbol": "X"})
        assert exc_info.value.code == "no_credentials"

    def test_403_to_no_credentials(self, svc_with_key, monkeypatch):
        exc = _HTTPErr(_FakeResponse(403, {"error": "forbidden"}))
        monkeypatch.setattr("clawmes.services.clawnch.http_post", self._trigger(svc_with_key, exc))
        with pytest.raises(ClawnchError) as exc_info:
            svc_with_key.start_deploy(token_params={"name": "x", "symbol": "X"})
        assert exc_info.value.code == "no_credentials"

    def test_404_to_not_found(self, svc_with_key, monkeypatch):
        exc = _HTTPErr(_FakeResponse(404, {"error": "no challenge"}))
        monkeypatch.setattr("clawmes.services.clawnch.http_post", self._trigger(svc_with_key, exc))
        with pytest.raises(ClawnchError) as exc_info:
            svc_with_key.start_deploy(token_params={"name": "x", "symbol": "X"})
        assert exc_info.value.code == "not_found"

    def test_408_to_challenge_expired(self, svc_with_key, monkeypatch):
        exc = _HTTPErr(_FakeResponse(408, {"error": "expired"}))
        monkeypatch.setattr("clawmes.services.clawnch.http_post", self._trigger(svc_with_key, exc))
        with pytest.raises(ClawnchError) as exc_info:
            svc_with_key.start_deploy(token_params={"name": "x", "symbol": "X"})
        assert exc_info.value.code == "challenge_expired"

    def test_bypass_invalid_code_to_bad_request(self, svc_with_key, monkeypatch):
        exc = _HTTPErr(_FakeResponse(400, {"error": "bypass invalid", "code": "BYPASS_INVALID"}))
        monkeypatch.setattr("clawmes.services.clawnch.http_post", self._trigger(svc_with_key, exc))
        with pytest.raises(ClawnchError) as exc_info:
            svc_with_key.start_deploy(token_params={"name": "x", "symbol": "X"})
        assert exc_info.value.code == "bad_request"

    def test_insufficient_funds_code_to_bad_request(self, svc_with_key, monkeypatch):
        exc = _HTTPErr(_FakeResponse(402, {"error": "not enough", "code": "INSUFFICIENT_FUNDS"}))
        monkeypatch.setattr("clawmes.services.clawnch.http_post", self._trigger(svc_with_key, exc))
        with pytest.raises(ClawnchError) as exc_info:
            svc_with_key.start_deploy(token_params={"name": "x", "symbol": "X"})
        assert exc_info.value.code == "bad_request"

    def test_unclassifiable_propagates(self, svc_with_key, monkeypatch):
        """When we can't translate, the original exception is re-raised."""

        class _Bare(Exception):
            pass

        bare = _Bare("transport failed")

        def _post(url, json, headers, timeout):  # noqa: A002
            raise bare

        monkeypatch.setattr("clawmes.services.clawnch.http_post", _post)
        with pytest.raises(_Bare):
            svc_with_key.start_deploy(token_params={"name": "x", "symbol": "X"})

    def test_response_without_json_body_falls_through(self, svc_with_key, monkeypatch):
        class _NoBody:
            status_code = 500

            def json(self):
                raise ValueError("not json")

        exc = _HTTPErr(_NoBody())

        def _post(url, json, headers, timeout):  # noqa: A002
            raise exc

        monkeypatch.setattr("clawmes.services.clawnch.http_post", _post)
        with pytest.raises(_HTTPErr):
            svc_with_key.start_deploy(token_params={"name": "x", "symbol": "X"})

    def test_get_reclassifies_too(self, svc_with_key, monkeypatch):
        exc = _HTTPErr(_FakeResponse(404, {"error": "no agent"}))

        def _get(url, headers, timeout):
            raise exc

        monkeypatch.setattr("clawmes.services.clawnch.http_get", _get)
        with pytest.raises(ClawnchError) as exc_info:
            svc_with_key.get_my_launches()
        assert exc_info.value.code == "not_found"

    def test_get_unclassifiable_propagates(self, svc_with_key, monkeypatch):
        class _Bare(Exception):
            pass

        def _get(url, headers, timeout):
            raise _Bare("transport down")

        monkeypatch.setattr("clawmes.services.clawnch.http_get", _get)
        with pytest.raises(_Bare):
            svc_with_key.get_my_launches()


# ──────────────────────────────────────────────────────────────────────
#  Singleton
# ──────────────────────────────────────────────────────────────────────


class TestSingleton:
    def test_returns_same_instance(self):
        a = get_clawnch_service()
        b = get_clawnch_service()
        assert a is b
