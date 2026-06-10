"""Unified LLM inference router for clawmes tools.

Tools that need targeted inference outside the host Hermes agent loop (the
``/research`` narrative summary, the ``/agent --ai`` intent extractor, etc.)
call :func:`chat_completion` here instead of binding to one provider. The
backend is selected at call time:

  1. ``CLAWMES_LLM_PROVIDER`` env — ``"venice"`` or ``"opengateway"`` (explicit).
  2. Otherwise auto: **Venice** when ``VENICE_API_KEY`` is set, else OpenGateway.

Both providers expose the same OpenAI-compatible ``chat_completion`` signature
(see :mod:`clawmes.services.venice` / :mod:`clawmes.services.opengateway`);
their per-provider error types are normalized to :class:`InferenceError`.

This router is independent from Hermes' own conversational LLM — it's opt-in,
per-call inference for clawmes' own tools.
"""

from __future__ import annotations

import os
from typing import Any

__all__ = ["InferenceError", "chat_completion", "resolve_provider"]


class InferenceError(RuntimeError):
    """Provider-agnostic inference failure.

    ``code`` mirrors the underlying provider's classification (``bad_request``,
    ``model_not_found``, ``rate_limited``, ``no_credentials``,
    ``payment_required``, ``api_error``). ``provider`` names the backend that
    raised it.
    """

    def __init__(self, code: str, message: str, *, provider: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.provider = provider


def resolve_provider() -> str:
    """Return the active provider name: ``"venice"`` or ``"opengateway"``."""
    choice = (os.environ.get("CLAWMES_LLM_PROVIDER") or "").strip().lower()
    if choice in ("venice", "opengateway"):
        return choice
    if os.environ.get("VENICE_API_KEY"):
        return "venice"
    return "opengateway"


def chat_completion(
    messages: list[dict[str, Any]],
    *,
    model: str | None = None,
    **kw: Any,
) -> dict[str, Any]:
    """Route a non-streaming chat completion to the configured provider.

    ``model`` defaults to the provider's own env-configured default
    (``VENICE_MODEL`` / ``OPENGATEWAY_MODEL``). Provider failures are raised as
    :class:`InferenceError`; non-provider exceptions (import / transport) are
    left to propagate so callers' broad handlers see them unchanged.
    """
    provider = resolve_provider()
    if provider == "venice":
        from clawmes.services.venice import VeniceError, get_venice_service

        svc = get_venice_service()
        err_type: type[Exception] = VeniceError
    else:
        from clawmes.services.opengateway import (
            OpenGatewayError,
            get_opengateway_service,
        )

        svc = get_opengateway_service()
        err_type = OpenGatewayError

    try:
        return svc.chat_completion(messages, model=model, **kw)
    except err_type as exc:
        raise InferenceError(
            getattr(exc, "code", "api_error"),
            str(getattr(exc, "message", exc)),
            provider=provider,
        ) from exc
