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

_log = logger_for("hooks.transform_terminal_output")


def callback(text: str, **kwargs: Any) -> str:
    """Return ``text`` with sensitive substrings redacted.

    Stub at this milestone — passes through. The real implementation
    delegates to ``services.credential_redactor.scan_and_redact``.
    """
    return text
