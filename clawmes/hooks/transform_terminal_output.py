"""``transform_terminal_output`` hook — CLI display redaction.

Final scrub before text reaches the user's terminal. Catches anything
that slipped past ``transform_tool_result`` (which sees the upstream
JSON; this sees the rendered display string).

Symmetrical with the messaging path's ``privacy.redact_pii`` (Hermes
built-in).
"""

from __future__ import annotations

from typing import Any

from clawmes.lib.logger import logger_for
from clawmes.services.credential_redactor import scan_and_redact

_log = logger_for("hooks.transform_terminal_output")


def callback(text: str, **kwargs: Any) -> str:
    """Return ``text`` with sensitive substrings redacted.

    Errors during redaction degrade to pass-through so a regex bug in
    the redactor can't blank out the user's terminal.
    """
    if not text:
        return text
    try:
        return scan_and_redact(text, context="terminal_output")
    except Exception:
        _log.exception("transform_terminal_output: redactor raised; passing through")
        return text
