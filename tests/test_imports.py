"""Smoke test — every package and subpackage imports cleanly.

If a circular import or syntax error sneaks in, this fails before the
plugin manager ever sees it.
"""

from __future__ import annotations


def test_root_import() -> None:
    import clawmes  # noqa: F401

    assert clawmes.__version__


def test_subpackage_imports() -> None:
    # Every public subpackage / module that ships with v0.1.0
    import clawmes.bridges  # noqa: F401
    import clawmes.cli  # noqa: F401
    import clawmes.commands  # noqa: F401
    import clawmes.hooks  # noqa: F401
    import clawmes.ledger  # noqa: F401
    import clawmes.lib  # noqa: F401
    import clawmes.lib.addr  # noqa: F401
    import clawmes.lib.chains  # noqa: F401
    import clawmes.lib.decimals  # noqa: F401
    import clawmes.lib.http  # noqa: F401
    import clawmes.lib.params  # noqa: F401
    import clawmes.lib.paths  # noqa: F401
    import clawmes.lib.time  # noqa: F401
    import clawmes.lib.tool_result  # noqa: F401
    import clawmes.onboarding  # noqa: F401
    import clawmes.plans  # noqa: F401
    import clawmes.policy  # noqa: F401
    import clawmes.services  # noqa: F401
    import clawmes.skills  # noqa: F401
    import clawmes.tools  # noqa: F401
    import clawmes.tools.transfer  # noqa: F401
    import clawmes.wallet  # noqa: F401
