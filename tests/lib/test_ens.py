"""Tests for ``clawmes.lib.ens`` — namehash + on-chain resolution."""

from __future__ import annotations

import pytest

from clawmes.lib.ens import (
    ENS_REGISTRY,
    EnsError,
    _decode_address,
    is_ens_name,
    namehash,
    resolve,
)


class TestIsEnsName:
    def test_plain_eth_name(self):
        assert is_ens_name("vitalik.eth") is True

    def test_subdomain(self):
        assert is_ens_name("app.foo.eth") is True

    def test_non_eth_tld(self):
        # OffchainResolver chains use other TLDs
        assert is_ens_name("alice.cb.id") is True

    def test_hex_address_lowercase(self):
        assert is_ens_name("0x" + "a" * 40) is False

    def test_hex_address_uppercase_prefix(self):
        assert is_ens_name("0X" + "a" * 40) is False

    def test_no_dot_no_match(self):
        assert is_ens_name("vitalik") is False

    def test_empty(self):
        assert is_ens_name("") is False


class TestNamehash:
    def test_empty_returns_zero(self):
        assert namehash("") == b"\x00" * 32

    def test_canonical_eth_namehash(self):
        # The hash of 'eth' is a well-known constant in the ENS spec.
        # https://docs.ens.domains/contract-api-reference/name-processing
        h = namehash("eth").hex()
        assert h == "93cdeb708b7545dc668eb9280176169d1c33cfd8ed6f04690a0bcc88a93fc4ae"

    def test_canonical_vitalik_dot_eth(self):
        # Canonical test vector for vitalik.eth — independently verifiable.
        h = namehash("vitalik.eth").hex()
        assert h == "ee6c4522aab0003e8d14cd40a6af439055fd2577951148c14b6cea9a53475835"

    def test_lowercases_input(self):
        # Names are canonicalized lowercase before hashing, so casing
        # doesn't affect the result.
        assert namehash("VITALIK.ETH") == namehash("vitalik.eth")

    def test_subdomain_consistency(self):
        # Subdomain hash chains: namehash('a.b.c') = keccak(namehash('b.c') || keccak('a'))
        from eth_utils import keccak

        expected = keccak(namehash("b.c") + keccak(b"a"))
        assert namehash("a.b.c") == expected


class TestDecodeAddress:
    def test_empty(self):
        assert _decode_address("") is None
        assert _decode_address("0x") is None
        assert _decode_address(None) is None

    def test_zero_address(self):
        assert _decode_address("0x" + "0" * 64) == "0x" + "0" * 40

    def test_real_address(self):
        # Right-most 20 bytes of the 32-byte slot
        body = "0" * 24 + "a" * 40
        assert _decode_address("0x" + body) == "0x" + "a" * 40

    def test_malformed_length(self):
        # Not 64 hex chars after 0x
        assert _decode_address("0xabc") is None


class TestResolve:
    @pytest.fixture(autouse=True)
    def _isolate(self, monkeypatch):
        from clawmes.services import rpc as rpc_mod

        monkeypatch.setattr(rpc_mod, "_instance", None)

    @pytest.fixture
    def fake_rpc(self, monkeypatch):
        from clawmes.lib import ens as ens_mod

        rpc = type("FakeRpc", (), {})()
        rpc.calls: list[dict] = []  # type: ignore[attr-defined]
        rpc.responses: list[str] = []  # type: ignore[attr-defined]

        def eth_call(*, to, data, chain_id, block="latest"):
            rpc.calls.append({"to": to, "data": data, "chain_id": chain_id})
            if not rpc.responses:
                raise AssertionError("no fake eth_call response queued")
            return rpc.responses.pop(0)

        rpc.eth_call = eth_call  # type: ignore[attr-defined]
        # ens.py binds get_rpc_service at import time, so we patch the
        # already-bound attribute, not the source module.
        monkeypatch.setattr(ens_mod, "get_rpc_service", lambda: rpc)
        return rpc

    def test_happy_path(self, fake_rpc):
        # Resolver address: 0xfff...fff (any non-zero)
        resolver = "0x" + "0" * 24 + "f" * 40
        # User address: 0xaaa...aaa
        user_addr = "0x" + "0" * 24 + "a" * 40
        fake_rpc.responses = [resolver, user_addr]
        addr = resolve("vitalik.eth")
        # Checksummed (uppercase 0xA's via eth_utils)
        assert addr.lower() == "0x" + "a" * 40
        # Two RPC calls — registry lookup, then resolver lookup
        assert len(fake_rpc.calls) == 2
        assert fake_rpc.calls[0]["to"] == ENS_REGISTRY
        assert fake_rpc.calls[0]["chain_id"] == 1

    def test_not_registered(self, fake_rpc):
        # Registry returns the zero address for the resolver
        fake_rpc.responses = ["0x" + "0" * 64]
        with pytest.raises(EnsError) as exc_info:
            resolve("nope.eth")
        assert exc_info.value.code == "not_registered"

    def test_no_address(self, fake_rpc):
        # Resolver exists, but addr() returns zero
        resolver = "0x" + "0" * 24 + "f" * 40
        fake_rpc.responses = [resolver, "0x" + "0" * 64]
        with pytest.raises(EnsError) as exc_info:
            resolve("registered-but-empty.eth")
        assert exc_info.value.code == "no_address"

    def test_registry_rpc_failure(self, fake_rpc):
        from clawmes.services.rpc import RpcError

        def boom(**kw):
            raise RpcError(-32000, "execution reverted", method="eth_call")

        fake_rpc.eth_call = boom  # type: ignore[attr-defined]
        with pytest.raises(EnsError) as exc_info:
            resolve("vitalik.eth")
        assert exc_info.value.code == "rpc_error"

    def test_resolver_rpc_failure(self, fake_rpc, monkeypatch):
        from clawmes.services.rpc import RpcError

        # First call succeeds (returns resolver), second raises
        resolver = "0x" + "0" * 24 + "f" * 40
        call_count = [0]

        def staged(**kw):
            call_count[0] += 1
            if call_count[0] == 1:
                return resolver
            raise RpcError(-32000, "resolver down", method="eth_call")

        fake_rpc.eth_call = staged  # type: ignore[attr-defined]
        with pytest.raises(EnsError) as exc_info:
            resolve("vitalik.eth")
        assert exc_info.value.code == "rpc_error"
        assert "addr lookup" in exc_info.value.message

    def test_checksum_fallback_on_eth_utils_failure(self, fake_rpc, monkeypatch):
        # If to_checksum_address blows up unexpectedly, we fall back to
        # the lowercase address rather than failing the resolution.
        resolver = "0x" + "0" * 24 + "f" * 40
        user_addr = "0x" + "0" * 24 + "b" * 40
        fake_rpc.responses = [resolver, user_addr]

        import eth_utils

        def bad_checksum(_):
            raise RuntimeError("checksum unavailable")

        monkeypatch.setattr(eth_utils, "to_checksum_address", bad_checksum)
        addr = resolve("vitalik.eth")
        assert addr == "0x" + "b" * 40

    def test_basename_routes_to_base_l2(self, fake_rpc):
        # .base.eth names should query the BASE_ENS_REGISTRY on chain 8453
        from clawmes.lib.ens import BASE_ENS_REGISTRY

        resolver = "0x" + "0" * 24 + "f" * 40
        user_addr = "0x" + "0" * 24 + "c" * 40
        fake_rpc.responses = [resolver, user_addr]
        addr = resolve("jesse.base.eth")
        # eth_utils returns mixed-case checksum — just verify it's the
        # same underlying address (case-insensitive comparison).
        assert addr.lower() == "0x" + "c" * 40
        # Both eth_calls should hit Base mainnet (chain_id=8453) on the
        # Base ENS Registry, not the Ethereum mainnet registry.
        assert fake_rpc.calls[0]["chain_id"] == 8453
        assert fake_rpc.calls[0]["to"] == BASE_ENS_REGISTRY
        assert fake_rpc.calls[1]["chain_id"] == 8453


class TestIsBasename:
    def test_yes(self):
        from clawmes.lib.ens import is_basename

        assert is_basename("jesse.base.eth")
        assert is_basename("JESSE.BASE.ETH")

    def test_no(self):
        from clawmes.lib.ens import is_basename

        assert not is_basename("vitalik.eth")
        assert not is_basename("foo.bar")
        assert not is_basename("")
