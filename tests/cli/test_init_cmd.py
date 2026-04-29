"""Tests for ``hermes clawmes init``."""

from __future__ import annotations

import argparse
from unittest.mock import patch

import pytest

from clawmes.cli import init as init_mod


def _ns(**kwargs):
    base = {
        "reconfigure": False,
        "skip_wallet": False,
        "check": False,
        "non_interactive": False,
    }
    base.update(kwargs)
    return argparse.Namespace(**base)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    return tmp_path


# --- helpers ------------------------------------------------------------


class TestReadEnv:
    def test_missing_file(self, tmp_path):
        result = init_mod._read_env(tmp_path / "nonexistent.env")
        assert result == {}

    def test_parses_lines(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text(
            "FOO=bar\n# a comment\n\nQUOTED=\"hello\"\nSINGLE='world'\nINVALID-NO-EQUALS\n"
        )
        out = init_mod._read_env(env)
        assert out == {"FOO": "bar", "QUOTED": "hello", "SINGLE": "world"}


class TestWriteEnv:
    def test_writes_and_chmods(self, tmp_path):
        path = tmp_path / ".env"
        init_mod._write_env(path, {"A": "1", "B": "2"})
        assert path.exists()
        assert oct(path.stat().st_mode)[-3:] == "600"
        text = path.read_text()
        # Sorted keys
        assert text == "A=1\nB=2\n"


class TestRedact:
    def test_short_value_fully_masked(self):
        assert init_mod._redact("abc") == "•••"

    def test_long_value_shows_last_4(self):
        out = init_mod._redact("supersecretvalue123")
        assert out.endswith("e123")
        assert out.startswith("•")


# --- non-interactive path -----------------------------------------------


class TestNonInteractive:
    def test_skip_mode(self, home, capsys, monkeypatch):
        monkeypatch.setenv("CLAWMES_INIT_WALLET_MODE", "skip")
        monkeypatch.delenv("CLAWMES_INIT_KEYS", raising=False)
        rc = init_mod.run(_ns(non_interactive=True))
        assert rc == 0
        assert "Nothing to set" in capsys.readouterr().out

    def test_walletconnect_mode(self, home, capsys, monkeypatch):
        monkeypatch.setenv("CLAWMES_INIT_WALLET_MODE", "walletconnect")
        monkeypatch.setenv("CLAWMES_INIT_WALLETCONNECT_PROJECT_ID", "proj-123")
        rc = init_mod.run(_ns(non_interactive=True))
        assert rc == 0
        env_text = (home / ".env").read_text()
        assert "WALLETCONNECT_PROJECT_ID=proj-123" in env_text

    def test_walletconnect_missing_pid_fails(self, home, capsys, monkeypatch):
        monkeypatch.setenv("CLAWMES_INIT_WALLET_MODE", "walletconnect")
        monkeypatch.delenv("CLAWMES_INIT_WALLETCONNECT_PROJECT_ID", raising=False)
        rc = init_mod.run(_ns(non_interactive=True))
        assert rc == 1
        assert "required" in capsys.readouterr().out

    def test_bankr_mode(self, home, capsys, monkeypatch):
        monkeypatch.setenv("CLAWMES_INIT_WALLET_MODE", "bankr")
        monkeypatch.setenv("CLAWMES_INIT_BANKR_API_KEY", "bankr-abc")
        rc = init_mod.run(_ns(non_interactive=True))
        assert rc == 0
        env_text = (home / ".env").read_text()
        assert "BANKR_API_KEY=bankr-abc" in env_text

    def test_bankr_missing_key_fails(self, home, capsys, monkeypatch):
        monkeypatch.setenv("CLAWMES_INIT_WALLET_MODE", "bankr")
        monkeypatch.delenv("CLAWMES_INIT_BANKR_API_KEY", raising=False)
        rc = init_mod.run(_ns(non_interactive=True))
        assert rc == 1

    def test_local_mode_skipped(self, home, capsys, monkeypatch):
        monkeypatch.setenv("CLAWMES_INIT_WALLET_MODE", "local")
        rc = init_mod.run(_ns(non_interactive=True))
        out = capsys.readouterr().out
        assert "interactive password" in out
        assert rc == 0  # still proceeds with optional keys

    def test_unknown_mode_fails(self, home, capsys, monkeypatch):
        monkeypatch.setenv("CLAWMES_INIT_WALLET_MODE", "zoltan")
        rc = init_mod.run(_ns(non_interactive=True))
        assert rc == 1
        assert "unknown" in capsys.readouterr().out.lower()

    def test_keys_pairs(self, home, capsys, monkeypatch):
        monkeypatch.setenv("CLAWMES_INIT_WALLET_MODE", "skip")
        monkeypatch.setenv(
            "CLAWMES_INIT_KEYS",
            "ZEROX_API_KEY=z1; LIFI_API_KEY=l1 ; INVALID; =empty",
        )
        rc = init_mod.run(_ns(non_interactive=True))
        assert rc == 0
        env_text = (home / ".env").read_text()
        assert "ZEROX_API_KEY=z1" in env_text
        assert "LIFI_API_KEY=l1" in env_text
        # The "INVALID" pair (no =) and " =empty" are skipped — but
        # " =empty" has the form "=empty" after split, so the empty key
        # gets stored as "". Just confirm valid pairs are there.

    def test_dry_run_does_not_write(self, home, capsys, monkeypatch):
        monkeypatch.setenv("CLAWMES_INIT_WALLET_MODE", "walletconnect")
        monkeypatch.setenv("CLAWMES_INIT_WALLETCONNECT_PROJECT_ID", "proj-123")
        rc = init_mod.run(_ns(non_interactive=True, check=True))
        assert rc == 0
        assert not (home / ".env").exists()
        out = capsys.readouterr().out
        assert "would set" in out

    def test_preserves_existing_keys(self, home, capsys, monkeypatch):
        # Existing .env file with unrelated keys
        env = home / ".env"
        env.write_text("OPENAI_API_KEY=sk-abc\nTELEGRAM_BOT_TOKEN=tg-xyz\n")
        env.chmod(0o600)
        monkeypatch.setenv("CLAWMES_INIT_WALLET_MODE", "walletconnect")
        monkeypatch.setenv("CLAWMES_INIT_WALLETCONNECT_PROJECT_ID", "proj-new")
        init_mod.run(_ns(non_interactive=True))
        text = env.read_text()
        # Existing values preserved
        assert "OPENAI_API_KEY=sk-abc" in text
        assert "TELEGRAM_BOT_TOKEN=tg-xyz" in text
        # New value added
        assert "WALLETCONNECT_PROJECT_ID=proj-new" in text


# --- interactive path ----------------------------------------------------


class TestInteractiveWalletMode:
    def test_walletconnect(self, home, capsys, monkeypatch):
        # Inputs: mode=walletconnect, then project ID, then 6 empty optional keys
        inputs = iter(["walletconnect", "proj-xyz", "", "", "", "", "", ""])
        monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))
        rc = init_mod.run(_ns())
        assert rc == 0
        assert (home / ".env").read_text().count("WALLETCONNECT_PROJECT_ID=proj-xyz") == 1

    def test_bankr_with_hidden_input(self, home, capsys, monkeypatch):
        inputs = iter(["bankr", "", "", "", "", "", ""])
        monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))
        monkeypatch.setattr("getpass.getpass", lambda _prompt="": "bankr-secret-key")
        rc = init_mod.run(_ns())
        assert rc == 0
        assert "BANKR_API_KEY=bankr-secret-key" in (home / ".env").read_text()

    def test_skip_mode_writes_only_optional(self, home, monkeypatch):
        inputs = iter(["skip", "z1", "", "", "", "", ""])
        monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))
        rc = init_mod.run(_ns())
        assert rc == 0
        text = (home / ".env").read_text()
        assert "ZEROX_API_KEY=z1" in text

    def test_unknown_choice_re_prompts(self, home, monkeypatch):
        # First answer is invalid, second is "skip"; then 6 empty optional keys
        inputs = iter(["zoltan", "skip", "", "", "", "", "", ""])
        monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))
        rc = init_mod.run(_ns())
        assert rc == 0

    def test_walletconnect_empty_pid_skips(self, home, monkeypatch):
        # empty project ID — should not write WALLETCONNECT_PROJECT_ID
        inputs = iter(["walletconnect", "", "", "", "", "", "", ""])
        monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))
        rc = init_mod.run(_ns())
        assert rc == 0
        env_path = home / ".env"
        if env_path.exists():
            assert "WALLETCONNECT_PROJECT_ID" not in env_path.read_text()

    def test_already_configured_no_reconfigure(self, home, capsys, monkeypatch):
        env = home / ".env"
        env.write_text("WALLETCONNECT_PROJECT_ID=proj-existing\n")
        env.chmod(0o600)
        # 6 optional keys — all skipped
        inputs = iter(["", "", "", "", "", ""])
        monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))
        init_mod.run(_ns())
        out = capsys.readouterr().out
        assert "already configured" in out

    def test_reconfigure_overrides(self, home, capsys, monkeypatch):
        env = home / ".env"
        env.write_text("WALLETCONNECT_PROJECT_ID=proj-old\n")
        env.chmod(0o600)
        inputs = iter(["walletconnect", "proj-new", "", "", "", "", "", ""])
        monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))
        init_mod.run(_ns(reconfigure=True))
        text = env.read_text()
        assert "WALLETCONNECT_PROJECT_ID=proj-new" in text

    def test_bankr_already_configured_hint(self, home, capsys, monkeypatch):
        env = home / ".env"
        env.write_text("BANKR_API_KEY=existing-key\n")
        env.chmod(0o600)
        # 6 optional inputs after the wallet step
        inputs = iter(["bankr", "", "", "", "", "", ""])
        monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))
        # Empty bankr key on input — should hit the `return {}` branch
        monkeypatch.setattr("getpass.getpass", lambda _prompt="": "")
        init_mod.run(_ns(reconfigure=True))
        out = capsys.readouterr().out
        # The "currently: bankr" hint comes from the elif branch
        assert "currently: bankr" in out
        # The redact-current line appears in bankr setup
        assert "Currently set to" in out

    def test_optional_key_already_set_skipped(self, home, monkeypatch):
        env = home / ".env"
        env.write_text("ZEROX_API_KEY=existing-zerox\n")
        env.chmod(0o600)
        # Skip wallet path; first 5 optional keys (ZEROX is now skipped from prompt)
        inputs = iter(["skip", "", "", "", "", ""])
        monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))
        rc = init_mod.run(_ns())
        assert rc == 0
        # ZEROX_API_KEY preserved unchanged
        assert "ZEROX_API_KEY=existing-zerox" in env.read_text()


class TestSkipWallet:
    def test_skip_wallet_only_asks_keys(self, home, monkeypatch, capsys):
        inputs = iter(["", "", "", "", "", ""])
        monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))
        init_mod.run(_ns(skip_wallet=True))
        out = capsys.readouterr().out
        assert "Skipping wallet setup" in out


class TestLocalKeyMode:
    def test_dry_run_skips_keystore(self, home, monkeypatch, capsys):
        inputs = iter(["local", "n", "", "", "", "", "", ""])  # 'n' = don't import
        monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))
        monkeypatch.setattr("getpass.getpass", lambda _prompt="": "supersecretpw")
        rc = init_mod.run(_ns(check=True))
        assert rc == 0
        out = capsys.readouterr().out
        assert "Would create encrypted keystore" in out

    def test_password_too_short_retries(self, home, monkeypatch, capsys):
        inputs = iter(["local", "n", "", "", "", "", "", ""])
        monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))
        # First 2 calls give a short password (then mismatched), then a good one
        passwords = iter(["short", "x", "longerpw1234", "longerpw1234"])
        monkeypatch.setattr("getpass.getpass", lambda _prompt="": next(passwords))
        rc = init_mod.run(_ns(check=True))
        assert rc == 0
        out = capsys.readouterr().out
        assert "at least 8" in out

    def test_password_mismatch_retries(self, home, monkeypatch):
        inputs = iter(["local", "n", "", "", "", "", "", ""])
        monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))
        passwords = iter(["password1", "password2", "password1", "password1"])
        monkeypatch.setattr("getpass.getpass", lambda _prompt="": next(passwords))
        rc = init_mod.run(_ns(check=True))
        assert rc == 0

    def test_real_keystore_creation(self, home, monkeypatch, capsys):
        # Drive through the real local-key path with a deterministic
        # mnemonic so we don't depend on system entropy.
        inputs = iter(
            [
                "local",
                "n",
                "",  # press Enter after viewing mnemonic
                "",
                "",
                "",
                "",
                "",
                "",
            ]
        )
        monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))
        monkeypatch.setattr("getpass.getpass", lambda _prompt="": "longerpw1234")

        # Patch the keystore primitives to avoid generating real mnemonics
        from clawmes.cli import init as init_mod_local

        with (
            patch.object(
                init_mod_local,
                "_setup_local",
                wraps=init_mod_local._setup_local,
            ),
        ):
            # Inject minimal stubs for the mnemonic / address / keystore funcs
            from clawmes.wallet import keystore as ks_mod

            monkeypatch.setattr(ks_mod, "generate_mnemonic", lambda **k: "test mnemonic")
            monkeypatch.setattr(
                ks_mod,
                "address_from_mnemonic",
                lambda m, **k: ("0x" + "a" * 40, "privkey"),
            )

            class _FakeKs:
                pass

            monkeypatch.setattr(ks_mod, "encrypt_mnemonic", lambda m, p, a: _FakeKs())
            monkeypatch.setattr(ks_mod, "save_keystore", lambda k: "file")

            rc = init_mod.run(_ns())
        assert rc == 0
        out = capsys.readouterr().out
        assert "0x" + "a" * 40 in out

    def test_import_existing_mnemonic(self, home, monkeypatch):
        inputs = iter(
            [
                "local",
                "y",  # import existing
                "abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon abandon about",
                "",
                "",
                "",
                "",
                "",
                "",
            ]
        )
        monkeypatch.setattr("builtins.input", lambda _prompt="": next(inputs))
        monkeypatch.setattr("getpass.getpass", lambda _prompt="": "longerpw1234")

        from clawmes.wallet import keystore as ks_mod

        captured = {}

        def fake_address(m, **k):
            captured["mnemonic"] = m
            return ("0x" + "b" * 40, "pk")

        monkeypatch.setattr(ks_mod, "address_from_mnemonic", fake_address)
        monkeypatch.setattr(ks_mod, "encrypt_mnemonic", lambda m, p, a: object())
        monkeypatch.setattr(ks_mod, "save_keystore", lambda k: "file")

        rc = init_mod.run(_ns())
        assert rc == 0
        assert captured["mnemonic"].startswith("abandon abandon")


class TestSummary:
    def test_persist_dry_run_no_write(self, home, capsys):
        env_path = home / ".env"
        rc = init_mod._persist_or_dry_run(
            env_path,
            existing={},
            new_values={"FOO": "bar"},
            dry_run=True,
        )
        assert rc == 0
        assert not env_path.exists()
        out = capsys.readouterr().out
        assert "would set" in out

    def test_persist_writes(self, home, capsys):
        env_path = home / ".env"
        rc = init_mod._persist_or_dry_run(
            env_path,
            existing={"PRESERVED": "yes"},
            new_values={"FOO": "bar"},
            dry_run=False,
        )
        assert rc == 0
        text = env_path.read_text()
        assert "FOO=bar" in text
        assert "PRESERVED=yes" in text
        assert "Wrote" in capsys.readouterr().out

    def test_no_values_returns_early(self, home, capsys):
        # Drive _run_interactive directly with all-skips
        ns = _ns(skip_wallet=True)
        # 6 empty optional keys
        with patch("builtins.input", side_effect=["", "", "", "", "", ""]):
            rc = init_mod.run(ns)
        out = capsys.readouterr().out
        assert "Nothing to write" in out
        assert rc == 0


class TestBanner:
    def test_banner_present(self, home, capsys, monkeypatch):
        monkeypatch.setenv("CLAWMES_INIT_WALLET_MODE", "skip")
        init_mod.run(_ns(non_interactive=True))
        out = capsys.readouterr().out
        assert "Welcome to clawmes" in out
