"""Tests for clawmes.lib.ui_artifacts."""

from __future__ import annotations

from clawmes.lib.ui_artifacts import (
    PREVIEW_TRIGGER_KEYS,
    attach_preview,
    clanker_url,
    dexscreener_url,
    enrich_token_links,
    enrich_tx_links,
    explorer_address_url,
    explorer_token_url,
    explorer_tx_url,
    will_auto_open,
)

_TX = "0x" + "a" * 64
_ADDR = "0x" + "b" * 40
_BAD = "0xnothex"
# Correct length but contains a non-hex character — exercises the per-char
# validation branch (not just the length short-circuit).
_TX_NONHEX = "0x" + "g" * 64
_ADDR_NONHEX = "0x" + "g" * 40


class TestExplorerTxUrl:
    def test_base(self):
        assert explorer_tx_url(_TX, 8453) == f"https://basescan.org/tx/{_TX}"

    def test_ethereum(self):
        assert explorer_tx_url(_TX, 1) == f"https://etherscan.io/tx/{_TX}"

    def test_bad_hash_returns_none(self):
        assert explorer_tx_url(_BAD, 8453) is None

    def test_address_is_not_a_tx_hash(self):
        # 40-hex address is not a 64-hex tx hash.
        assert explorer_tx_url(_ADDR, 8453) is None

    def test_unknown_chain_returns_none(self):
        assert explorer_tx_url(_TX, 999999) is None

    def test_non_string_returns_none(self):
        assert explorer_tx_url(None, 8453) is None  # type: ignore[arg-type]

    def test_correct_length_nonhex_returns_none(self):
        assert explorer_tx_url(_TX_NONHEX, 8453) is None


class TestExplorerAddressUrl:
    def test_base(self):
        assert explorer_address_url(_ADDR, 8453) == f"https://basescan.org/address/{_ADDR}"

    def test_bad_addr_returns_none(self):
        assert explorer_address_url(_BAD, 8453) is None

    def test_unknown_chain_returns_none(self):
        assert explorer_address_url(_ADDR, 999999) is None

    def test_correct_length_nonhex_returns_none(self):
        assert explorer_address_url(_ADDR_NONHEX, 8453) is None


class TestExplorerTokenUrl:
    def test_base(self):
        assert explorer_token_url(_ADDR, 8453) == f"https://basescan.org/token/{_ADDR}"

    def test_bad_returns_none(self):
        assert explorer_token_url(_BAD, 8453) is None

    def test_unknown_chain_returns_none(self):
        assert explorer_token_url(_ADDR, 999999) is None


class TestDexscreenerUrl:
    def test_base(self):
        assert dexscreener_url(_ADDR, 8453) == f"https://dexscreener.com/base/{_ADDR}"

    def test_ethereum(self):
        assert dexscreener_url(_ADDR, 1) == f"https://dexscreener.com/ethereum/{_ADDR}"

    def test_unindexed_chain_returns_none(self):
        # A real chain id that DexScreener doesn't have a slug for here.
        assert dexscreener_url(_ADDR, 999999) is None

    def test_bad_addr_returns_none(self):
        assert dexscreener_url(_BAD, 8453) is None

    def test_non_0x_value_returns_none(self):
        # Exercises the not-startswith-0x guard in _is_address.
        assert dexscreener_url("notanaddress", 8453) is None


class TestClankerUrl:
    def test_base(self):
        assert clanker_url(_ADDR, 8453) == f"https://clanker.world/clanker/{_ADDR}"

    def test_default_chain_is_base(self):
        assert clanker_url(_ADDR) == f"https://clanker.world/clanker/{_ADDR}"

    def test_non_base_returns_none(self):
        assert clanker_url(_ADDR, 1) is None

    def test_bad_addr_returns_none(self):
        assert clanker_url(_BAD, 8453) is None


class TestEnrichTxLinks:
    def test_adds_explorer_url(self):
        details: dict = {"tx_hash": _TX}
        enrich_tx_links(details, tx_hash=_TX, chain_id=8453)
        assert details["explorer_url"] == f"https://basescan.org/tx/{_TX}"

    def test_returns_same_dict(self):
        details: dict = {}
        assert enrich_tx_links(details, tx_hash=_TX, chain_id=8453) is details

    def test_does_not_overwrite_existing(self):
        details = {"explorer_url": "https://custom.example/tx"}
        enrich_tx_links(details, tx_hash=_TX, chain_id=8453)
        assert details["explorer_url"] == "https://custom.example/tx"

    def test_noop_on_bad_hash(self):
        details: dict = {}
        enrich_tx_links(details, tx_hash=_BAD, chain_id=8453)
        assert "explorer_url" not in details

    def test_explorer_url_does_not_auto_open(self):
        # explorer_url is NOT a preview-trigger key, so it must not auto-open.
        details: dict = {}
        enrich_tx_links(details, tx_hash=_TX, chain_id=8453)
        assert will_auto_open(details) is False


class TestEnrichTokenLinks:
    def test_adds_all_base_links(self):
        details: dict = {}
        enrich_token_links(details, token=_ADDR, chain_id=8453)
        assert details["dexscreener_url"] == f"https://dexscreener.com/base/{_ADDR}"
        assert details["token_explorer_url"] == f"https://basescan.org/token/{_ADDR}"
        assert details["clanker_url"] == f"https://clanker.world/clanker/{_ADDR}"

    def test_no_clanker_off_base(self):
        details: dict = {}
        enrich_token_links(details, token=_ADDR, chain_id=1)
        assert "clanker_url" not in details
        assert details["dexscreener_url"] == f"https://dexscreener.com/ethereum/{_ADDR}"

    def test_include_clanker_false(self):
        details: dict = {}
        enrich_token_links(details, token=_ADDR, chain_id=8453, include_clanker=False)
        assert "clanker_url" not in details

    def test_preserves_existing(self):
        details = {
            "dexscreener_url": "x",
            "token_explorer_url": "y",
            "clanker_url": "z",
        }
        enrich_token_links(details, token=_ADDR, chain_id=8453)
        assert details == {"dexscreener_url": "x", "token_explorer_url": "y", "clanker_url": "z"}

    def test_noop_on_bad_token(self):
        details: dict = {}
        enrich_token_links(details, token=_BAD, chain_id=8453)
        assert details == {}

    def test_returns_same_dict(self):
        details: dict = {}
        assert enrich_token_links(details, token=_ADDR, chain_id=8453) is details

    def test_token_links_do_not_auto_open(self):
        details: dict = {}
        enrich_token_links(details, token=_ADDR, chain_id=8453)
        assert will_auto_open(details) is False


class TestAttachPreview:
    def test_sets_preview(self):
        details: dict = {}
        attach_preview(details, "/tmp/card.html")
        assert details["preview"] == "/tmp/card.html"

    def test_empty_path_noop(self):
        details: dict = {}
        attach_preview(details, "")
        assert "preview" not in details

    def test_coerces_to_str(self, tmp_path):
        details: dict = {}
        card = tmp_path / "card.html"
        attach_preview(details, card)  # type: ignore[arg-type]
        assert details["preview"] == str(card)

    def test_returns_same_dict(self):
        details: dict = {}
        assert attach_preview(details, "/x.html") is details

    def test_preview_triggers_auto_open(self):
        details: dict = {}
        attach_preview(details, "/tmp/card.html")
        assert will_auto_open(details) is True


class TestWillAutoOpen:
    def test_url_key_triggers(self):
        assert will_auto_open({"url": "https://example.com"}) is True

    def test_http_target(self):
        assert will_auto_open({"target": "http://x.io"}) is True

    def test_file_uri(self):
        assert will_auto_open({"path": "file:///tmp/x"}) is True

    def test_relative_path(self):
        assert will_auto_open({"file": "./out.html"}) is True

    def test_home_path(self):
        assert will_auto_open({"filepath": "~/out.html"}) is True

    def test_non_target_value(self):
        assert will_auto_open({"url": "just a string"}) is False

    def test_non_string_value(self):
        assert will_auto_open({"url": 123}) is False

    def test_empty(self):
        assert will_auto_open({}) is False

    def test_trigger_keys_constant(self):
        assert PREVIEW_TRIGGER_KEYS == ("url", "target", "path", "file", "filepath", "preview")
