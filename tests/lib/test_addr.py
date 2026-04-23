"""Tests for clawmes.lib.addr."""

from __future__ import annotations

from clawmes.lib.addr import (
    DEAD_ADDRESS,
    ZERO_ADDRESS,
    is_dead_address,
    is_ens_name,
    is_hex_address,
    is_zero_address,
    needs_ens_resolution,
    short,
)


class TestIsHexAddress:
    def test_lowercase(self):
        assert is_hex_address("0x" + "a" * 40)

    def test_uppercase(self):
        assert is_hex_address("0x" + "A" * 40)

    def test_mixed_case(self):
        assert is_hex_address("0xAbCd" + "ef" * 18)

    def test_too_short(self):
        assert not is_hex_address("0x" + "a" * 39)

    def test_too_long(self):
        assert not is_hex_address("0x" + "a" * 41)

    def test_no_prefix(self):
        assert not is_hex_address("a" * 40)

    def test_invalid_chars(self):
        assert not is_hex_address("0x" + "g" * 40)

    def test_empty(self):
        assert not is_hex_address("")


class TestIsEnsName:
    def test_simple(self):
        assert is_ens_name("vitalik.eth")

    def test_subdomain(self):
        assert is_ens_name("foo.bar.eth")

    def test_uppercase(self):
        assert is_ens_name("Vitalik.ETH")

    def test_with_hyphen(self):
        assert is_ens_name("alice-bob.eth")

    def test_no_eth_suffix(self):
        assert not is_ens_name("vitalik.com")

    def test_just_eth(self):
        assert not is_ens_name(".eth")

    def test_empty(self):
        assert not is_ens_name("")

    def test_address_not_ens(self):
        assert not is_ens_name("0x" + "a" * 40)


class TestZeroAndDead:
    def test_zero_address_const_is_canonical(self):
        assert is_hex_address(ZERO_ADDRESS)
        assert ZERO_ADDRESS == "0x" + "0" * 40

    def test_is_zero_address(self):
        assert is_zero_address(ZERO_ADDRESS)
        assert is_zero_address("0x" + "0" * 40)
        assert not is_zero_address("0x" + "0" * 39 + "1")

    def test_is_zero_address_rejects_non_address(self):
        assert not is_zero_address("not-an-address")

    def test_is_dead_address(self):
        assert is_dead_address(DEAD_ADDRESS)
        # Case-insensitive — "dead" can be in any case
        assert is_dead_address("0x" + "0" * 36 + "DEAD")
        assert is_dead_address("0x" + "0" * 36 + "dead")

    def test_is_dead_address_rejects_non_hex(self):
        # Cover line 39 — is_hex_address False branch returns False early
        assert is_dead_address("not-an-address") is False
        assert is_dead_address("") is False


class TestShort:
    def test_default(self):
        addr = "0x1234567890abcdef1234567890abcdef12345678"
        assert short(addr) == "0x1234…5678"

    def test_custom_head_tail(self):
        addr = "0x1234567890abcdef1234567890abcdef12345678"
        assert short(addr, head=4, tail=2) == "0x12…78"

    def test_non_address_returned_unchanged(self):
        assert short("not-an-address") == "not-an-address"
        assert short("vitalik.eth") == "vitalik.eth"


class TestNeedsEnsResolution:
    def test_yes(self):
        assert needs_ens_resolution("alice.eth")

    def test_no(self):
        assert not needs_ens_resolution("0x" + "a" * 40)
        assert not needs_ens_resolution("not-anything")


class TestToChecksum:
    def test_with_eth_utils(self):
        # Valid hex address — should return checksummed (or at least
        # accept what eth_utils gives us)
        from clawmes.lib.addr import to_checksum

        addr = "0xfb6916095ca1df60bb79ce92ce3ea74c37c5d359"
        result = to_checksum(addr)
        assert result.lower() == addr.lower()

    def test_no_eth_utils_fallback(self, monkeypatch):
        """Cover the ``except ImportError`` branch when eth_utils is missing."""
        import sys

        from clawmes.lib import addr as addr_mod

        # Force `from eth_utils import to_checksum_address` to fail
        real_import = (
            __builtins__["__import__"]
            if isinstance(__builtins__, dict)
            else __builtins__.__import__
        )

        def fake_import(name, *args, **kwargs):
            if name == "eth_utils":
                raise ImportError("simulated")
            return real_import(name, *args, **kwargs)

        if isinstance(__builtins__, dict):
            monkeypatch.setitem(__builtins__, "__import__", fake_import)
        else:
            monkeypatch.setattr(__builtins__, "__import__", fake_import)
        monkeypatch.delitem(sys.modules, "eth_utils", raising=False)

        # Valid hex → fallback returns lowercase
        addr = "0xABCDEF" + "0" * 34
        assert addr_mod.to_checksum(addr) == addr.lower()

    def test_no_eth_utils_invalid_address(self, monkeypatch):
        """Fallback raises ValueError on a non-hex input."""
        import sys

        from clawmes.lib import addr as addr_mod

        real_import = (
            __builtins__["__import__"]
            if isinstance(__builtins__, dict)
            else __builtins__.__import__
        )

        def fake_import(name, *args, **kwargs):
            if name == "eth_utils":
                raise ImportError("simulated")
            return real_import(name, *args, **kwargs)

        if isinstance(__builtins__, dict):
            monkeypatch.setitem(__builtins__, "__import__", fake_import)
        else:
            monkeypatch.setattr(__builtins__, "__import__", fake_import)
        monkeypatch.delitem(sys.modules, "eth_utils", raising=False)

        with pytest.raises(ValueError, match="Not a hex address"):
            addr_mod.to_checksum("not-an-address")


class TestZeroAddressEdgeCases:
    def test_is_zero_address_with_valid_hex_non_zero(self):
        # Cover line 39 — is_hex_address True branch + is_zero_address False
        assert not is_zero_address("0x" + "a" * 40)


import pytest  # noqa: E402  (used by TestToChecksum.test_no_eth_utils_invalid_address)
