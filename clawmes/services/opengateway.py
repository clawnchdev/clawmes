"""OpenAI-compatible LLM client for the gitlawb OpenGateway.

OpenGateway (``https://opengateway.gitlawb.com``) is an OpenAI-compatible
inference gateway that routes a single endpoint across multiple model
providers (Xiaomi MiMo, GMI Cloud, etc.) under server-side credentials.
Per the gitlawb partnership, clawmes ships a first-class client so tools
that need targeted inference outside the host Hermes agent loop (e.g.
classifiers, summarizers, structured-extraction helpers) can use it
without each tool wiring its own ``httpx`` client.

Scope of this service:

  * **Non-streaming chat completions only.** Streaming Server-Sent
    Events are out of scope here; clients that need streaming should
    use Hermes' own LLM client, which already handles SSE properly.
  * **OpenGateway endpoint only.** The base URL is hardcoded, matching
    the convention used by the 0x / CoinGecko / LiFi services. Users
    pointing at a different OpenAI-compatible endpoint is out of scope
    for this service; it would defeat the purpose of a branded
    partnership integration.
  * **Independent from the host Hermes LLM.** This service does *not*
    reroute the agent's main conversational inference — that's owned
    by Hermes itself via ``ANTHROPIC_API_KEY`` / ``OPENAI_API_KEY``
    etc. (see ``README.md`` config section). Adding OpenGateway as
    Hermes' main provider is a Hermes-level concern.

API key: ``OPENGATEWAY_API_KEY`` env var, ``ogw_live_…`` format.
Default model: ``OPENGATEWAY_MODEL`` env var; can be overridden per
call via the ``model=`` keyword. If neither is set, ``chat_completion``
raises ``OpenGatewayError("bad_request", ...)``.
"""

from __future__ import annotations

import os
import threading
from typing import Any

from clawmes.lib.http import http_post
from clawmes.lib.logger import logger_for
from clawmes.services._base import Service

_log = logger_for("services.opengateway")

# OpenGateway's canonical OpenAI-compatible endpoint. The provider's
# docs (https://gitlawb.com/opengateway) say: "Point any OpenAI-
# compatible client at opengateway.gitlawb.com/v1 and pass the model."
_BASE_URL = "https://opengateway.gitlawb.com/v1"


class OpenGatewayError(RuntimeError):
    """Raised on OpenGateway API failures.

    ``code`` classification:
      * ``no_credentials`` — OPENGATEWAY_API_KEY is not set. The
        gateway's docs note that anonymous traffic is allowed during
        the partnership window but will be removed; we refuse to send
        unauthenticated traffic so users notice the configuration gap
        before the auth wall lands.
      * ``bad_request`` — caller-side error (empty messages list,
        no model resolvable from arg / env, HTTP 400 from upstream).
      * ``model_not_found`` — HTTP 404 / "model not found" envelope.
      * ``rate_limited`` — HTTP 429.
      * ``api_error`` — generic upstream failure.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class OpenGatewayService(Service):
    id = "clawmes.opengateway"

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._api_key: str | None = None
        self._default_model: str | None = None

    def start(self) -> None:
        with self._lock:
            self._api_key = os.environ.get("OPENGATEWAY_API_KEY") or None
            self._default_model = os.environ.get("OPENGATEWAY_MODEL") or None
        _log.info(
            "opengateway service started (auth=%s, default_model=%s)",
            "key" if self._api_key else "missing",
            self._default_model or "<unset>",
        )

    def stop(self) -> None:
        with self._lock:
            self._api_key = None
            self._default_model = None

    def health(self) -> dict[str, Any]:
        with self._lock:
            return {
                "id": self.id,
                "status": "configured" if self._api_key else "missing_key",
                "default_model": self._default_model,
            }

    def chat_completion(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout: float = 60.0,
        **extra: Any,
    ) -> dict[str, Any]:
        """Send a non-streaming chat completion request.

        ``messages`` is the standard OpenAI ``[{role, content}, ...]``
        list. ``model`` overrides the env-configured default. ``extra``
        is passed through to the upstream JSON body unchanged — useful
        for ``top_p``, ``stop``, ``response_format``, etc.

        Returns the parsed JSON response (the OpenAI chat completion
        envelope: ``choices[*].message.content``, ``usage``, etc.).
        Streaming is explicitly disabled — pass ``stream=True`` and we
        raise ``bad_request``.
        """
        if not messages:
            raise OpenGatewayError("bad_request", "messages list must be non-empty")
        if extra.get("stream"):
            raise OpenGatewayError(
                "bad_request",
                "streaming is not supported by this service; use Hermes' LLM client",
            )

        with self._lock:
            api_key = self._api_key
            default_model = self._default_model

        if not api_key:
            raise OpenGatewayError(
                "no_credentials",
                "OPENGATEWAY_API_KEY is not set; cannot send authenticated traffic",
            )

        resolved_model = model or default_model
        if not resolved_model:
            raise OpenGatewayError(
                "bad_request",
                "no model specified and OPENGATEWAY_MODEL env var is not set",
            )

        body: dict[str, Any] = {"model": resolved_model, "messages": messages}
        if temperature is not None:
            body["temperature"] = temperature
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        body.update(extra)

        return self._call("/chat/completions", body, api_key=api_key, timeout=timeout)

    def _call(
        self,
        path: str,
        body: dict[str, Any],
        *,
        api_key: str,
        timeout: float,
    ) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        url = _BASE_URL + path
        try:
            response = http_post(url, json=body, headers=headers, timeout=timeout)
        except Exception as exc:  # noqa: BLE001 — classify below
            msg = str(exc).lower()
            if "429" in msg or "rate" in msg:
                raise OpenGatewayError("rate_limited", str(exc)) from exc
            if "404" in msg or "model" in msg and "not found" in msg:
                raise OpenGatewayError("model_not_found", str(exc)) from exc
            if "400" in msg:
                raise OpenGatewayError("bad_request", str(exc)) from exc
            raise OpenGatewayError("api_error", f"opengateway request failed: {exc}") from exc

        if not isinstance(response, dict):
            raise OpenGatewayError(
                "api_error",
                f"opengateway returned non-dict response: {type(response).__name__}",
            )
        # OpenAI-compatible error envelope: {"error": {"message", "type", "code"}}
        if isinstance(response.get("error"), dict):
            err = response["error"]
            text = str(err.get("message") or "")
            text_lower = text.lower()
            err_type = str(err.get("type") or "").lower()
            if "rate" in text_lower or err_type == "rate_limit_error":
                code = "rate_limited"
            elif "model" in text_lower and "not found" in text_lower:
                code = "model_not_found"
            elif err_type == "invalid_request_error":
                code = "bad_request"
            else:
                code = "api_error"
            raise OpenGatewayError(code, f"opengateway error: {text}")
        return response


_instance: OpenGatewayService | None = None


def get_opengateway_service() -> OpenGatewayService:
    global _instance
    if _instance is None:
        _instance = OpenGatewayService()
    return _instance
