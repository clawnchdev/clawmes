"""Tests for the ``hermes clawmes`` argparse setup + dispatcher."""

from __future__ import annotations

import argparse

import pytest

from clawmes.cli import _argparse


@pytest.fixture
def parser():
    p = argparse.ArgumentParser(prog="clawmes")
    _argparse.setup(p)
    return p


# Setup --------------------------------------------------------------------


class TestSetup:
    def test_init_subcommand(self, parser):
        ns = parser.parse_args(["init"])
        assert ns.clawmes_command == "init"
        assert ns.reconfigure is False
        assert ns.skip_wallet is False
        assert ns.check is False
        assert ns.non_interactive is False

    def test_init_with_flags(self, parser):
        ns = parser.parse_args(
            ["init", "--reconfigure", "--skip-wallet", "--check", "--non-interactive"]
        )
        assert ns.reconfigure is True
        assert ns.skip_wallet is True
        assert ns.check is True
        assert ns.non_interactive is True

    def test_doctor_subcommand(self, parser):
        ns = parser.parse_args(["doctor"])
        assert ns.clawmes_command == "doctor"
        assert ns.fix is False

    def test_doctor_with_fix(self, parser):
        ns = parser.parse_args(["doctor", "--fix"])
        assert ns.fix is True

    def test_version_subcommand(self, parser):
        ns = parser.parse_args(["version"])
        assert ns.clawmes_command == "version"

    def test_status_subcommand(self, parser):
        ns = parser.parse_args(["status"])
        assert ns.clawmes_command == "status"
        assert ns.json is False

    def test_status_json(self, parser):
        ns = parser.parse_args(["status", "--json"])
        assert ns.json is True

    def test_wallet_subcommand(self, parser):
        ns = parser.parse_args(["wallet"])
        assert ns.clawmes_command == "wallet"
        assert ns.action is None
        assert ns.mode is None

    def test_wallet_with_action_and_mode(self, parser):
        ns = parser.parse_args(["wallet", "switch", "bankr"])
        assert ns.action == "switch"
        assert ns.mode == "bankr"

    def test_plans_subcommand(self, parser):
        ns = parser.parse_args(["plans"])
        assert ns.clawmes_command == "plans"
        assert ns.action == "list"

    def test_plans_show(self, parser):
        ns = parser.parse_args(["plans", "show", "p-123"])
        assert ns.action == "show"
        assert ns.plan_id == "p-123"

    def test_policy_subcommand(self, parser):
        ns = parser.parse_args(["policy"])
        assert ns.action == "list"

    def test_policy_set(self, parser):
        ns = parser.parse_args(["policy", "set", "approve under 0.05 ETH"])
        assert ns.action == "set"
        assert ns.rules == "approve under 0.05 ETH"

    def test_persona_show(self, parser):
        ns = parser.parse_args(["persona", "show"])
        assert ns.action == "show"

    def test_persona_reinstall(self, parser):
        ns = parser.parse_args(["persona", "reinstall"])
        assert ns.action == "reinstall"

    def test_skills_install_multiple(self, parser):
        ns = parser.parse_args(["skills", "install", "transfer", "lending"])
        assert ns.action == "install"
        assert ns.skill == ["transfer", "lending"]

    def test_skills_list_no_skills(self, parser):
        ns = parser.parse_args(["skills", "list"])
        assert ns.action == "list"
        assert ns.skill == []

    def test_logs_choice(self, parser):
        ns = parser.parse_args(["logs", "wc"])
        assert ns.bridge == "wc"
        assert ns.follow is False

    def test_logs_with_follow(self, parser):
        ns = parser.parse_args(["logs", "sa", "-f"])
        assert ns.follow is True

    def test_update_subcommand(self, parser):
        ns = parser.parse_args(["update"])
        assert ns.clawmes_command == "update"

    def test_uninstall_subcommand(self, parser):
        ns = parser.parse_args(["uninstall"])
        assert ns.purge is False

    def test_uninstall_purge(self, parser):
        ns = parser.parse_args(["uninstall", "--purge"])
        assert ns.purge is True


# Handle dispatcher --------------------------------------------------------


class TestHandle:
    def test_no_subcommand_prints_usage(self, capsys):
        ns = argparse.Namespace(clawmes_command=None)
        rc = _argparse.handle(ns)
        assert rc == 2
        err = capsys.readouterr().err
        assert "Usage:" in err

    def test_init_dispatches(self, monkeypatch, capsys):
        called = []

        def fake_run(args):
            called.append(args)
            return 0

        from clawmes.cli import init as init_mod

        monkeypatch.setattr(init_mod, "run", fake_run)
        ns = argparse.Namespace(clawmes_command="init")
        rc = _argparse.handle(ns)
        assert rc == 0
        assert called == [ns]

    def test_doctor_dispatches(self, monkeypatch):
        called = []
        from clawmes.cli import doctor as doctor_mod

        monkeypatch.setattr(doctor_mod, "run", lambda a: called.append(a) or 0)
        ns = argparse.Namespace(clawmes_command="doctor")
        rc = _argparse.handle(ns)
        assert rc == 0
        assert called

    def test_version_dispatches(self, monkeypatch):
        called = []
        from clawmes.cli import version as version_mod

        monkeypatch.setattr(version_mod, "run", lambda a: called.append(a) or 0)
        ns = argparse.Namespace(clawmes_command="version")
        rc = _argparse.handle(ns)
        assert rc == 0
        assert called

    def test_status_dispatches(self, monkeypatch):
        called = []
        from clawmes.cli import doctor as doctor_mod

        monkeypatch.setattr(doctor_mod, "run_status", lambda a: called.append(a) or 0)
        ns = argparse.Namespace(clawmes_command="status")
        rc = _argparse.handle(ns)
        assert rc == 0
        assert called

    def test_unknown_subcommand_returns_1(self, capsys):
        ns = argparse.Namespace(clawmes_command="some-unimplemented")
        rc = _argparse.handle(ns)
        assert rc == 1
        out = capsys.readouterr().out
        assert "scaffolded but not yet implemented" in out

    def test_keyboard_interrupt_returns_130(self, monkeypatch, capsys):
        from clawmes.cli import init as init_mod

        def boom(args):
            raise KeyboardInterrupt()

        monkeypatch.setattr(init_mod, "run", boom)
        ns = argparse.Namespace(clawmes_command="init")
        rc = _argparse.handle(ns)
        assert rc == 130
        assert "Interrupted" in capsys.readouterr().out

    def test_other_exception_returns_1(self, monkeypatch, capsys):
        from clawmes.cli import init as init_mod

        def boom(args):
            raise RuntimeError("simulated")

        monkeypatch.setattr(init_mod, "run", boom)
        ns = argparse.Namespace(clawmes_command="init")
        rc = _argparse.handle(ns)
        assert rc == 1
        assert "Error: simulated" in capsys.readouterr().err
