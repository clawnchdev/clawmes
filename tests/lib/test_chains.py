"""Tests for clawmes.lib.chains."""

from __future__ import annotations

import pytest

from clawmes.lib.chains import (
    CHAINS,
    Chain,
    chain_ids,
    default_chain_id,
    get_chain,
    is_supported,
)


class TestRegistry:
    def test_base_present(self):
        chain = CHAINS[8453]
        assert chain.short_name == "base"
        assert chain.is_l2

    def test_mainnet_not_l2(self):
        chain = CHAINS[1]
        assert chain.is_l2 is False

    def test_default_is_base(self):
        assert default_chain_id() == 8453

    def test_chain_ids_includes_8453(self):
        assert 8453 in chain_ids()

    def test_is_supported_yes(self):
        assert is_supported(1)
        assert is_supported(8453)

    def test_is_supported_no(self):
        assert not is_supported(999999)


class TestGetChain:
    def test_by_id(self):
        c = get_chain(8453)
        assert c.short_name == "base"

    def test_by_short_name(self):
        c = get_chain("base")
        assert c.chain_id == 8453

    def test_by_short_name_case_insensitive(self):
        assert get_chain("BASE").chain_id == 8453
        assert get_chain("Base").chain_id == 8453

    def test_by_full_name(self):
        c = get_chain("Ethereum Mainnet")
        assert c.chain_id == 1

    def test_by_full_name_case_insensitive(self):
        assert get_chain("ethereum mainnet").chain_id == 1

    def test_unknown_id(self):
        with pytest.raises(KeyError, match="Unknown chain id"):
            get_chain(999999)

    def test_unknown_name(self):
        with pytest.raises(KeyError, match="Unknown chain"):
            get_chain("not-a-chain")

    def test_whitespace_in_name(self):
        # Should strip whitespace
        assert get_chain("  base  ").chain_id == 8453


class TestChainDataclass:
    def test_frozen(self):
        from dataclasses import FrozenInstanceError

        chain = Chain(
            chain_id=1,
            name="Test",
            short_name="test",
            native_symbol="ETH",
            native_decimals=18,
            block_explorer_url="https://example.com",
        )
        with pytest.raises(FrozenInstanceError):
            chain.chain_id = 2  # type: ignore[misc]
