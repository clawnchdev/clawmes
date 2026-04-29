"""Tests for the five niche/stub tools.

giza, nookplot, paysponge, lobster_cash are HTTP-wrapper read/write
tools requiring API keys. _user_tools is a dynamic dispatcher.
"""

from __future__ import annotations

import json

import pytest


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    from clawmes.policy import storage as policy_storage

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("GIZA_API_KEY", raising=False)
    monkeypatch.delenv("NOOKPLOT_API_KEY", raising=False)
    monkeypatch.delenv("PAYSPONGE_API_KEY", raising=False)
    monkeypatch.delenv("LOBSTER_API_KEY", raising=False)
    policy_storage.save_policies([])


def _stub_http(monkeypatch, module_path: str, attr: str, response):
    def fake(*args, **kwargs):
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(f"{module_path}.{attr}", fake)


# --- Giza ----------------------------------------------------------------


class TestGiza:
    def test_no_api_key(self):
        from clawmes.tools.giza import giza

        out = json.loads(giza({"action": "models"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "no_credentials"

    def test_models(self, monkeypatch):
        monkeypatch.setenv("GIZA_API_KEY", "k")
        _stub_http(monkeypatch, "clawmes.tools.giza", "http_get", {"models": []})
        from clawmes.tools.giza import giza

        out = json.loads(giza({"action": "models"}))
        assert "isError" not in out

    def test_inference(self, monkeypatch):
        monkeypatch.setenv("GIZA_API_KEY", "k")
        _stub_http(monkeypatch, "clawmes.tools.giza", "http_post", {"prediction": 0.5})
        from clawmes.tools.giza import giza

        out = json.loads(
            giza(
                {
                    "action": "inference",
                    "model_id": "m-1",
                    "input_data": {"x": 1},
                }
            )
        )
        assert "isError" not in out

    def test_inference_missing_input(self, monkeypatch):
        monkeypatch.setenv("GIZA_API_KEY", "k")
        from clawmes.tools.giza import giza

        out = json.loads(giza({"action": "inference", "model_id": "m-1"}))
        assert out["isError"] is True

    def test_verify(self, monkeypatch):
        monkeypatch.setenv("GIZA_API_KEY", "k")
        _stub_http(monkeypatch, "clawmes.tools.giza", "http_get", {"valid": True})
        from clawmes.tools.giza import giza

        out = json.loads(giza({"action": "verify", "proof_id": "p-1"}))
        assert "isError" not in out

    def test_api_error(self, monkeypatch):
        monkeypatch.setenv("GIZA_API_KEY", "k")
        _stub_http(
            monkeypatch,
            "clawmes.tools.giza",
            "http_get",
            RuntimeError("network"),
        )
        from clawmes.tools.giza import giza

        out = json.loads(giza({"action": "models"}))
        assert out["isError"] is True


# --- Nookplot ------------------------------------------------------------


class TestNookplot:
    def test_analyze(self, monkeypatch):
        _stub_http(monkeypatch, "clawmes.tools.nookplot", "http_get", {"engagement": 0.7})
        from clawmes.tools.nookplot import nookplot

        out = json.loads(nookplot({"action": "analyze", "fid": 12345}))
        assert "isError" not in out

    def test_analyze_no_fid(self):
        from clawmes.tools.nookplot import nookplot

        out = json.loads(nookplot({"action": "analyze"}))
        assert out["isError"] is True

    def test_top_creators(self, monkeypatch):
        _stub_http(monkeypatch, "clawmes.tools.nookplot", "http_get", {"creators": []})
        from clawmes.tools.nookplot import nookplot

        out = json.loads(nookplot({"action": "top_creators"}))
        assert "isError" not in out

    def test_engagement(self, monkeypatch):
        _stub_http(monkeypatch, "clawmes.tools.nookplot", "http_get", {"likes": 5})
        from clawmes.tools.nookplot import nookplot

        out = json.loads(nookplot({"action": "engagement", "cast_hash": "0xabc"}))
        assert "isError" not in out

    def test_with_api_key(self, monkeypatch):
        monkeypatch.setenv("NOOKPLOT_API_KEY", "k")
        _stub_http(monkeypatch, "clawmes.tools.nookplot", "http_get", {"data": []})
        from clawmes.tools.nookplot import nookplot

        out = json.loads(nookplot({"action": "top_creators"}))
        assert "isError" not in out

    def test_api_error(self, monkeypatch):
        _stub_http(
            monkeypatch,
            "clawmes.tools.nookplot",
            "http_get",
            RuntimeError("network"),
        )
        from clawmes.tools.nookplot import nookplot

        out = json.loads(nookplot({"action": "top_creators"}))
        assert out["isError"] is True


# --- Paysponge -----------------------------------------------------------


class TestPaysponge:
    def test_no_api_key(self):
        from clawmes.tools.paysponge import paysponge

        out = json.loads(
            paysponge(
                {
                    "action": "quote",
                    "from_currency": "USD",
                    "to_currency": "USDC",
                    "amount": "100",
                }
            )
        )
        assert out["isError"] is True
        assert out["details"]["error_code"] == "no_credentials"

    def test_quote(self, monkeypatch):
        monkeypatch.setenv("PAYSPONGE_API_KEY", "k")
        _stub_http(
            monkeypatch,
            "clawmes.tools.paysponge",
            "http_get",
            {"rate": 1.0},
        )
        from clawmes.tools.paysponge import paysponge

        out = json.loads(
            paysponge(
                {
                    "action": "quote",
                    "from_currency": "USD",
                    "to_currency": "USDC",
                    "amount": "100",
                }
            )
        )
        assert "isError" not in out

    def test_buy(self, monkeypatch):
        monkeypatch.setenv("PAYSPONGE_API_KEY", "k")
        _stub_http(
            monkeypatch,
            "clawmes.tools.paysponge",
            "http_post",
            {"order_id": "o1"},
        )
        from clawmes.tools.paysponge import paysponge

        out = json.loads(
            paysponge(
                {
                    "action": "buy",
                    "from_currency": "USD",
                    "to_currency": "USDC",
                    "amount": "100",
                    "destination": "0x" + "a" * 40,
                }
            )
        )
        assert "isError" not in out

    def test_sell(self, monkeypatch):
        monkeypatch.setenv("PAYSPONGE_API_KEY", "k")
        _stub_http(
            monkeypatch,
            "clawmes.tools.paysponge",
            "http_post",
            {"order_id": "o1"},
        )
        from clawmes.tools.paysponge import paysponge

        out = json.loads(
            paysponge(
                {
                    "action": "sell",
                    "from_currency": "USDC",
                    "to_currency": "USD",
                    "amount": "100",
                    "destination": "bank-account",
                }
            )
        )
        assert "isError" not in out

    def test_kyc_status(self, monkeypatch):
        monkeypatch.setenv("PAYSPONGE_API_KEY", "k")
        _stub_http(
            monkeypatch,
            "clawmes.tools.paysponge",
            "http_get",
            {"status": "verified"},
        )
        from clawmes.tools.paysponge import paysponge

        out = json.loads(paysponge({"action": "kyc_status"}))
        assert "isError" not in out

    def test_api_error(self, monkeypatch):
        monkeypatch.setenv("PAYSPONGE_API_KEY", "k")
        _stub_http(
            monkeypatch,
            "clawmes.tools.paysponge",
            "http_get",
            RuntimeError("network"),
        )
        from clawmes.tools.paysponge import paysponge

        out = json.loads(paysponge({"action": "kyc_status"}))
        assert out["isError"] is True


# --- Lobster -------------------------------------------------------------


class TestLobster:
    def test_no_api_key(self):
        from clawmes.tools.lobster_cash import lobster_cash

        out = json.loads(lobster_cash({"action": "deposit", "amount": "100"}))
        assert out["isError"] is True

    def test_deposit(self, monkeypatch):
        monkeypatch.setenv("LOBSTER_API_KEY", "k")
        _stub_http(
            monkeypatch,
            "clawmes.tools.lobster_cash",
            "http_post",
            {"note": "0xnote"},
        )
        from clawmes.tools.lobster_cash import lobster_cash

        out = json.loads(lobster_cash({"action": "deposit", "amount": "100"}))
        assert "isError" not in out

    def test_withdraw(self, monkeypatch):
        monkeypatch.setenv("LOBSTER_API_KEY", "k")
        _stub_http(
            monkeypatch,
            "clawmes.tools.lobster_cash",
            "http_post",
            {"tx": "0xtx"},
        )
        from clawmes.tools.lobster_cash import lobster_cash

        out = json.loads(
            lobster_cash(
                {
                    "action": "withdraw",
                    "note": "0xnote",
                    "destination": "0x" + "a" * 40,
                }
            )
        )
        assert "isError" not in out

    def test_proof(self, monkeypatch):
        monkeypatch.setenv("LOBSTER_API_KEY", "k")
        _stub_http(
            monkeypatch,
            "clawmes.tools.lobster_cash",
            "http_post",
            {"proof": "0xproof"},
        )
        from clawmes.tools.lobster_cash import lobster_cash

        out = json.loads(lobster_cash({"action": "proof", "note": "0xn"}))
        assert "isError" not in out

    def test_api_error(self, monkeypatch):
        monkeypatch.setenv("LOBSTER_API_KEY", "k")
        _stub_http(
            monkeypatch,
            "clawmes.tools.lobster_cash",
            "http_post",
            RuntimeError("network"),
        )
        from clawmes.tools.lobster_cash import lobster_cash

        out = json.loads(lobster_cash({"action": "deposit", "amount": "100"}))
        assert out["isError"] is True


# --- _user_tools ---------------------------------------------------------


class TestUserToolsDispatcher:
    def test_missing_tool_name(self):
        from clawmes.tools._user_tools import _user_tools

        out = json.loads(_user_tools({}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "param_error"

    def test_dunder_blocked(self):
        from clawmes.tools._user_tools import _user_tools

        out = json.loads(_user_tools({"tool_name": "_evil"}))
        assert out["isError"] is True

    def test_self_blocked(self):
        from clawmes.tools._user_tools import _user_tools

        out = json.loads(_user_tools({"tool_name": "_user_tools"}))
        assert out["isError"] is True

    def test_not_found(self):
        from clawmes.tools._user_tools import _user_tools

        out = json.loads(_user_tools({"tool_name": "doesnt_exist"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "not_found"

    def test_handler_loaded_and_called(self, tmp_path, monkeypatch):
        from clawmes.tools._user_tools import _user_tools, _user_tools_dir

        d = _user_tools_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / "my_tool.py").write_text(
            'def handler(args, **kwargs):\n    return f\'echo:{args.get("x", "none")}\'\n',
            encoding="utf-8",
        )
        out = _user_tools({"tool_name": "my_tool", "args": {"x": "hello"}})
        # Returned a string — passed through verbatim
        assert "echo:hello" in out

    def test_handler_returns_dict(self, tmp_path):
        from clawmes.tools._user_tools import _user_tools, _user_tools_dir

        d = _user_tools_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / "dict_tool.py").write_text(
            "def handler(args, **kwargs):\n    return {'ok': True}\n",
            encoding="utf-8",
        )
        out = json.loads(_user_tools({"tool_name": "dict_tool"}))
        # Wrapped in json_result
        assert "isError" not in out
        assert out["details"]["result"] == {"ok": True}

    def test_handler_raises(self, tmp_path):
        from clawmes.tools._user_tools import _user_tools, _user_tools_dir

        d = _user_tools_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / "bad_tool.py").write_text(
            "def handler(args, **kwargs):\n    raise ValueError('boom')\n",
            encoding="utf-8",
        )
        out = json.loads(_user_tools({"tool_name": "bad_tool"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "tool_error"

    def test_args_not_dict_defaults_to_empty(self, tmp_path):
        from clawmes.tools._user_tools import _user_tools, _user_tools_dir

        d = _user_tools_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / "argless.py").write_text(
            "def handler(args, **kwargs):\n    return f'got:{len(args)}'\n",
            encoding="utf-8",
        )
        out = _user_tools({"tool_name": "argless", "args": "not-a-dict"})
        assert "got:0" in out

    def test_no_handler_function(self, tmp_path):
        from clawmes.tools._user_tools import _user_tools, _user_tools_dir

        d = _user_tools_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / "broken.py").write_text(
            "X = 1\n",
            encoding="utf-8",
        )
        out = json.loads(_user_tools({"tool_name": "broken"}))
        assert out["isError"] is True
        assert out["details"]["error_code"] == "not_found"


# --- Register hooks ------------------------------------------------------


class TestRegister:
    @pytest.mark.parametrize(
        "module_path, expected_name",
        [
            ("clawmes.tools.giza", "giza"),
            ("clawmes.tools.nookplot", "nookplot"),
            ("clawmes.tools.paysponge", "paysponge"),
            ("clawmes.tools.lobster_cash", "lobster_cash"),
            ("clawmes.tools._user_tools", "_user_tools"),
        ],
    )
    def test_register(self, module_path, expected_name):
        import importlib

        mod = importlib.import_module(module_path)
        recorded = []

        class FakeCtx:
            def register_tool(self, **kw):
                recorded.append(kw)

        mod.register(FakeCtx())
        assert recorded[0]["name"] == expected_name
