"""``transform_tool_result`` hook — credential redaction at source.

Scrubs API keys, mnemonic phrases, private keys, WC pairing URIs, and
Bankr session tokens from tool results before either the LLM or the
display layer sees them.

Patterns:
  * BIP-39 word-list mnemonic (12 / 15 / 18 / 24 words)
  * Hex private key (``0x[0-9a-fA-F]{64}``)
  * API key prefixes: ``sk-ant-`` / ``sk-or-`` / ``sk-`` / ``xoxb-`` /
    ``ghp_``
  * WC v2 pairing URI: ``wc:...@2...``
  * Bankr session token (custom prefix)

False positives (e.g. a 64-hex tx hash misread as a private key) are
rejected by checksum / context heuristics in
``services.credential_redactor``.
"""

from __future__ import annotations

from typing import Any

from clawmes.lib.logger import logger_for

_log = logger_for("hooks.transform_tool_result")


def callback(result: str, **kwargs: Any) -> str:
    """Return ``result`` with credentials redacted.

    Stub — passes through. The real implementation delegates to
    ``services.credential_redactor.scan_and_redact``.
    """
    return result
