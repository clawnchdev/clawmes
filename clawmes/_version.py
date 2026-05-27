"""Single-source-of-truth version string.

Read by:
  * ``clawmes/__init__.py`` — exposed as ``clawmes.__version__``
  * ``clawmes/cli/version.py`` — emitted by ``hermes clawmes version``
  * ``hermes clawmes doctor`` — reported in diagnostics output
  * Tooling that does not want to incur a full package import
"""

__version__ = "0.6.0"
