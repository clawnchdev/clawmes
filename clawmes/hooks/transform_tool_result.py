"""``transform_tool_result`` hook — credential redaction at source.

Scrubs API keys, mnemonic phrases, private keys, WC pairing URIs, and
Bankr session tokens from tool results before either the LLM or the
display layer sees them.

Patterns:
  * BIP-39 word-list mnemonic (12 / 15 / 18 / 24 words)
  * Hex private key (``0x[0-9a-fA-F]{64}`` with key-context heuristic)
  * API key prefixes: ``sk-ant-`` / ``sk-or-`` / ``sk-`` / ``xoxb-`` /
    ``gh[pso]_``
  * WC v2 pairing URI: ``wc:...@2...``
  * Bankr session token (``bankr_sess_…``)

False positives (e.g. a 64-hex tx hash misread as a private key) are
rejected by the surrounding-text keyword heuristic in
``services.credential_redactor``.
"""

from __future__ import annotations

from typing import Any

from clawmes.lib.logger import logger_for
from clawmes.services.credential_redactor import scan_and_redact

_log = logger_for("hooks.transform_tool_result")


def callback(result: str, **kwargs: Any) -> str:
    """Return ``result`` with credentials redacted.

    Errors during redaction degrade to pass-through — the agent loop
    must never crash because the redactor had a regex bug.
    """
    if not result:
        return result
    try:
        return scan_and_redact(result, context="tool_result")
    except Exception:
        _log.exception("transform_tool_result: redactor raised; passing through")
        return result
