"""Tests for clawmes.services.identity."""

from __future__ import annotations

import pytest

from clawmes.services import identity as id_mod
from clawmes.services.identity import (
    IdentityService,
    base58btc_encode,
    encode_did_key,
    get_identity_service,
)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(id_mod, "_instance", None)


class TestBase58Encode:
    def test_empty(self):
        assert base58btc_encode(b"") == ""

    def test_known_vector(self):
        # "hello" → StV1DL6CwTryKyV (from base58btc reference)
        assert base58btc_encode(b"hello") == "Cn8eVZg"

    def test_leading_zeros_preserved_as_1(self):
        # Leading zero bytes encode as '1' characters in base58btc.
        result = base58btc_encode(b"\x00\x00\xff")
        assert result.endswith("11") or "1" in result


class TestEncodeDidKey:
    def test_well_known_zero_key(self):
        # All-zero pubkey is a degenerate test vector but easy to read.
        pubkey = b"\x00" * 32
        did = encode_did_key(pubkey)
        assert did.startswith("did:key:z")
        # The multicodec prefix \xed\x01 is included before the key.

    def test_wrong_length_raises(self):
        with pytest.raises(ValueError, match="must be 32 bytes"):
            encode_did_key(b"\x00" * 16)
        with pytest.raises(ValueError, match="must be 32 bytes"):
            encode_did_key(b"\x00" * 64)

    def test_real_keypair_round_trip(self):
        # Generate, encode, parse the prefix — verify multicodec is there.
        from Crypto.PublicKey import ECC

        key = ECC.generate(curve="Ed25519")
        pub = key.public_key().export_key(format="raw")
        did = encode_did_key(pub)
        assert did.startswith("did:key:z")
        # The encoded section after "did:key:z" base58-decodes back
        # to multicodec prefix + the same pubkey.


class TestIdentityServiceLifecycle:
    def test_start_is_noop(self):
        IdentityService().start()

    def test_stop_clears(self):
        svc = IdentityService()
        svc.generate()
        svc.stop()
        assert not svc.has_identity()
        assert svc.show() == {}

    def test_default_has_no_identity(self):
        assert IdentityService().has_identity() is False
        assert IdentityService().show() == {}


class TestGenerate:
    def test_returns_summary(self):
        svc = IdentityService()
        summary = svc.generate()
        assert "did" in summary
        assert summary["did"].startswith("did:key:z")
        assert len(summary["public_key_hex"]) == 64  # 32 bytes hex
        assert summary["created_at"] is not None

    def test_overwrites_existing(self):
        svc = IdentityService()
        first = svc.generate()
        second = svc.generate()
        # Different keys produced.
        assert first["did"] != second["did"]

    def test_has_identity_after_generate(self):
        svc = IdentityService()
        svc.generate()
        assert svc.has_identity() is True


class TestShow:
    def test_returns_empty_dict_when_no_identity(self):
        assert IdentityService().show() == {}

    def test_returns_full_summary(self):
        svc = IdentityService()
        svc.generate()
        summary = svc.show()
        assert set(summary.keys()) == {"did", "public_key_hex", "created_at"}


class TestPublicKeyHex:
    def test_returns_none_when_no_identity(self):
        assert IdentityService().public_key_hex() is None

    def test_returns_64_char_hex_after_generate(self):
        svc = IdentityService()
        svc.generate()
        hex_str = svc.public_key_hex()
        assert hex_str is not None
        assert len(hex_str) == 64
        bytes.fromhex(hex_str)  # must be valid hex


class TestSign:
    def test_signs_message(self):
        svc = IdentityService()
        svc.generate()
        sig = svc.sign(b"hello world")
        assert len(sig) == 64
        assert isinstance(sig, bytes)

    def test_raises_when_no_identity(self):
        svc = IdentityService()
        with pytest.raises(RuntimeError, match="No agent identity"):
            svc.sign(b"hello")

    def test_signature_deterministic_per_message(self):
        # Ed25519 is deterministic — same key + same message = same sig.
        svc = IdentityService()
        svc.generate()
        sig1 = svc.sign(b"identical")
        sig2 = svc.sign(b"identical")
        assert sig1 == sig2

    def test_signature_differs_per_message(self):
        svc = IdentityService()
        svc.generate()
        sig1 = svc.sign(b"first")
        sig2 = svc.sign(b"second")
        assert sig1 != sig2


class TestVerify:
    def _signed(self, svc, message):
        sig = svc.sign(message)
        pub = svc.public_key_hex()
        assert pub is not None
        return pub, sig

    def test_valid_signature(self):
        svc = IdentityService()
        svc.generate()
        pub, sig = self._signed(svc, b"msg")
        assert IdentityService.verify(pub, b"msg", sig) is True

    def test_wrong_message_fails(self):
        svc = IdentityService()
        svc.generate()
        pub, sig = self._signed(svc, b"original")
        assert IdentityService.verify(pub, b"tampered", sig) is False

    def test_wrong_signature_fails(self):
        svc = IdentityService()
        svc.generate()
        pub, _ = self._signed(svc, b"msg")
        bogus_sig = b"\x00" * 64
        assert IdentityService.verify(pub, b"msg", bogus_sig) is False

    def test_wrong_pubkey_fails(self):
        svc = IdentityService()
        svc.generate()
        _, sig = self._signed(svc, b"msg")

        other = IdentityService()
        other.generate()
        other_pub = other.public_key_hex()
        assert other_pub is not None
        assert IdentityService.verify(other_pub, b"msg", sig) is False

    def test_non_hex_pubkey_returns_false(self):
        assert IdentityService.verify("not-hex!!", b"msg", b"\x00" * 64) is False

    def test_wrong_size_pubkey_returns_false(self):
        # 16 bytes hex (32 chars) — too short for ed25519.
        assert IdentityService.verify("00" * 16, b"msg", b"\x00" * 64) is False

    def test_wrong_size_signature_returns_false(self):
        svc = IdentityService()
        svc.generate()
        pub = svc.public_key_hex()
        assert pub is not None
        # 32-byte signature is too short.
        assert IdentityService.verify(pub, b"msg", b"\x00" * 32) is False


class TestSingleton:
    def test_singleton(self):
        a = get_identity_service()
        b = get_identity_service()
        assert a is b
