"""Tests for the delegation-related RpcService additions.

Covers ``get_code`` and the ``CLAWMES_RPC_<id>`` discovery of extra chains
(testnets / Linea) the delegation framework supports.
"""

from __future__ import annotations

from clawmes.services.rpc import RpcService


class TestGetCode:
    def test_get_code_returns_hex(self, monkeypatch):
        svc = RpcService()
        monkeypatch.setattr(svc, "_call", lambda cid, method, params: "0xdeadbeef")
        assert svc.get_code("0x" + "11" * 20, 8453) == "0xdeadbeef"

    def test_get_code_none_becomes_0x(self, monkeypatch):
        svc = RpcService()
        monkeypatch.setattr(svc, "_call", lambda cid, method, params: None)
        assert svc.get_code("0x" + "11" * 20, 8453) == "0x"

    def test_get_code_passes_block(self, monkeypatch):
        svc = RpcService()
        captured = {}

        def _call(cid, method, params):
            captured["method"] = method
            captured["params"] = params
            return "0x"

        monkeypatch.setattr(svc, "_call", _call)
        svc.get_code("0xabc", 1, block="pending")
        assert captured["method"] == "eth_getCode"
        assert captured["params"] == ["0xabc", "pending"]


class TestExtraChainDiscovery:
    def test_testnet_via_env(self, monkeypatch):
        monkeypatch.setenv("CLAWMES_RPC_84532", "https://sepolia.base.example")
        svc = RpcService()
        svc.start()
        assert svc.has_endpoint(84532)
        assert not svc.is_default_endpoint(84532)

    def test_linea_via_env(self, monkeypatch):
        monkeypatch.setenv("CLAWMES_RPC_59144", "https://linea.example")
        svc = RpcService()
        svc.start()
        assert svc.has_endpoint(59144)

    def test_non_numeric_suffix_ignored(self, monkeypatch):
        monkeypatch.setenv("CLAWMES_RPC_FOO", "https://nope.example")
        svc = RpcService()
        svc.start()
        # Only the default chains remain; the bogus var is skipped.
        assert 8453 in svc.configured_chain_ids()

    def test_empty_value_ignored(self, monkeypatch):
        monkeypatch.setenv("CLAWMES_RPC_84532", "")
        svc = RpcService()
        svc.start()
        assert not svc.has_endpoint(84532)

    def test_override_default_chain_still_works(self, monkeypatch):
        monkeypatch.setenv("CLAWMES_RPC_8453", "https://my-base.example")
        svc = RpcService()
        svc.start()
        assert not svc.is_default_endpoint(8453)
