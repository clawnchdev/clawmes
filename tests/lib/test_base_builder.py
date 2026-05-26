"""Tests for clawmes.lib.base_builder."""

from __future__ import annotations

from clawmes.lib.base_builder import BASE_BUILDER_CODE, append_builder_code


class TestAppendBuilderCode:
    def test_appends_on_base(self):
        out = append_builder_code("0xdeadbeef", 8453)
        assert out.startswith("0xdeadbeef")
        assert out.endswith(BASE_BUILDER_CODE[2:])

    def test_pass_through_on_other_chains(self):
        assert append_builder_code("0xdeadbeef", 1) == "0xdeadbeef"
        assert append_builder_code("0xdeadbeef", 42161) == "0xdeadbeef"

    def test_pass_through_on_none_chain(self):
        assert append_builder_code("0xdeadbeef", None) == "0xdeadbeef"

    def test_adds_prefix_to_raw_hex(self):
        # Defensive — if a caller forgets the 0x prefix, we add it
        # before appending so the output is well-formed.
        out = append_builder_code("deadbeef", 8453)
        assert out.startswith("0xdeadbeef")
        assert out.endswith(BASE_BUILDER_CODE[2:])

    def test_constant_shape(self):
        # Sanity: the suffix is hex-prefixed and even-length so it slices
        # cleanly into bytes when appended to other calldata.
        assert BASE_BUILDER_CODE.startswith("0x")
        body = BASE_BUILDER_CODE[2:]
        assert len(body) % 2 == 0
        assert all(c in "0123456789abcdefABCDEF" for c in body)
