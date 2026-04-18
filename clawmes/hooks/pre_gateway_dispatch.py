"""``pre_gateway_dispatch`` hook — intercept inbound messages before the agent loop.

Returns one of:

  * ``{"action": "skip"}`` — drop the message entirely (e.g. onboarding
    intercept that handles the first user message itself)
  * ``{"action": "rewrite", "text": "..."}`` — change the inbound text
    before the agent sees it
  * ``{"action": "allow"}`` or ``None`` — pass through unchanged

Used for:

  1. **Onboarding interception** — the first message from a brand-new
     user triggers the welcome flow rather than going to the LLM.
  2. **Slash-prefix normalization** — some channels strip leading ``/``;
     normalize before dispatch so command parsing works uniformly.
  3. **Channel-specific text fixups** — Telegram occasionally
     double-encodes emoji; correct here.

Internal events (background-process completions) skip this hook entirely
in Hermes core.
"""

from __future__ import annotations

from typing import Any

from clawmes.lib.logger import logger_for

_log = logger_for("hooks.pre_gateway_dispatch")


def callback(
    *,
    event: dict[str, Any] | None = None,
    gateway: Any = None,
    session_store: Any = None,
    **kwargs: Any,
) -> dict[str, str] | None:
    """Inbound message interceptor."""
    if event is None:
        return None
    # TODO(v0.1.0): onboarding interception
    # TODO(v0.1.0): slash-prefix normalization
    # TODO(v0.1.0): channel-specific text fixups
    _log.debug(
        "pre_gateway_dispatch: from=%s text_len=%d",
        event.get("from"),
        len(event.get("content") or ""),
    )
    return None
