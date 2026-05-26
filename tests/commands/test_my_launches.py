"""Tests for the /my_launches slash command."""

from __future__ import annotations

import pytest

from clawmes.commands import my_launches as ml_mod
from clawmes.lib import dexscreener
from clawmes.services.clawnch import ClawnchError
from clawmes.wallet.state import WalletState


class _FakeSvc:
    def __init__(self):
        self.return_body: object = {"launches": []}
        self.raise_exc: Exception | None = None

    def get_my_launches(self):
        if self.raise_exc:
            raise self.raise_exc
        return self.return_body


@pytest.fixture
def fake_svc(monkeypatch):
    s = _FakeSvc()
    monkeypatch.setattr(ml_mod, "get_clawnch_service", lambda: s)
    return s


@pytest.fixture
def fake_wallet(monkeypatch):
    state: dict = {"connected": False, "address": None}

    def _state():
        if state["connected"]:
            return WalletState.for_chain(
                mode="local",
                address=state["address"] or "0x" + "a" * 40,
                chain_id=8453,
            )
        return WalletState.disconnected()

    monkeypatch.setattr(ml_mod, "get_wallet_state", _state)
    return state


@pytest.fixture
def fake_basescan_get(monkeypatch):
    state: dict = {"body": {"status": "1", "result": []}, "raises": None}

    def _fake(url, *, params=None, timeout=None):  # noqa: ARG001
        if state["raises"]:
            raise state["raises"]
        return state["body"]

    monkeypatch.setattr(ml_mod, "http_get", _fake)
    return state


@pytest.fixture
def fake_find_token(monkeypatch):
    state: dict = {"by_addr": {}}

    def _fake(addr, *, chain="base"):  # noqa: ARG001
        return state["by_addr"].get(addr)

    monkeypatch.setattr(dexscreener, "find_token", _fake)
    return state


# ── arg parsing ─────────────────────────────────────────────────────


class TestParseArgs:
    def test_default(self):
        assert ml_mod._parse_args("") == "clawnch"

    def test_clawnch(self):
        assert ml_mod._parse_args("--clawnch") == "clawnch"

    def test_all(self):
        assert ml_mod._parse_args("--all") == "all"

    def test_last_wins(self):
        assert ml_mod._parse_args("--all --clawnch") == "clawnch"


# ── --clawnch path ──────────────────────────────────────────────────


class TestClawnchUniverse:
    async def test_no_credentials(self, fake_svc):
        fake_svc.raise_exc = ClawnchError("no_credentials", "no key")
        out = await ml_mod.handle_my_launches("--clawnch")
        assert "no_credentials" in out
        assert "/register_agent" in out

    async def test_other_error(self, fake_svc):
        fake_svc.raise_exc = ClawnchError("api_error", "boom")
        out = await ml_mod.handle_my_launches("--clawnch")
        assert "api_error" in out
        assert "/register_agent" not in out

    async def test_empty_list(self, fake_svc):
        fake_svc.return_body = {"launches": []}
        out = await ml_mod.handle_my_launches("--clawnch")
        assert "No Clawnch launches" in out
        assert "--all" in out

    async def test_renders(self, fake_svc):
        fake_svc.return_body = {
            "launches": [
                {
                    "symbol": "MNEME",
                    "name": "MNEME",
                    "tokenAddress": "0x" + "1" * 40,
                }
            ]
        }
        out = await ml_mod.handle_my_launches("--clawnch")
        assert "Your Clawnch launches (1)" in out
        assert "MNEME" in out

    async def test_default_is_clawnch(self, fake_svc):
        fake_svc.return_body = {"launches": []}
        out = await ml_mod.handle_my_launches("")
        assert "No Clawnch launches" in out


# ── --all path ──────────────────────────────────────────────────────


class TestAllUniverse:
    async def test_no_wallet(self, fake_wallet):
        fake_wallet["connected"] = False
        out = await ml_mod.handle_my_launches("--all")
        assert "No wallet connected" in out
        assert "/my_launches" in out

    async def test_basescan_error(self, fake_wallet, fake_basescan_get):
        fake_wallet["connected"] = True
        fake_wallet["address"] = "0x" + "1" * 40
        fake_basescan_get["raises"] = RuntimeError("rate limited")
        out = await ml_mod.handle_my_launches("--all")
        assert "rate limited" in out

    async def test_no_creations(self, fake_wallet, fake_basescan_get):
        fake_wallet["connected"] = True
        fake_wallet["address"] = "0x" + "1" * 40
        fake_basescan_get["body"] = {"status": "1", "result": []}
        out = await ml_mod.handle_my_launches("--all")
        assert "No contract creations" in out

    async def test_basescan_status_0_means_empty(self, fake_wallet, fake_basescan_get):
        # status="0" + "No transactions found" is normal-empty, not an error
        fake_wallet["connected"] = True
        fake_basescan_get["body"] = {"status": "0", "message": "No transactions found"}
        out = await ml_mod.handle_my_launches("--all")
        assert "No contract creations" in out

    async def test_basescan_non_dict_body(self, fake_wallet, fake_basescan_get):
        fake_wallet["connected"] = True
        fake_basescan_get["body"] = ["not", "a", "dict"]
        out = await ml_mod.handle_my_launches("--all")
        assert "No contract creations" in out

    async def test_basescan_non_list_result(self, fake_wallet, fake_basescan_get):
        fake_wallet["connected"] = True
        fake_basescan_get["body"] = {"status": "1", "result": "garbage"}
        out = await ml_mod.handle_my_launches("--all")
        assert "No contract creations" in out

    async def test_basescan_filter_drops_non_creations(
        self, fake_wallet, fake_basescan_get, fake_find_token
    ):
        # All rows fail the (to=="" AND contractAddress truthy) filter,
        # so basescan helper returns []. Surface message is the same as
        # "no creations at all".
        fake_wallet["connected"] = True
        fake_basescan_get["body"] = {
            "status": "1",
            "result": [
                {"to": "0xnonempty", "contractAddress": "0xshouldbeskipped"},
                "garbage",
                {"to": "", "contractAddress": ""},
            ],
        }
        out = await ml_mod.handle_my_launches("--all")
        assert "No contract creations" in out

    async def test_renders_with_and_without_dex_listing(
        self, fake_wallet, fake_basescan_get, fake_find_token
    ):
        fake_wallet["connected"] = True
        addr_listed = "0x" + "1" * 40
        addr_unlisted = "0x" + "2" * 40
        fake_basescan_get["body"] = {
            "status": "1",
            "result": [
                {"to": "", "contractAddress": addr_listed},
                {"to": "", "contractAddress": addr_unlisted},
            ],
        }
        fake_find_token["by_addr"][addr_listed] = {
            "baseToken": {"symbol": "L", "address": addr_listed},
            "priceUsd": "1",
            "marketCap": 100_000,
            "volume": {"h24": 10_000},
        }
        out = await ml_mod.handle_my_launches("--all")
        assert "L" in out
        assert "no DEX listing" in out
        assert "Pass --clawnch" in out

    async def test_max_results_cap(self, fake_wallet, fake_basescan_get, fake_find_token):
        fake_wallet["connected"] = True
        # 30 creations — should cap at _MAX_RESULTS (25)
        results = [{"to": "", "contractAddress": f"0x{i:040x}"} for i in range(1, 31)]
        fake_basescan_get["body"] = {"status": "1", "result": results}
        out = await ml_mod.handle_my_launches("--all")
        assert "no DEX listing" in out
        # Should see #25 but not #26
        rendered = out.split("\n")
        hit25 = [line for line in rendered if line.strip().startswith("25.")]
        assert len(hit25) == 1
        assert not any(line.strip().startswith("26.") for line in rendered)


# ── basescan_contract_creations env var path ───────────────────────


class TestBasescanApiKey:
    def test_with_api_key(self, monkeypatch):
        captured: dict = {}

        def _fake(url, *, params=None, timeout=None):  # noqa: ARG001
            captured["params"] = params
            return {"status": "1", "result": []}

        monkeypatch.setattr(ml_mod, "http_get", _fake)
        monkeypatch.setenv("BASESCAN_API_KEY", "my-key")
        ml_mod._basescan_contract_creations("0x" + "1" * 40)
        assert captured["params"]["apikey"] == "my-key"

    def test_without_api_key(self, monkeypatch):
        captured: dict = {}

        def _fake(url, *, params=None, timeout=None):  # noqa: ARG001
            captured["params"] = params
            return {"status": "1", "result": []}

        monkeypatch.setattr(ml_mod, "http_get", _fake)
        monkeypatch.delenv("BASESCAN_API_KEY", raising=False)
        ml_mod._basescan_contract_creations("0x" + "1" * 40)
        assert "apikey" not in captured["params"]


# ── shape extractors / formatters ───────────────────────────────────


class TestExtractLaunches:
    def test_none(self):
        assert ml_mod._extract_launches(None) == []

    def test_list_filters_non_dicts(self):
        assert ml_mod._extract_launches([{"a": 1}, "garbage"]) == [{"a": 1}]

    def test_tokens_key(self):
        assert ml_mod._extract_launches({"tokens": [{"a": 1}]}) == [{"a": 1}]

    def test_data_key(self):
        assert ml_mod._extract_launches({"data": [{"a": 1}]}) == [{"a": 1}]

    def test_results_key(self):
        assert ml_mod._extract_launches({"results": [{"a": 1}]}) == [{"a": 1}]

    def test_dict_no_known_key(self):
        assert ml_mod._extract_launches({"random": "value"}) == []


class TestFormatLaunch:
    def test_minimal(self):
        out = ml_mod._format_clawnch_launch({})
        assert "?" in out

    def test_full(self):
        out = ml_mod._format_clawnch_launch(
            {
                "symbol": "MNEME",
                "name": "MNEME Coin",
                "tokenAddress": "0x" + "1" * 40,
            }
        )
        assert "MNEME" in out
        assert "MNEME Coin" in out

    def test_redundant_name_dropped(self):
        out = ml_mod._format_clawnch_launch({"symbol": "X", "name": "X"})
        assert out.count("X") == 1


class TestShort:
    def test_short_input(self):
        assert ml_mod._short("0xabc") == "0xabc"

    def test_truncates(self):
        assert "…" in ml_mod._short("0x" + "1" * 40)


# ── command_history best-effort ────────────────────────────────────


class TestRecordingBestEffort:
    async def test_recording_failure_does_not_break(self, monkeypatch, fake_svc):
        from clawmes.services import command_history as ch_mod

        def _boom(*a, **kw):
            raise RuntimeError("history broken")

        monkeypatch.setattr(ch_mod, "record_command_call", _boom)
        out = await ml_mod.handle_my_launches("--clawnch")
        assert isinstance(out, str)


class TestRegister:
    def test_registers(self):
        captured = []

        class FakeCtx:
            def register_command(self, **kw):
                captured.append(kw["name"])

        ml_mod.register(FakeCtx())
        assert captured == ["my_launches"]
