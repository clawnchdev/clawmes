"""Leaf utility modules used across clawmes.

Convention: nothing in ``clawmes/lib/`` may import from ``clawmes.tools``,
``clawmes.commands``, ``clawmes.services``, or ``clawmes.hooks``. ``lib`` is
the bottom of the dependency graph — pure helpers, no side effects.
"""
