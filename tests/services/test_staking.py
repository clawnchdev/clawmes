"""Tests for clawmes.services.staking."""

from __future__ import annotations

import pytest

from clawmes.services.staking import (
    LIDO_STETH_MAINNET,
    RP_RETH_MAINNET,
    SELECTOR_LIDO_SUBMIT,
    SELECTOR_RP_DEPOSIT,
    StakingError,
    deposit_target,
    protocol_name,
    receipt_token,
    supported_protocols,
    supports,
)


class TestSupports:
    def test_lido_mainnet(self):
        assert supports("lido", 1)

    def test_rocketpool_mainnet(self):
        assert supports("rocketpool", 1)

    def test_l2_unsupported(self):
        # Both protocols are mainnet-only at this milestone
        assert not supports("lido", 8453)
        assert not supports("rocketpool", 42161)

    def test_unknown_protocol(self):
        assert not supports("ankr", 1)


class TestDepositTarget:
    def test_lido_includes_referral_zero(self):
        contract, calldata = deposit_target("lido", 1)
        assert contract == LIDO_STETH_MAINNET
        assert calldata.startswith(SELECTOR_LIDO_SUBMIT)
        # Selector + 32-byte zero referral
        assert calldata.endswith("0" * 64)

    def test_rocketpool_no_args(self):
        contract, calldata = deposit_target("rocketpool", 1)
        assert contract == RP_RETH_MAINNET
        assert calldata == SELECTOR_RP_DEPOSIT

    def test_unsupported_chain(self):
        with pytest.raises(StakingError):
            deposit_target("lido", 8453)

    def test_unknown_protocol(self):
        with pytest.raises(StakingError):
            deposit_target("nonexistent", 1)


class TestReceiptToken:
    def test_lido(self):
        assert receipt_token("lido", 1) == LIDO_STETH_MAINNET

    def test_rocketpool(self):
        assert receipt_token("rocketpool", 1) == RP_RETH_MAINNET

    def test_unsupported(self):
        with pytest.raises(StakingError):
            receipt_token("lido", 8453)


class TestProtocolName:
    def test_known(self):
        assert "Lido" in protocol_name("lido")
        assert "Rocket Pool" in protocol_name("rocketpool")

    def test_unknown_raises(self):
        with pytest.raises(StakingError):
            protocol_name("ankr")


class TestSupportedProtocols:
    def test_returns_both(self):
        protocols = supported_protocols()
        assert "lido" in protocols
        assert "rocketpool" in protocols
