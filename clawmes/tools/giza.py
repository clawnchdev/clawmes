"""``giza`` — Giza zkML inference (read-only).

Giza is the canonical zkML platform — runs ML models with verifiable
zero-knowledge proofs. Three actions:

  * ``inference`` — run a model on user-supplied input. Returns the
    prediction + a proof handle.
  * ``models``    — list available models.
  * ``verify``    — verify a previously-issued proof.

Requires ``GIZA_API_KEY`` env var. Free tier supports limited models;
production uses subscription tier.
"""

from __future__ import annotations

import os
from typing import Any

from clawmes.lib.http import http_get, http_post
from clawmes.lib.logger import logger_for
from clawmes.lib.params import read_str
from clawmes.lib.tool_result import error_result, json_result
from clawmes.tools.registry import read_tool, register_with_ctx

_log = logger_for("tools.giza")

_GIZA_BASE = "https://api.gizatech.xyz"

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["inference", "models", "verify"],
        },
        "model_id": {"type": "string"},
        "input_data": {
            "type": "object",
            "description": "Model-specific input payload (inference).",
        },
        "proof_id": {"type": "string", "description": "For verify action."},
    },
    "required": ["action"],
}


@read_tool(
    name="giza",
    toolset="clawmes-defi",
    description=(
        "Giza zkML — verifiable ML inference. Run a model with a "
        "zero-knowledge proof of correct execution; list available "
        "models; verify proofs."
    ),
    schema=_SCHEMA,
    emoji="\U0001f9d0",
)
def giza(args: dict[str, Any], **kwargs: Any) -> str:
    api_key = os.environ.get("GIZA_API_KEY")
    if not api_key:
        return error_result(
            "GIZA_API_KEY required. Sign up at https://www.gizatech.xyz",
            code="no_credentials",
        )
    headers = {"Authorization": f"Bearer {api_key}"}

    action = read_str(args, "action", required=True)
    try:
        if action == "models":
            result = http_get(f"{_GIZA_BASE}/v1/models", headers=headers, timeout=15.0)
        elif action == "inference":
            model_id = read_str(args, "model_id", required=True)
            input_data = args.get("input_data")
            if not isinstance(input_data, dict):
                return error_result(
                    "inference requires input_data dict",
                    code="param_error",
                )
            result = http_post(
                f"{_GIZA_BASE}/v1/models/{model_id}/inference",
                json=input_data,
                headers=headers,
                timeout=60.0,  # zkML inference can be slow
            )
        else:
            proof_id = read_str(args, "proof_id", required=True)
            result = http_get(
                f"{_GIZA_BASE}/v1/proofs/{proof_id}/verify",
                headers=headers,
                timeout=30.0,
            )
    except Exception as exc:  # noqa: BLE001
        return error_result(f"Giza request failed: {exc}", code="api_error")

    return json_result(
        {"action": action, "result": result},
        summary=f"giza {action}",
    )


def register(ctx) -> None:
    register_with_ctx(ctx, giza)
