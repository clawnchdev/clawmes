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

API key: ``OPENGATEWAY_API_KEY`` env var, ``ogw_live_…`` format. The
gateway accepts unauthenticated traffic today ("auth optional during
the partnership window, required soon" per their docs), so the service
sends requests without an ``Authorization`` header when the env var is
missing and logs a warning at start so the future auth flip is not a
total surprise. Setting the key is strongly recommended in production
for attribution and rate-limit isolation.

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

    ``code`` classification (preferred sources in order: ``error.code``
    field in the upstream envelope, then ``error.type``, then keyword
    match on the upstream message, then keyword match on the raised
    exception string when no structured body is available):

      * ``bad_request`` — caller-side error: empty messages, no model
        resolvable from arg / env, OpenAI ``invalid_request_error``,
        HTTP 400.
      * ``model_not_found`` — OpenAI ``unsupported_model`` /
        ``model_not_found`` code, HTTP 404, or a message containing
        both "model" and "not found".
      * ``rate_limited`` — OpenAI ``rate_limit_exceeded`` /
        ``rate_limit_error``, HTTP 429.
      * ``no_credentials`` — OpenAI ``authentication_error`` /
        ``permission_denied``, HTTP 401 / 403. Raised when the gateway
        refuses the call for auth reasons; *not* raised pre-flight
        when ``OPENGATEWAY_API_KEY`` is absent (the gateway accepts
        unauth'd traffic during the partnership window).
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
        if self._api_key:
            _log.info(
                "opengateway service started (auth=key, default_model=%s)",
                self._default_model or "<unset>",
            )
        else:
            # Unauthenticated traffic is currently accepted by the gateway,
            # so we don't refuse to start — but we warn so the future
            # auth-required flip is not a total surprise.
            _log.warning(
                "opengateway service started UNAUTHENTICATED (no OPENGATEWAY_API_KEY); "
                "calls will succeed during the gitlawb partnership window but stop "
                "working when auth becomes required (default_model=%s)",
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

        ``messages`` is the standard OpenAI ``[{role, content}, ...]``
        list. ``model`` overrides the env-configured default. ``extra``
        is passed through to the upstream JSON body unchanged — useful
        for ``top_p``, ``stop``, ``response_format``, etc.

        Returns the parsed JSON response (the OpenAI chat completion
        envelope: ``choices[*].message.content``, ``usage``, etc.).
        Streaming is explicitly disabled — pass ``stream=True`` and we
        raise ``bad_request``.

        Calls without ``OPENGATEWAY_API_KEY`` are sent unauthenticated
        (the gateway allows this during the partnership window). The
        upstream will start returning ``401`` once auth is required —
        that surfaces here as ``OpenGatewayError("no_credentials", …)``.
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

    def chat_completion_premium(
        self,
        messages: list[dict[str, Any]],
        **kwargs: Any,
    ) -> dict[str, Any]:
        """High-tier chat completion — gated on Clawnch premium.

        Identical contract to :meth:`chat_completion` but adds the
        Clawnch premium gate (``opengateway_high_tier`` feature). Free
        tier callers see a structured ``premium_required`` response.

        Why a separate method instead of a flag on ``chat_completion``:
        keeps the free path completely free of premium-gate cost —
        no service round-trip when a free-tier tool uses the gateway
        for normal inference. Premium callers opt in explicitly.

        Returns the same OpenAI chat-completion envelope on grant. On
        denial, returns the gate denial dict (with ``isError``,
        ``content``, ``details``) — tools should treat it as an error
        path, not a normal response.
        """
        from clawmes.lib.premium import gate

        denial = gate("opengateway_high_tier", tool_shape=False)
        if denial is not None:
            return {
                "isError": True,
                "content": [{"type": "text", "text": denial}],
                "details": {"premium_required": True, "feature": "opengateway_high_tier"},
            }
        return self.chat_completion(messages, **kwargs)

    def _call(
        self,
        path: str,
        body: dict[str, Any],
        *,
        api_key: str | None,
        timeout: float,
    ) -> dict[str, Any]:
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            # Upstream bug workaround (verified via live probe, 2026-05):
            # opengateway advertises ``content-encoding: gzip`` on 2xx
            # responses but the body fails zlib decompression with
            # "Error -3: incorrect header check". curl works because
            # curl tolerates the malformed gzip stream; httpx does not.
            # Asking for identity encoding bypasses the broken path.
            # Drop this override once gitlawb fixes the gateway's
            # compression layer.
            "Accept-Encoding": "identity",
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        url = _BASE_URL + path
        try:
            response = http_post(url, json=body, headers=headers, timeout=timeout)
        except Exception as exc:  # noqa: BLE001 — classify below
            # ``clawmes.lib.http.http_post`` wraps httpx and calls
            # ``raise_for_status`` on non-2xx, which discards the
            # response body. The exception object still carries the
            # original response on its ``.response`` attribute, so we
            # duck-type past lib/http to recover the structured error
            # envelope without importing httpx here.
            body_dict: dict[str, Any] | None = None
            resp = getattr(exc, "response", None)
            if resp is not None:
                try:
                    parsed = resp.json()
                except Exception:  # noqa: BLE001 — body might not be JSON
                    parsed = None
                if isinstance(parsed, dict):
                    body_dict = parsed

            if body_dict is not None and isinstance(body_dict.get("error"), dict):
                raise self._classify_envelope(body_dict["error"]) from exc

            # No structured body — fall back to substring matching on
            # the raised exception (transport errors, non-JSON 5xx, etc).
            msg = str(exc).lower()
            if "429" in msg or "rate limit" in msg:
                raise OpenGatewayError("rate_limited", str(exc)) from exc
            if "401" in msg or "403" in msg:
                raise OpenGatewayError("no_credentials", str(exc)) from exc
            if "404" in msg or ("model" in msg and "not found" in msg):
                raise OpenGatewayError("model_not_found", str(exc)) from exc
            if "400" in msg:
                raise OpenGatewayError("bad_request", str(exc)) from exc
            raise OpenGatewayError("api_error", f"opengateway request failed: {exc}") from exc

        if not isinstance(response, dict):
            raise OpenGatewayError(
                "api_error",
                f"opengateway returned non-dict response: {type(response).__name__}",
            )
        # Defensive: some buggy upstreams return 2xx with an error
        # envelope in the body. clawmes.lib.http won't have raised in
        # that case, so classify here.
        if isinstance(response.get("error"), dict):
            raise self._classify_envelope(response["error"])
        return response

    @staticmethod
    def _classify_envelope(err: dict[str, Any]) -> OpenGatewayError:
        """Classify an OpenAI-style error envelope into an OpenGatewayError.

        Inspects ``error.code`` first (specific), then ``error.type``
        (still structured), then keyword-matches ``error.message`` as
        a last-ditch (some upstreams omit the structured fields).
        """
        message = str(err.get("message") or "")
        err_type = str(err.get("type") or "").lower()
        err_code = str(err.get("code") or "").lower()
        display = f"opengateway error ({err_code or err_type or 'unknown'}): {message}"

        if err_code in {"unsupported_model", "model_not_found"}:
            return OpenGatewayError("model_not_found", display)
        if err_code == "rate_limit_exceeded" or err_type == "rate_limit_error":
            return OpenGatewayError("rate_limited", display)
        if err_type in {"authentication_error", "permission_denied"}:
            return OpenGatewayError("no_credentials", display)
        if err_type == "invalid_request_error":
            # Distinguish model-not-found that came through as
            # invalid_request_error (per real OpenGateway behavior:
            # ``code=unsupported_model`` carries ``type=invalid_request_error``).
            # The code check above already caught that; if we got here,
            # it's a true bad request.
            msg_lower = message.lower()
            if "model" in msg_lower and ("not found" in msg_lower or "unsupported" in msg_lower):
                return OpenGatewayError("model_not_found", display)
            return OpenGatewayError("bad_request", display)
        return OpenGatewayError("api_error", display)


_instance: OpenGatewayService | None = None


def get_opengateway_service() -> OpenGatewayService:
    global _instance
    if _instance is None:
        _instance = OpenGatewayService()
    return _instance
