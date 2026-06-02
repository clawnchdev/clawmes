"""Tests for clawmes.lib.ui_cards."""

from __future__ import annotations

from pathlib import Path

from clawmes.lib import ui_cards


class TestRenderCard:
    def test_includes_title_and_body(self):
        out = ui_cards.render_card("My Title", "<p>hi</p>")
        assert "My Title" in out
        assert "<p>hi</p>" in out
        assert out.startswith("<!doctype html>")

    def test_subtitle_rendered_when_set(self):
        out = ui_cards.render_card("T", "b", subtitle="sub here")
        assert "sub here" in out
        assert '<span class="card-sub">' in out

    def test_subtitle_absent_when_empty(self):
        out = ui_cards.render_card("T", "b")
        # the .card-sub CSS rule is always present; the span is not
        assert '<span class="card-sub">' not in out

    def test_footer_rendered_when_set(self):
        out = ui_cards.render_card("T", "b", footer="foot text")
        assert "foot text" in out
        assert "foot" in out

    def test_footer_absent_when_empty(self):
        out = ui_cards.render_card("T", "b")
        assert 'class="foot"' not in out

    def test_title_is_escaped(self):
        out = ui_cards.render_card("<script>alert(1)</script>", "b")
        assert "<script>alert(1)</script>" not in out
        assert "&lt;script&gt;" in out

    def test_subtitle_and_footer_escaped(self):
        out = ui_cards.render_card("T", "b", subtitle="<i>s</i>", footer="<i>f</i>")
        assert "<i>s</i>" not in out
        assert "<i>f</i>" not in out


class TestKvSection:
    def test_renders_rows(self):
        out = ui_cards.kv_section("Heading", [("Key", "Val")])
        assert "Heading" in out
        assert "Key" in out
        assert "Val" in out

    def test_empty_rows_returns_empty(self):
        assert ui_cards.kv_section("H", []) == ""

    def test_key_is_escaped(self):
        out = ui_cards.kv_section("H", [("<b>k</b>", "v")])
        assert "<b>k</b>" not in out

    def test_value_html_passthrough(self):
        # values may contain pre-built HTML (mono spans, colour) by design
        out = ui_cards.kv_section("H", [("k", '<span class="mono">0xabc</span>')])
        assert '<span class="mono">0xabc</span>' in out


class TestLinksSection:
    def test_renders_links(self):
        out = ui_cards.links_section("Markets", [("DexScreener", "https://d.com")])
        assert "DexScreener" in out
        assert "https://d.com" in out

    def test_drops_blank_urls(self):
        out = ui_cards.links_section("M", [("A", ""), ("B", "https://b.com")])
        assert "B" in out
        assert ">A " not in out

    def test_all_blank_returns_empty(self):
        assert ui_cards.links_section("M", [("A", ""), ("B", "")]) == ""

    def test_empty_returns_empty(self):
        assert ui_cards.links_section("M", []) == ""

    def test_url_escaped(self):
        out = ui_cards.links_section("M", [("L", 'https://x.com/"onmouseover')])
        assert '"onmouseover' not in out.split("href=")[1][:40]


class TestPortfolioCard:
    def test_total_formatted(self):
        out = ui_cards.portfolio_card(
            address="0x" + "a" * 40, chain="Base", total_usd=1234.5, holdings=[]
        )
        assert "$1,234.50" in out
        assert "Portfolio" in out

    def test_total_none_renders_dash(self):
        out = ui_cards.portfolio_card(
            address="0x" + "a" * 40, chain="Base", total_usd=None, holdings=[]
        )
        assert "\u2014" in out

    def test_holding_with_usd(self):
        out = ui_cards.portfolio_card(
            address="0x" + "a" * 40,
            chain="Base",
            total_usd=None,
            holdings=[{"symbol": "WETH", "amount": "1.5", "usd": 5000.0}],
        )
        assert "WETH" in out
        assert "1.5" in out
        assert "$5,000.00" in out

    def test_holding_without_usd(self):
        out = ui_cards.portfolio_card(
            address="0x" + "a" * 40,
            chain="Base",
            total_usd=None,
            holdings=[{"symbol": "FOO", "amount": "10"}],
        )
        assert "FOO" in out
        assert "10" in out

    def test_address_truncated_when_long(self):
        addr = "0x" + "a" * 40
        out = ui_cards.portfolio_card(address=addr, chain="Base", total_usd=None, holdings=[])
        assert f"{addr[:6]}\u2026{addr[-4:]}" in out

    def test_short_address_not_truncated(self):
        out = ui_cards.portfolio_card(address="0xabc", chain="Base", total_usd=None, holdings=[])
        assert "0xabc" in out

    def test_holding_symbol_escaped(self):
        out = ui_cards.portfolio_card(
            address="0xabc",
            chain="Base",
            total_usd=None,
            holdings=[{"symbol": "<x>", "amount": "1"}],
        )
        assert "<x>" not in out


class TestResearchCard:
    def test_rows_and_links(self):
        out = ui_cards.research_card(
            symbol="MNEME",
            rows=[("Price", "$0.01"), ("Liquidity", "$5k")],
            links=[("DexScreener", "https://d.com")],
        )
        assert "MNEME" in out
        assert "Price" in out
        assert "$0.01" in out
        assert "DexScreener" in out

    def test_value_escaped(self):
        out = ui_cards.research_card(symbol="X", rows=[("k", "<b>v</b>")], links=[])
        assert "<b>v</b>" not in out


class TestReceiptCard:
    def test_rows_and_links(self):
        out = ui_cards.receipt_card(
            title="Swap submitted",
            rows=[("Sold", "0.1 ETH"), ("Bought", "300 USDC")],
            links=[("Explorer", "https://basescan.org/tx/0x")],
        )
        assert "Swap submitted" in out
        assert "0.1 ETH" in out
        assert "Explorer" in out


class TestConnectCard:
    def test_uri_present(self):
        out = ui_cards.connect_card(uri="wc:abc123@2?relay=x")
        assert "wc:abc123@2?relay=x" in out
        assert "Copy URI" in out

    def test_uri_escaped(self):
        out = ui_cards.connect_card(uri="wc:<script>@2")
        assert "wc:<script>@2" not in out


class TestWriteCard:
    def test_writes_file_and_returns_path(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        path = ui_cards.write_card("<html></html>", "Portfolio")
        assert isinstance(path, Path)
        assert path.exists()
        assert path.read_text(encoding="utf-8") == "<html></html>"
        assert path.parent == tmp_path / "clawmes" / "cards"

    def test_slugifies_name(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        path = ui_cards.write_card("x", "Swap Receipt!")
        assert path.name.startswith("swap-receipt-")
        assert path.suffix == ".html"

    def test_non_alnum_name_falls_back_to_card(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        path = ui_cards.write_card("x", "!!!")
        assert path.name.startswith("card-")

    def test_path_is_absolute(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        path = ui_cards.write_card("x", "p")
        assert path.is_absolute()
