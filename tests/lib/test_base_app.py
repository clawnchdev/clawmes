"""Tests for clawmes.lib.base_app."""

from __future__ import annotations

from clawmes.lib import base_app


class TestTokenUrl:
    def test_default_template(self, monkeypatch):
        monkeypatch.delenv("CLAWMES_BASE_APP_TOKEN_URL", raising=False)
        url = base_app.token_url("0x" + "a" * 40)
        assert url == "https://base.app/?token=0x" + "a" * 40

    def test_empty_returns_empty(self, monkeypatch):
        monkeypatch.delenv("CLAWMES_BASE_APP_TOKEN_URL", raising=False)
        assert base_app.token_url("") == ""

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("CLAWMES_BASE_APP_TOKEN_URL", "https://staging.base.app/t/{address}")
        url = base_app.token_url("0xabc")
        assert url == "https://staging.base.app/t/0xabc"


class TestTxUrl:
    def test_default_template(self, monkeypatch):
        monkeypatch.delenv("CLAWMES_BASE_APP_TX_URL", raising=False)
        url = base_app.tx_url("0xtx")
        assert url == "https://base.app/?tx=0xtx"

    def test_empty_returns_empty(self, monkeypatch):
        monkeypatch.delenv("CLAWMES_BASE_APP_TX_URL", raising=False)
        assert base_app.tx_url("") == ""

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("CLAWMES_BASE_APP_TX_URL", "https://staging.base.app/tx/{tx_hash}")
        url = base_app.tx_url("0xabc")
        assert url == "https://staging.base.app/tx/0xabc"
