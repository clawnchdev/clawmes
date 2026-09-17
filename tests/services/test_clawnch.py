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

    def test_base_url_defaults_to_www(self, svc):
        # Apex clawn.ch 307-redirects to www; the client doesn't follow
        # cross-host redirects, so the default targets the www host directly.
        svc.start()
        assert svc.health()["base_url"] == "https://www.clawn.ch"

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

    def test_includes_burn_tx(self, svc_with_key, monkeypatch):
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
            burn_tx_hash="0xburn",
        )
        assert captured[0]["burnTxHash"] == "0xburn"
        # No bypass key when bypass not requested.
        assert "bypassTxHash" not in captured[0]

    def test_burn_and_bypass_independent(self, svc_with_key, monkeypatch):
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
            bypass_tx_hash="0xbypass",
            burn_tx_hash="0xburn",
        )
        assert captured[0]["bypassTxHash"] == "0xbypass"
        assert captured[0]["burnTxHash"] == "0xburn"


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

    def test_deploy_forwards_burn_tx(self, svc_with_key, monkeypatch):
        # Deploy convenience must forward burn_tx_hash through start_deploy
        # into the body of the /api/deploy POST.
        captured: list[dict] = []
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
            captured.append(json)
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
        svc_with_key.deploy(
            token_params={"name": "x", "symbol": "X"},
            burn_tx_hash="0xburn",
        )
        # First call is /api/deploy with the start_deploy body.
        assert captured[0]["burnTxHash"] == "0xburn"


# ──────────────────────────────────────────────────────────────────────
#  reads
# ──────────────────────────────────────────────────────────────────────


class TestPrepareDeploy:
    """``prepare_deploy`` — non-custodial calldata flow."""

    def _ok_payload(self):
        return {
            "ok": True,
            "data": {
                "to": "0xE85A59c628F7d27878ACeB4bf3b35733630083a9",
                "data": "0xdf40224a" + "00" * 32,
                "value": "0x0",
                "chainId": 8453,
            },
            "meta": {
                "platformFeeBps": 2000,
                "userFeeBps": 8000,
                "vaultPercentage": 0,
                "source": "base-mcp",
            },
        }

    def test_requires_from(self, svc):
        with pytest.raises(ClawnchError) as exc_info:
            svc.prepare_deploy(from_address="", name="X", symbol="X")
        assert exc_info.value.code == "bad_request"

    def test_requires_name(self, svc):
        with pytest.raises(ClawnchError) as exc_info:
            svc.prepare_deploy(from_address="0x" + "1" * 40, name="", symbol="X")
        assert exc_info.value.code == "bad_request"

    def test_requires_symbol(self, svc):
        with pytest.raises(ClawnchError) as exc_info:
            svc.prepare_deploy(from_address="0x" + "1" * 40, name="X", symbol="")
        assert exc_info.value.code == "bad_request"

    def test_returns_envelope_on_ok(self, svc, monkeypatch):
        captured: list[dict] = []
        payload = self._ok_payload()

        def _get(url, params=None, headers=None, timeout=None):
            captured.append({"url": url, "params": params})
            return payload

        monkeypatch.setattr("clawmes.services.clawnch.http_get", _get)
        out = svc.prepare_deploy(
            from_address="0x" + "1" * 40,
            name="MyCoin",
            symbol="MYC",
            description="hello",
            image="https://example.com/x.png",
            twitter="https://x.com/clawnch",
            website="clawn.ch",
            telegram="https://t.me/clawnch",
            farcaster="https://warpcast.com/clawnch",
            discord="https://discord.gg/clawnch",
            burn_tx_hash="0x" + "a" * 64,
        )
        assert out == payload
        # All optional params propagated to the query string
        sent = captured[0]["params"]
        assert sent["from"] == "0x" + "1" * 40
        assert sent["name"] == "MyCoin"
        assert sent["symbol"] == "MYC"
        assert sent["description"] == "hello"
        assert sent["image"] == "https://example.com/x.png"
        assert sent["twitter"] == "https://x.com/clawnch"
        assert sent["website"] == "clawn.ch"
        assert sent["telegram"] == "https://t.me/clawnch"
        assert sent["farcaster"] == "https://warpcast.com/clawnch"
        assert sent["discord"] == "https://discord.gg/clawnch"
        assert sent["burnTxHash"] == "0x" + "a" * 64

    def test_minimal_params_omit_optionals(self, svc, monkeypatch):
        captured: list[dict] = []

        def _get(url, params=None, headers=None, timeout=None):
            captured.append({"params": params})
            return self._ok_payload()

        monkeypatch.setattr("clawmes.services.clawnch.http_get", _get)
        svc.prepare_deploy(from_address="0x" + "1" * 40, name="X", symbol="X")
        sent = captured[0]["params"]
        assert "description" not in sent
        assert "image" not in sent
        assert "twitter" not in sent
        assert "burnTxHash" not in sent

    def test_non_dict_body_raises(self, svc, monkeypatch):
        monkeypatch.setattr(
            "clawmes.services.clawnch.http_get",
            lambda *a, **k: ["not", "a", "dict"],
        )
        with pytest.raises(ClawnchError) as exc_info:
            svc.prepare_deploy(from_address="0x" + "1" * 40, name="X", symbol="X")
        assert exc_info.value.code == "api_error"

    def test_ok_false_rate_limited(self, svc, monkeypatch):
        monkeypatch.setattr(
            "clawmes.services.clawnch.http_get",
            lambda *a, **k: {
                "ok": False,
                "error": "Wallet has hit the 10 prepare/day cap.",
                "code": "rate_limited",
            },
        )
        with pytest.raises(ClawnchError) as exc_info:
            svc.prepare_deploy(from_address="0x" + "1" * 40, name="X", symbol="X")
        assert exc_info.value.code == "rate_limited"

    def test_ok_false_burn_required_carries_meta(self, svc, monkeypatch):
        """The mandatory-burn rejection maps to burn_required with meta."""
        monkeypatch.setattr(
            "clawmes.services.clawnch.http_get",
            lambda *a, **k: {
                "ok": False,
                "error": "This launch path now requires a verified 1,000,000 $CLAWNCH burn.",
                "code": "burn_required",
                "meta": {
                    "minBurnTokens": "1000000",
                    "burnAddress": "0x000000000000000000000000000000000000dEaD",
                },
            },
        )
        with pytest.raises(ClawnchError) as exc_info:
            svc.prepare_deploy(from_address="0x" + "1" * 40, name="X", symbol="X")
        assert exc_info.value.code == "burn_required"
        assert exc_info.value.meta["minBurnTokens"] == "1000000"
        assert exc_info.value.meta["burnAddress"].endswith("dEaD")

    def test_ok_false_burn_required_without_meta(self, svc, monkeypatch):
        monkeypatch.setattr(
            "clawmes.services.clawnch.http_get",
            lambda *a, **k: {"ok": False, "error": "burn it", "code": "burn_required"},
        )
        with pytest.raises(ClawnchError) as exc_info:
            svc.prepare_deploy(from_address="0x" + "1" * 40, name="X", symbol="X")
        assert exc_info.value.code == "burn_required"
        assert exc_info.value.meta == {}

    @pytest.mark.parametrize(
        "code",
        ["invalid_from", "invalid_name", "invalid_symbol", "missing_required", "invalid_burn"],
    )
    def test_ok_false_bad_request_codes(self, svc, monkeypatch, code):
        monkeypatch.setattr(
            "clawmes.services.clawnch.http_get",
            lambda *a, **k: {"ok": False, "error": "boom", "code": code},
        )
        with pytest.raises(ClawnchError) as exc_info:
            svc.prepare_deploy(from_address="0x" + "1" * 40, name="X", symbol="X")
        assert exc_info.value.code == "bad_request"

    def test_ok_false_unknown_code_maps_to_api_error(self, svc, monkeypatch):
        monkeypatch.setattr(
            "clawmes.services.clawnch.http_get",
            lambda *a, **k: {"ok": False, "error": "something", "code": "weird_code"},
        )
        with pytest.raises(ClawnchError) as exc_info:
            svc.prepare_deploy(from_address="0x" + "1" * 40, name="X", symbol="X")
        assert exc_info.value.code == "api_error"

    def test_ok_false_no_code_field(self, svc, monkeypatch):
        monkeypatch.setattr(
            "clawmes.services.clawnch.http_get",
            lambda *a, **k: {"ok": False, "error": "no code"},
        )
        with pytest.raises(ClawnchError) as exc_info:
            svc.prepare_deploy(from_address="0x" + "1" * 40, name="X", symbol="X")
        assert exc_info.value.code == "api_error"

    def test_ok_false_no_error_field(self, svc, monkeypatch):
        monkeypatch.setattr(
            "clawmes.services.clawnch.http_get",
            lambda *a, **k: {"ok": False, "code": "weird_code"},
        )
        with pytest.raises(ClawnchError) as exc_info:
            svc.prepare_deploy(from_address="0x" + "1" * 40, name="X", symbol="X")
        assert "prepare_deploy failed" in exc_info.value.message


class TestReads:
    def test_get_my_launches_requires_key(self, svc):
        svc.start()
        with pytest.raises(ClawnchError) as exc_info:
            svc.get_my_launches()
        assert exc_info.value.code == "no_credentials"

    def test_get_my_launches_calls_api(self, svc_with_key, monkeypatch):
        def _get(url, params=None, headers=None, timeout=None):
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

        def _get(url, params=None, headers=None, timeout=None):
            captured.append(url)
            return {"token": "0xabc"}

        monkeypatch.setattr("clawmes.services.clawnch.http_get", _get)
        svc.start()
        result = svc.get_launch("0xabc")
        assert result == {"token": "0xabc"}
        assert "address=0xabc" in captured[0]

    def test_get_launch_unauthed_works_without_key(self, svc, monkeypatch):
        captured: list[dict] = []

        def _get(url, params=None, headers=None, timeout=None):
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
        # Matches server-side default in api/lib/launch-bypass.ts.
        assert info["fee_eth"] == "0.005"

    def test_get_bypass_recipient_env_override(self, monkeypatch, svc):
        monkeypatch.setenv("CLAWNCH_BYPASS_RECIPIENT", "0xabc")
        monkeypatch.setenv("CLAWNCH_BYPASS_FEE_ETH", "0.01")
        info = svc.get_bypass_recipient()
        assert info["recipient"] == "0xabc"
        assert info["fee_eth"] == "0.01"

    def test_get_burn_config_default(self, svc):
        cfg = svc.get_burn_config()
        assert cfg["token_address"].startswith("0x")
        assert cfg["burn_address"].lower().endswith("dead")
        assert cfg["min_burn_tokens"] == 1_000_000

    def test_get_burn_config_env_override(self, monkeypatch, svc):
        monkeypatch.setenv("CLAWNCH_TOKEN_ADDRESS", "0xtoken")
        monkeypatch.setenv("CLAWNCH_BURN_ADDRESS", "0xburn")
        monkeypatch.setenv("CLAWNCH_MIN_BURN_TOKENS", "500000")
        cfg = svc.get_burn_config()
        assert cfg["token_address"] == "0xtoken"
        assert cfg["burn_address"] == "0xburn"
        assert cfg["min_burn_tokens"] == 500000


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

    def test_402_burn_required_code_carries_meta(self, svc_with_key, monkeypatch):
        """HTTP 402 burn_required maps to burn_required with the meta block."""
        exc = _HTTPErr(
            _FakeResponse(
                402,
                {
                    "ok": False,
                    "error": "This launch path now requires a verified 1,000,000 $CLAWNCH burn.",
                    "code": "burn_required",
                    "meta": {
                        "minBurnTokens": "1000000",
                        "burnAddress": "0x000000000000000000000000000000000000dEaD",
                    },
                },
            )
        )
        monkeypatch.setattr("clawmes.services.clawnch.http_post", self._trigger(svc_with_key, exc))
        with pytest.raises(ClawnchError) as exc_info:
            svc_with_key.start_deploy(token_params={"name": "x", "symbol": "X"})
        assert exc_info.value.code == "burn_required"
        assert exc_info.value.meta["minBurnTokens"] == "1000000"

    def test_burn_payment_required_legacy_code(self, svc_with_key, monkeypatch):
        """The custodial path's BURN_PAYMENT_REQUIRED hint maps the same way."""
        exc = _HTTPErr(_FakeResponse(402, {"error": "burn first", "code": "BURN_PAYMENT_REQUIRED"}))
        monkeypatch.setattr("clawmes.services.clawnch.http_post", self._trigger(svc_with_key, exc))
        with pytest.raises(ClawnchError) as exc_info:
            svc_with_key.start_deploy(token_params={"name": "x", "symbol": "X"})
        assert exc_info.value.code == "burn_required"
        assert exc_info.value.meta == {}

    def test_402_without_code_hint_to_burn_required(self, svc_with_key, monkeypatch):
        """Bare 402 (body unparseable / code missing) still classifies."""
        exc = _HTTPErr(_FakeResponse(402, {"error": "payment required"}))
        monkeypatch.setattr("clawmes.services.clawnch.http_post", self._trigger(svc_with_key, exc))
        with pytest.raises(ClawnchError) as exc_info:
            svc_with_key.start_deploy(token_params={"name": "x", "symbol": "X"})
        assert exc_info.value.code == "burn_required"

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

        def _get(url, params=None, headers=None, timeout=None):
            raise exc

        monkeypatch.setattr("clawmes.services.clawnch.http_get", _get)
        with pytest.raises(ClawnchError) as exc_info:
            svc_with_key.get_my_launches()
        assert exc_info.value.code == "not_found"

    def test_get_unclassifiable_propagates(self, svc_with_key, monkeypatch):
        class _Bare(Exception):
            pass

        def _get(url, params=None, headers=None, timeout=None):
            raise _Bare("transport down")

        monkeypatch.setattr("clawmes.services.clawnch.http_get", _get)
        with pytest.raises(_Bare):
            svc_with_key.get_my_launches()


# ──────────────────────────────────────────────────────────────────────
#  Robinhood Chain — launch ticket / deposit / claim / feed
# ──────────────────────────────────────────────────────────────────────

_TX = "0x" + "f" * 64
_ROUTER_TX = "0x" + "e" * 64
_WALLET = "0x" + "1" * 40


def _rh_ticket_response(**overrides):
    """The upstream ticket envelope (mirrors api/robinhood/ticket.ts)."""
    resp = {
        "ok": True,
        "data": {
            "to": "0xdd4e0000000000000000000000000000000053b5",
            "data": "0xdeadbeef",
            "value": "0x470de4df820000",
            "chainId": 4663,
        },
        "ticket": {
            "agent": _WALLET,
            "feeRecipient": _WALLET,
            "paramsHash": "0x" + "a" * 64,
            "nonce": "1",
            "deadline": "1800000000",
            "signature": "0x" + "b" * 130,
        },
        "meta": {
            "backend": "bags",
            "chain": "robinhood",
            "router": "0xdd4e0000000000000000000000000000000053b5",
            "depositAddress": "0xde0000000000000000000000000000000000ad",
            "creationFeeWei": "20000000000000000",
            "ttlSeconds": 600,
        },
    }
    resp.update(overrides)
    return resp


class TestRHUrlHelpers:
    def test_trade_url(self):
        from clawmes.services.clawnch import rh_trade_url

        assert rh_trade_url(ADDR) == f"https://bags.fm/token/{ADDR}"
        assert rh_trade_url("0xnothex") is None
        assert rh_trade_url("") is None
        assert rh_trade_url(None) is None  # type: ignore[arg-type]

    def test_explorer_token_url(self):
        from clawmes.services.clawnch import rh_explorer_token_url

        assert rh_explorer_token_url(ADDR) == (
            f"https://robinhoodchain.blockscout.com/token/{ADDR}"
        )
        assert rh_explorer_token_url("0xbad") is None

    def test_explorer_tx_url(self):
        from clawmes.services.clawnch import rh_explorer_tx_url

        assert rh_explorer_tx_url(_TX) == f"https://robinhoodchain.blockscout.com/tx/{_TX}"
        assert rh_explorer_tx_url("0xshort") is None
        assert rh_explorer_tx_url(ADDR) is None  # address is not a tx hash

    def test_is_tx_hash(self):
        from clawmes.services.clawnch import is_tx_hash

        assert is_tx_hash(_TX) is True
        assert is_tx_hash("0x" + "F" * 64) is True
        assert is_tx_hash("0X" + "f" * 64) is False
        assert is_tx_hash("0x" + "g" * 64) is False
        assert is_tx_hash(42) is False
        assert is_tx_hash(None) is False


class TestRHTokenInfo:
    def test_default_address(self, svc):
        info = svc.rh_token_info()
        assert info["token_address"] == "0x6a50F139F3eD4C9c7bDa0D067c5Ed09De1EEBbeA"
        assert info["chain"] == "robinhood"
        assert info["chain_id"] == 4663
        assert info["trade_url"].startswith("https://bags.fm/token/0x6a50")
        assert "robinhoodchain.blockscout.com" in info["explorer_url"]

    def test_env_override(self, monkeypatch, svc):
        monkeypatch.setenv("CLAWNCH_RH_TOKEN_ADDRESS", "0x" + "c" * 40)
        info = svc.rh_token_info()
        assert info["token_address"] == "0x" + "c" * 40


class TestGetBurnConfigChains:
    def test_default_is_base(self, svc):
        cfg = svc.get_burn_config()
        assert cfg["chain"] == "base"
        assert cfg["chain_id"] == 8453
        assert cfg["burn_required"] is True

    def test_robinhood_has_no_burn_and_rhc_token(self, svc):
        cfg = svc.get_burn_config(chain="robinhood")
        assert cfg["chain"] == "robinhood"
        assert cfg["chain_id"] == 4663
        assert cfg["token_address"] == "0x6a50F139F3eD4C9c7bDa0D067c5Ed09De1EEBbeA"
        assert cfg["burn_address"] is None
        assert cfg["min_burn_tokens"] == 0
        assert cfg["burn_required"] is False

    def test_robinhood_aliases(self, svc):
        for alias in ("rh", "4663", "ROBINHOOD"):
            assert svc.get_burn_config(chain=alias)["chain_id"] == 4663

    def test_unknown_chain_raises(self, svc):
        with pytest.raises(ClawnchError) as exc_info:
            svc.get_burn_config(chain="solana")
        assert exc_info.value.code == "bad_request"


class TestRHTicket:
    def test_requires_api_key(self, svc):
        svc.start()
        with pytest.raises(ClawnchError) as exc_info:
            svc.rh_ticket(agent_wallet=_WALLET, name="X", symbol="X")
        assert exc_info.value.code == "no_credentials"

    def test_requires_valid_wallet(self, svc_with_key):
        with pytest.raises(ClawnchError) as exc_info:
            svc_with_key.rh_ticket(agent_wallet="", name="X", symbol="X")
        assert exc_info.value.code == "bad_request"
        with pytest.raises(ClawnchError) as exc_info:
            svc_with_key.rh_ticket(agent_wallet="0xnope", name="X", symbol="X")
        assert exc_info.value.code == "bad_request"

    def test_requires_name_and_symbol(self, svc_with_key):
        with pytest.raises(ClawnchError) as exc_info:
            svc_with_key.rh_ticket(agent_wallet=_WALLET, name="", symbol="X")
        assert exc_info.value.code == "bad_request"
        with pytest.raises(ClawnchError) as exc_info:
            svc_with_key.rh_ticket(agent_wallet=_WALLET, name="X", symbol="")
        assert exc_info.value.code == "bad_request"

    def test_name_and_symbol_length_caps(self, svc_with_key):
        with pytest.raises(ClawnchError) as exc_info:
            svc_with_key.rh_ticket(agent_wallet=_WALLET, name="x" * 33, symbol="X")
        assert "name too long" in exc_info.value.message
        with pytest.raises(ClawnchError) as exc_info:
            svc_with_key.rh_ticket(agent_wallet=_WALLET, name="X", symbol="x" * 11)
        assert "symbol too long" in exc_info.value.message

    def test_rejects_bad_fee_recipient(self, svc_with_key):
        with pytest.raises(ClawnchError) as exc_info:
            svc_with_key.rh_ticket(
                agent_wallet=_WALLET, name="X", symbol="X", fee_recipient="0xnope"
            )
        assert exc_info.value.code == "bad_request"

    def test_posts_ticket_with_bearer_auth(self, svc_with_key, monkeypatch):
        captured: list[tuple] = []

        def _post(url, json, headers, timeout):  # noqa: A002
            captured.append((url, json, headers))
            return _rh_ticket_response()

        monkeypatch.setattr("clawmes.services.clawnch.http_post", _post)
        out = svc_with_key.rh_ticket(
            agent_wallet=_WALLET,
            name="MyCoin",
            symbol="MYC",
            description="desc",
            image="https://x/i.png",
            fee_recipient="0x" + "2" * 40,
        )
        assert out["ok"] is True
        url, body, headers = captured[0]
        assert url.endswith("/api/robinhood/ticket")
        assert headers["Authorization"] == "Bearer test-key"
        assert body["agentWallet"] == _WALLET
        assert body["name"] == "MyCoin"
        assert body["symbol"] == "MYC"
        assert body["description"] == "desc"
        assert body["image"] == "https://x/i.png"
        assert body["feeRecipient"] == "0x" + "2" * 40

    def test_minimal_body_omits_optionals(self, svc_with_key, monkeypatch):
        captured: list[dict] = []

        def _post(url, json, headers, timeout):  # noqa: A002
            captured.append(json)
            return _rh_ticket_response()

        monkeypatch.setattr("clawmes.services.clawnch.http_post", _post)
        svc_with_key.rh_ticket(agent_wallet=_WALLET, name="X", symbol="X")
        assert set(captured[0]) == {"agentWallet", "name", "symbol"}

    def test_refuses_wrong_chain_ticket(self, svc_with_key, monkeypatch):
        resp = _rh_ticket_response()
        resp["data"]["chainId"] = 8453

        def _post(url, json, headers, timeout):  # noqa: A002
            return resp

        monkeypatch.setattr("clawmes.services.clawnch.http_post", _post)
        with pytest.raises(ClawnchError) as exc_info:
            svc_with_key.rh_ticket(agent_wallet=_WALLET, name="X", symbol="X")
        assert exc_info.value.code == "api_error"
        assert "4663" in exc_info.value.message

    def test_refuses_wrong_chain_meta(self, svc_with_key, monkeypatch):
        resp = _rh_ticket_response()
        resp["data"].pop("chainId")
        resp["meta"]["chain"] = "base"

        def _post(url, json, headers, timeout):  # noqa: A002
            return resp

        monkeypatch.setattr("clawmes.services.clawnch.http_post", _post)
        with pytest.raises(ClawnchError) as exc_info:
            svc_with_key.rh_ticket(agent_wallet=_WALLET, name="X", symbol="X")
        assert exc_info.value.code == "api_error"

    def test_ok_false_wallet_mismatch_passthrough(self, svc_with_key, monkeypatch):
        def _post(url, json, headers, timeout):  # noqa: A002
            return {
                "ok": False,
                "error": "agentWallet must match the registered agent wallet",
                "code": "wallet_mismatch",
            }

        monkeypatch.setattr("clawmes.services.clawnch.http_post", _post)
        with pytest.raises(ClawnchError) as exc_info:
            svc_with_key.rh_ticket(agent_wallet=_WALLET, name="X", symbol="X")
        assert exc_info.value.code == "wallet_mismatch"

    def test_unauthorized_code_maps_to_no_credentials(self, svc_with_key, monkeypatch):
        def _post(url, json, headers, timeout):  # noqa: A002
            return {"ok": False, "error": "register first", "code": "unauthorized"}

        monkeypatch.setattr("clawmes.services.clawnch.http_post", _post)
        with pytest.raises(ClawnchError) as exc_info:
            svc_with_key.rh_ticket(agent_wallet=_WALLET, name="X", symbol="X")
        assert exc_info.value.code == "no_credentials"

    def test_non_dict_body_raises_api_error(self, svc_with_key, monkeypatch):
        monkeypatch.setattr(
            "clawmes.services.clawnch.http_post",
            lambda *a, **k: ["not", "a", "dict"],
        )
        with pytest.raises(ClawnchError) as exc_info:
            svc_with_key.rh_ticket(agent_wallet=_WALLET, name="X", symbol="X")
        assert exc_info.value.code == "api_error"

    def test_http_401_maps_to_no_credentials(self, svc_with_key, monkeypatch):
        exc = _HTTPErr(_FakeResponse(401, {"ok": False, "code": "unauthorized"}))

        def _post(url, json, headers, timeout):  # noqa: A002
            raise exc

        monkeypatch.setattr("clawmes.services.clawnch.http_post", _post)
        with pytest.raises(ClawnchError) as exc_info:
            svc_with_key.rh_ticket(agent_wallet=_WALLET, name="X", symbol="X")
        assert exc_info.value.code == "no_credentials"


class TestRHConfirmLaunch:
    def test_requires_api_key(self, svc):
        svc.start()
        with pytest.raises(ClawnchError) as exc_info:
            svc.rh_confirm_launch(tx_hash=_TX)
        assert exc_info.value.code == "no_credentials"

    def test_rejects_bad_tx_hash(self, svc_with_key):
        with pytest.raises(ClawnchError) as exc_info:
            svc_with_key.rh_confirm_launch(tx_hash="0x123")
        assert exc_info.value.code == "bad_request"

    def test_posts_confirm_mode(self, svc_with_key, monkeypatch):
        captured: list[tuple] = []

        def _post(url, json, headers, timeout):  # noqa: A002
            captured.append((url, json))
            return {"ok": True, "launch": {"token": ADDR, "agent": _WALLET, "mode": "ticket"}}

        monkeypatch.setattr("clawmes.services.clawnch.http_post", _post)
        out = svc_with_key.rh_confirm_launch(tx_hash=_TX)
        assert out["launch"]["token"] == ADDR
        url, body = captured[0]
        assert url.endswith("/api/robinhood/launch")
        assert body == {"mode": "confirm", "txHash": _TX}

    def test_tx_not_found_maps_to_not_found(self, svc_with_key, monkeypatch):
        exc = _HTTPErr(
            _FakeResponse(404, {"ok": False, "error": "not found", "code": "tx_not_found"})
        )

        def _post(url, json, headers, timeout):  # noqa: A002
            raise exc

        monkeypatch.setattr("clawmes.services.clawnch.http_post", _post)
        with pytest.raises(ClawnchError) as exc_info:
            svc_with_key.rh_confirm_launch(tx_hash=_TX)
        assert exc_info.value.code == "not_found"

    def test_not_agentic_launch_passthrough(self, svc_with_key, monkeypatch):
        exc = _HTTPErr(
            _FakeResponse(
                400,
                {"ok": False, "error": "no AgenticLaunch event", "code": "not_agentic_launch"},
            )
        )

        def _post(url, json, headers, timeout):  # noqa: A002
            raise exc

        monkeypatch.setattr("clawmes.services.clawnch.http_post", _post)
        with pytest.raises(ClawnchError) as exc_info:
            svc_with_key.rh_confirm_launch(tx_hash=_TX)
        assert exc_info.value.code == "not_agentic_launch"


class TestRHDepositLaunch:
    def test_requires_api_key(self, svc):
        svc.start()
        with pytest.raises(ClawnchError) as exc_info:
            svc.rh_deposit_launch(deposit_tx_hash=_TX, agent_wallet=_WALLET, name="X", symbol="X")
        assert exc_info.value.code == "no_credentials"

    def test_validations(self, svc_with_key):
        with pytest.raises(ClawnchError) as exc_info:
            svc_with_key.rh_deposit_launch(
                deposit_tx_hash=_TX, agent_wallet="0xnope", name="X", symbol="X"
            )
        assert exc_info.value.code == "bad_request"
        with pytest.raises(ClawnchError) as exc_info:
            svc_with_key.rh_deposit_launch(
                deposit_tx_hash="0xbad", agent_wallet=_WALLET, name="X", symbol="X"
            )
        assert exc_info.value.code == "bad_request"
        with pytest.raises(ClawnchError) as exc_info:
            svc_with_key.rh_deposit_launch(
                deposit_tx_hash=_TX, agent_wallet=_WALLET, name="", symbol="X"
            )
        assert exc_info.value.code == "bad_request"

    def test_posts_deposit_mode(self, svc_with_key, monkeypatch):
        captured: list[tuple] = []

        def _post(url, json, headers, timeout):  # noqa: A002
            captured.append((url, json))
            return {"ok": True, "launch": {"token": ADDR, "mode": "deposit"}}

        monkeypatch.setattr("clawmes.services.clawnch.http_post", _post)
        out = svc_with_key.rh_deposit_launch(
            deposit_tx_hash=_TX,
            agent_wallet=_WALLET,
            name="MyCoin",
            symbol="MYC",
            description="d",
            image="https://x/i.png",
        )
        assert out["launch"]["mode"] == "deposit"
        url, body = captured[0]
        assert url.endswith("/api/robinhood/launch")
        assert body["mode"] == "deposit"
        assert body["depositTxHash"] == _TX
        assert body["agentWallet"] == _WALLET
        assert body["name"] == "MyCoin"
        assert body["symbol"] == "MYC"
        assert body["description"] == "d"
        assert body["image"] == "https://x/i.png"

    def test_duplicate_deposit_passthrough(self, svc_with_key, monkeypatch):
        exc = _HTTPErr(
            _FakeResponse(409, {"ok": False, "error": "used", "code": "duplicate_deposit"})
        )

        def _post(url, json, headers, timeout):  # noqa: A002
            raise exc

        monkeypatch.setattr("clawmes.services.clawnch.http_post", _post)
        with pytest.raises(ClawnchError) as exc_info:
            svc_with_key.rh_deposit_launch(
                deposit_tx_hash=_TX, agent_wallet=_WALLET, name="X", symbol="X"
            )
        assert exc_info.value.code == "duplicate_deposit"

    def test_deposit_invalid_passthrough(self, svc_with_key, monkeypatch):
        exc = _HTTPErr(
            _FakeResponse(400, {"ok": False, "error": "bad deposit", "code": "deposit_invalid"})
        )

        def _post(url, json, headers, timeout):  # noqa: A002
            raise exc

        monkeypatch.setattr("clawmes.services.clawnch.http_post", _post)
        with pytest.raises(ClawnchError) as exc_info:
            svc_with_key.rh_deposit_launch(
                deposit_tx_hash=_TX, agent_wallet=_WALLET, name="X", symbol="X"
            )
        assert exc_info.value.code == "deposit_invalid"


class TestRHClaimable:
    _PAYLOAD = {
        "ok": True,
        "chainId": 4663,
        "token": ADDR,
        "address": _WALLET,
        "feeShare": "0x" + "e" * 40,
        "isClaimer": True,
        "bps": 4000,
        "claimableWei": "1000000000000000",
        "claim": {"to": "0x" + "e" * 40, "data": "0x1234", "value": "0x0", "chainId": 4663},
    }

    def test_validations(self, svc):
        svc.start()
        with pytest.raises(ClawnchError) as exc_info:
            svc.rh_claimable(token="0xnope", address=_WALLET)
        assert exc_info.value.code == "bad_request"
        with pytest.raises(ClawnchError) as exc_info:
            svc.rh_claimable(token=ADDR, address="0xnope")
        assert exc_info.value.code == "bad_request"

    def test_public_read_sends_params(self, svc, monkeypatch):
        captured: list[tuple] = []

        def _get(url, params=None, headers=None, timeout=None):
            captured.append((url, params, headers))
            return self._PAYLOAD

        monkeypatch.setattr("clawmes.services.clawnch.http_get", _get)
        svc.start()  # no key
        out = svc.rh_claimable(token=ADDR, address=_WALLET)
        assert out["claimableWei"] == "1000000000000000"
        url, params, headers = captured[0]
        assert url.endswith("/api/robinhood/claim")
        assert params == {"token": ADDR, "address": _WALLET}
        # Public read works without auth.
        assert "Authorization" not in headers

    def test_no_fee_share_passthrough(self, svc, monkeypatch):
        exc = _HTTPErr(
            _FakeResponse(404, {"ok": False, "error": "not a Bags token", "code": "no_fee_share"})
        )

        def _get(url, params=None, headers=None, timeout=None):
            raise exc

        monkeypatch.setattr("clawmes.services.clawnch.http_get", _get)
        svc.start()
        with pytest.raises(ClawnchError) as exc_info:
            svc.rh_claimable(token=ADDR, address=_WALLET)
        assert exc_info.value.code == "no_fee_share"


class TestRHClaim:
    def test_requires_api_key(self, svc):
        svc.start()
        with pytest.raises(ClawnchError) as exc_info:
            svc.rh_claim(token=ADDR)
        assert exc_info.value.code == "no_credentials"

    def test_requires_valid_token(self, svc_with_key):
        with pytest.raises(ClawnchError) as exc_info:
            svc_with_key.rh_claim(token="0xnope")
        assert exc_info.value.code == "bad_request"

    def test_posts_claim_request(self, svc_with_key, monkeypatch):
        captured: list[tuple] = []
        payload = {
            "ok": True,
            "ready": True,
            "claimableWei": "1000000000000000",
            "claimableEth": "0.001000",
            "claim": {
                "to": "0x" + "e" * 40,
                "data": "0xdeadbeef",
                "value": "0x0",
                "chainId": 4663,
            },
        }

        def _post(url, json, headers, timeout):  # noqa: A002
            captured.append((url, json, headers))
            return payload

        monkeypatch.setattr("clawmes.services.clawnch.http_post", _post)
        out = svc_with_key.rh_claim(token=ADDR)
        assert out["ready"] is True
        url, body, headers = captured[0]
        assert url.endswith("/api/robinhood/claim")
        assert body == {"token": ADDR}
        assert headers["Authorization"] == "Bearer test-key"

    def test_ready_false_is_not_an_error(self, svc_with_key, monkeypatch):
        def _post(url, json, headers, timeout):  # noqa: A002
            return {"ok": True, "ready": False, "claimableWei": "0", "claim": None}

        monkeypatch.setattr("clawmes.services.clawnch.http_post", _post)
        out = svc_with_key.rh_claim(token=ADDR)
        assert out["ready"] is False
        assert out["claim"] is None

    def test_refuses_wrong_chain_claim_tx(self, svc_with_key, monkeypatch):
        def _post(url, json, headers, timeout):  # noqa: A002
            return {
                "ok": True,
                "ready": True,
                "claim": {"to": "0x" + "e" * 40, "data": "0x", "value": "0x0", "chainId": 8453},
            }

        monkeypatch.setattr("clawmes.services.clawnch.http_post", _post)
        with pytest.raises(ClawnchError) as exc_info:
            svc_with_key.rh_claim(token=ADDR)
        assert exc_info.value.code == "api_error"
        assert "8453" in exc_info.value.message

    def test_not_claimer_403_stays_not_claimer(self, svc_with_key, monkeypatch):
        """A 403 must not be flattened to no_credentials when the body says
        the wallet simply isn't a claimer (the API key IS valid)."""
        exc = _HTTPErr(
            _FakeResponse(403, {"ok": False, "error": "not a claimer", "code": "not_claimer"})
        )

        def _post(url, json, headers, timeout):  # noqa: A002
            raise exc

        monkeypatch.setattr("clawmes.services.clawnch.http_post", _post)
        with pytest.raises(ClawnchError) as exc_info:
            svc_with_key.rh_claim(token=ADDR)
        assert exc_info.value.code == "not_claimer"


class TestRHLaunches:
    def test_rejects_bad_agent(self, svc):
        svc.start()
        with pytest.raises(ClawnchError) as exc_info:
            svc.rh_launches(agent="0xnope")
        assert exc_info.value.code == "bad_request"

    def test_rejects_bad_pagination(self, svc):
        svc.start()
        with pytest.raises(ClawnchError) as exc_info:
            svc.rh_launches(limit=0)
        assert exc_info.value.code == "bad_request"
        with pytest.raises(ClawnchError) as exc_info:
            svc.rh_launches(offset=-1)
        assert exc_info.value.code == "bad_request"
        with pytest.raises(ClawnchError) as exc_info:
            svc.rh_launches(limit="lots")  # type: ignore[arg-type]
        assert exc_info.value.code == "bad_request"

    def test_reads_and_decorates_rows(self, svc, monkeypatch):
        captured: list[tuple] = []
        payload = {
            "ok": True,
            "chain": "robinhood",
            "launches": [
                {
                    "token": ADDR,
                    "agent": _WALLET,
                    "name": "MyCoin",
                    "symbol": "MYC",
                    "mode": "ticket",
                    "txHash": _TX,
                    "routerTxHash": _ROUTER_TX,
                    "chainId": 4663,
                },
                {"token": "0xnothex", "name": "junk"},
            ],
            "pagination": {"limit": 50, "offset": 0, "total": 2, "hasMore": False},
        }

        def _get(url, params=None, headers=None, timeout=None):
            captured.append((url, params))
            return payload

        monkeypatch.setattr("clawmes.services.clawnch.http_get", _get)
        svc.start()
        out = svc.rh_launches()
        url, params = captured[0]
        assert url.endswith("/api/robinhood/launches")
        assert params == {"limit": "50", "offset": "0"}
        first = out["launches"][0]
        assert first["trade_url"] == f"https://bags.fm/token/{ADDR}"
        assert first["explorer_url"] == f"https://robinhoodchain.blockscout.com/token/{ADDR}"
        assert first["tx_url"] == f"https://robinhoodchain.blockscout.com/tx/{_TX}"
        assert first["router_tx_url"] == (f"https://robinhoodchain.blockscout.com/tx/{_ROUTER_TX}")
        assert first["chain"] == "robinhood"
        assert first["chain_id"] == 4663
        # Row with an invalid token gets no fabricated links.
        assert "trade_url" not in out["launches"][1]

    def test_clamps_limit_and_forwards_agent(self, svc, monkeypatch):
        captured: list[dict] = []

        def _get(url, params=None, headers=None, timeout=None):
            captured.append(params or {})
            return {"ok": True, "launches": [], "pagination": {}}

        monkeypatch.setattr("clawmes.services.clawnch.http_get", _get)
        svc.start()
        svc.rh_launches(agent=_WALLET, limit=9999, offset=5)
        assert captured[0]["limit"] == "200"
        assert captured[0]["offset"] == "5"
        assert captured[0]["agent"] == _WALLET

    def test_ok_false_raises(self, svc, monkeypatch):
        monkeypatch.setattr(
            "clawmes.services.clawnch.http_get",
            lambda *a, **k: {"ok": False, "error": "boom", "code": "launches_error"},
        )
        svc.start()
        with pytest.raises(ClawnchError) as exc_info:
            svc.rh_launches()
        assert exc_info.value.code == "api_error"


class TestRHCWrongChainGuards:
    """Base-only surfaces must refuse RHC requests instead of silently
    running against the wrong chain."""

    def test_deploy_robinhood_refused(self, svc):
        with pytest.raises(ClawnchError) as exc_info:
            svc.deploy(token_params={"name": "X", "symbol": "X"}, chain="robinhood")
        assert exc_info.value.code == "unsupported_chain"
        assert "rh_ticket" in exc_info.value.message

    def test_deploy_unknown_chain_refused(self, svc):
        with pytest.raises(ClawnchError) as exc_info:
            svc.deploy(token_params={"name": "X", "symbol": "X"}, chain="solana")
        assert exc_info.value.code == "bad_request"

    def test_deploy_base_default_still_works(self, svc_with_key, monkeypatch):
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
        out = svc_with_key.deploy(token_params={"name": "X", "symbol": "X"})
        assert out["success"] is True

    def test_prepare_deploy_robinhood_refused(self, svc):
        with pytest.raises(ClawnchError) as exc_info:
            svc.prepare_deploy(
                from_address="0x" + "1" * 40, name="X", symbol="X", chain="robinhood"
            )
        assert exc_info.value.code == "unsupported_chain"
        assert "rh_ticket" in exc_info.value.message

    def test_prepare_deploy_unknown_chain_refused(self, svc):
        with pytest.raises(ClawnchError) as exc_info:
            svc.prepare_deploy(from_address="0x" + "1" * 40, name="X", symbol="X", chain="solana")
        assert exc_info.value.code == "bad_request"

    def test_prepare_deploy_base_does_not_send_chain_param(self, svc, monkeypatch):
        captured: list[dict] = []

        def _get(url, params=None, headers=None, timeout=None):
            captured.append(params or {})
            return {
                "ok": True,
                "data": {"to": "0x1", "data": "0x2", "value": "0x0", "chainId": 8453},
                "meta": {},
            }

        monkeypatch.setattr("clawmes.services.clawnch.http_get", _get)
        svc.start()
        svc.prepare_deploy(from_address="0x" + "1" * 40, name="X", symbol="X", chain="base")
        # The server ignores the chain param — never send it.
        assert "chain" not in captured[0]


# ──────────────────────────────────────────────────────────────────────
#  Singleton
# ──────────────────────────────────────────────────────────────────────


class TestSingleton:
    def test_returns_same_instance(self):
        a = get_clawnch_service()
        b = get_clawnch_service()
        assert a is b
