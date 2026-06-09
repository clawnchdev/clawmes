"""OpenAI-compatible LLM client for Venice AI.

Venice (``https://venice.ai``) is a privacy-first inference provider with an
OpenAI-compatible API at ``https://api.venice.ai/api/v1``. clawmes ships a
first-class client (alongside :mod:`clawmes.services.opengateway`) so tools that
need targeted inference outside the host Hermes agent loop — classifiers,
summarizers, structured-extraction helpers — can use Venice without each tool
wiring its own ``httpx`` client.

Scope of this service:

  * **Non-streaming chat completions only.** Streaming SSE is out of scope;
    clients that need streaming should use Hermes' own LLM client.
  * **Venice endpoint only.** The base URL is hardcoded, matching the
    convention used by the 0x / CoinGecko / LiFi / OpenGateway services.
  * **Independent from the host Hermes LLM.** This does *not* reroute the
    agent's main conversational inference — that's a Hermes-level concern
    (``ANTHROPIC_API_KEY`` / ``OPENAI_API_KEY`` etc.).

Authentication: ``VENICE_API_KEY`` env var (``Bearer`` token from the Venice
dashboard). **Required** — unlike OpenGateway, Venice does not serve free
unauthenticated traffic: a request without a key is answered with HTTP ``402``
(Venice's x402 pay-per-call challenge), surfaced here as
``VeniceError("no_credentials", …)``. The service still starts without the key
(logging a warning) so the rest of clawmes loads cleanly.

Default model: ``VENICE_MODEL`` env var; can be overridden per call via the
``model=`` keyword. If neither is set, :meth:`chat_completion` raises
``VeniceError("bad_request", …)``. The current catalog is public at
``GET https://api.venice.ai/api/v1/models`` (and https://docs.venice.ai/models/overview).
"""

from __future__ import annotations

import os
import threading
from typing import Any

from clawmes.lib.http import http_post
from clawmes.lib.logger import logger_for
from clawmes.services._base import Service

_log = logger_for("services.venice")

# Venice's canonical OpenAI-compatible endpoint.
_BASE_URL = "https://api.venice.ai/api/v1"


class VeniceError(RuntimeError):
    """Raised on Venice API failures.

    ``code`` classification (preferred sources in order: ``error.code`` /
    ``error.type`` in an OpenAI-style envelope, then HTTP status, then keyword
    match on the raised exception string):

      * ``bad_request`` — caller-side error: empty messages, no resolvable
        model, OpenAI ``invalid_request_error``, HTTP 400.
      * ``model_not_found`` — OpenAI ``unsupported_model`` / ``model_not_found``
        code, HTTP 404, or a message containing both "model" and "not found".
      * ``rate_limited`` — OpenAI ``rate_limit_exceeded`` / ``rate_limit_error``,
        HTTP 429.
      * ``no_credentials`` — HTTP 401 / 403, or HTTP 402 (Venice's x402
        "authentication required" / pay-per-call challenge), or OpenAI
        ``authentication_error`` / ``permission_denied``.
      * ``api_error`` — generic upstream failure.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class VeniceService(Service):
    id = "clawmes.venice"

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._api_key: str | None = None
        self._default_model: str | None = None

    def start(self) -> None:
        with self._lock:
            self._api_key = os.environ.get("VENICE_API_KEY") or None
            self._default_model = os.environ.get("VENICE_MODEL") or None
        if self._api_key:
            _log.info(
                "venice service started (auth=key, default_model=%s)",
                self._default_model or "<unset>",
            )
        else:
            _log.warning(
                "venice service started UNAUTHENTICATED (no VENICE_API_KEY); Venice "
                "requires a key — chat completions will fail with HTTP 402 until one "
                "is set (default_model=%s)",
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
                "status": "authenticated" if self._api_key else "unauthenticated",
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

        ``messages`` is the standard OpenAI ``[{role, content}, ...]`` list.
        ``model`` overrides the env-configured default. ``extra`` passes through
        to the upstream JSON body unchanged — useful for ``top_p``, ``stop``,
        ``response_format``, and Venice's own ``venice_parameters``.

        Returns the parsed OpenAI chat completion envelope. Streaming is
        explicitly disabled — pass ``stream=True`` and we raise ``bad_request``.
        """
        if not messages:
            raise VeniceError("bad_request", "messages list must be non-empty")
        if extra.get("stream"):
            raise VeniceError(
                "bad_request",
                "streaming is not supported by this service; use Hermes' LLM client",
            )

        with self._lock:
            api_key = self._api_key
            default_model = self._default_model

        resolved_model = model or default_model
        if not resolved_model:
            raise VeniceError(
                "bad_request",
                "no model specified and VENICE_MODEL env var is not set",
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
        api_key: str | None,
        timeout: float,
    ) -> dict[str, Any]:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        url = _BASE_URL + path
        try:
            response = http_post(url, json=body, headers=headers, timeout=timeout)
        except Exception as exc:  # noqa: BLE001 — classify below
            # ``clawmes.lib.http.http_post`` calls ``raise_for_status`` on
            # non-2xx, which discards the body. The original response is still
            # on ``exc.response``; recover the structured error envelope.
            body_dict: dict[str, Any] | None = None
            resp = getattr(exc, "response", None)
            if resp is not None:
                try:
                    parsed = resp.json()
                except Exception:  # noqa: BLE001 — body might not be JSON
                    parsed = None
                if isinstance(parsed, dict):
                    body_dict = parsed

            # OpenAI-style nested error envelope (Venice uses this for most
            # request errors, e.g. model issues).
            if body_dict is not None and isinstance(body_dict.get("error"), dict):
                raise self._classify_envelope(body_dict["error"]) from exc

            # Venice's auth/payment errors are a flat ``{"error": "..."}`` with
            # an HTTP 402 x402 challenge. Classify by status code in the
            # exception string (lib/http embeds it), with a keyword fallback.
            msg = str(exc).lower()
            flat = body_dict.get("error") if isinstance(body_dict, dict) else None
            detail = flat if isinstance(flat, str) and flat else str(exc)
            if "402" in msg or "401" in msg or "403" in msg or "authentication required" in msg:
                raise VeniceError("no_credentials", detail) from exc
            if "429" in msg or "rate limit" in msg:
                raise VeniceError("rate_limited", detail) from exc
            if "404" in msg or ("model" in msg and "not found" in msg):
                raise VeniceError("model_not_found", detail) from exc
            if "400" in msg:
                raise VeniceError("bad_request", detail) from exc
            raise VeniceError("api_error", f"venice request failed: {detail}") from exc

        if not isinstance(response, dict):
            raise VeniceError(
                "api_error",
                f"venice returned non-dict response: {type(response).__name__}",
            )
        # Defensive: some upstreams return 2xx with an error envelope in the body.
        if isinstance(response.get("error"), dict):
            raise self._classify_envelope(response["error"])
        return response

    @staticmethod
    def _classify_envelope(err: dict[str, Any]) -> VeniceError:
        """Classify an OpenAI-style error envelope into a VeniceError."""
        message = str(err.get("message") or "")
        err_type = str(err.get("type") or "").lower()
        err_code = str(err.get("code") or "").lower()
        display = f"venice error ({err_code or err_type or 'unknown'}): {message}"

        if err_code in {"unsupported_model", "model_not_found"}:
            return VeniceError("model_not_found", display)
        if err_code == "rate_limit_exceeded" or err_type == "rate_limit_error":
            return VeniceError("rate_limited", display)
        if err_type in {"authentication_error", "permission_denied"}:
            return VeniceError("no_credentials", display)
        if err_type == "invalid_request_error":
            msg_lower = message.lower()
            if "model" in msg_lower and ("not found" in msg_lower or "unsupported" in msg_lower):
                return VeniceError("model_not_found", display)
            return VeniceError("bad_request", display)
        return VeniceError("api_error", display)


_instance: VeniceService | None = None


def get_venice_service() -> VeniceService:
    global _instance
    if _instance is None:
        _instance = VeniceService()
    return _instance
