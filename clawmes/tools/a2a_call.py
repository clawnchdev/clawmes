"""``a2a_call`` — Agent-to-Agent JSON-RPC 2.0 client.

The A2A protocol (https://google.github.io/A2A/) lets agents send
structured tasks to each other over JSON-RPC 2.0. Skills are
advertised via an AgentCard at ``/.well-known/agent-card.json`` and
tasks are sent to a peer's ``/tasks/send`` endpoint.

Two actions:

  * ``discover`` — fetch the peer's AgentCard and surface its skill
    list + version. Pure read.
  * ``send_task`` — send a JSON-RPC 2.0 task to the peer. Returns the
    response payload.

Authentication is currently OUT OF SCOPE. v1 only supports
unauthenticated A2A calls (most agent peers, including BV-7X's public
endpoints, don't require auth for read tasks). Authenticated A2A —
signing requests with the agent's DID per RFC 9421 HTTP Signatures —
pairs with the IdentityService (PR #10) and is sketched as a
follow-up.

Network allowlist: the target peer's host MUST be in
``clawmes.lib.http._DEFAULT_ALLOWLIST`` or the runtime allowlist
(added via ``/allow <host>``). This is the existing prompt-injection
defense — an LLM that gets tricked into hitting an attacker-controlled
A2A endpoint is blocked at the HTTP layer.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from clawmes.lib.http import http_get, http_post
from clawmes.lib.params import ParamError, read_enum, read_str
from clawmes.lib.tool_result import error_result, json_result
from clawmes.tools.registry import read_tool, register_with_ctx

_VALID_ACTIONS = ["discover", "send_task"]


_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": _VALID_ACTIONS,
        },
        "agent_url": {
            "type": "string",
            "description": (
                "Base URL of the A2A peer (e.g. https://bv7x.ai). For "
                "discover, we fetch /.well-known/agent-card.json. For "
                "send_task, we POST to {agent_url}/api/bv7x/a2a/tasks/send "
                "by default; override the task path with task_path="
            ),
        },
        "task_path": {
            "type": "string",
            "description": (
                "Override the task POST path. Defaults to "
                "'/api/bv7x/a2a/tasks/send' (matches BV-7X). Some peers "
                "expose '/a2a/tasks/send' or '/tasks/send'."
            ),
        },
        "skill": {
            "type": "string",
            "description": (
                "A2A skill name to invoke. Required for send_task. "
                "Discover the peer's available skills via action=discover."
            ),
        },
        "params": {
            "type": "object",
            "description": (
                "Optional params dict to pass to the skill. Shape is "
                "skill-specific; check the peer's discover output."
            ),
        },
        "task_id": {
            "type": "string",
            "description": "Optional JSON-RPC request id. Defaults to '1'.",
        },
    },
    "required": ["action", "agent_url"],
}


@read_tool(
    name="a2a_call",
    toolset="clawmes-intelligence",
    description=(
        "Call another agent via the A2A protocol (JSON-RPC 2.0). "
        "Action=discover fetches the peer's AgentCard + skill list. "
        "Action=send_task POSTs a JSON-RPC task. Target host must be on "
        "the network allowlist (/allow <host> for session-scoped peers). "
        "Authentication via DID signatures is not yet supported \u2014 v1 "
        "is for unauthenticated read tasks."
    ),
    schema=_SCHEMA,
    emoji="\U0001f9e9",  # 🧩
)
def a2a_call(args: dict[str, Any], **kwargs: Any) -> str:
    try:
        action = read_enum(args, "action", _VALID_ACTIONS, required=True)
        agent_url = read_str(args, "agent_url", required=True)
    except ParamError as exc:
        return error_result(str(exc), code="param_error")
    assert agent_url is not None

    parsed = urlparse(agent_url)
    if not parsed.scheme or not parsed.netloc:
        return error_result(
            f"agent_url is malformed (no scheme/host): {agent_url!r}",
            code="param_error",
        )
    base = f"{parsed.scheme}://{parsed.netloc}"

    if action == "discover":
        return _handle_discover(base)
    # action == "send_task" — the only remaining option.
    return _handle_send_task(args, base)


def _handle_discover(base_url: str) -> str:
    card_url = base_url.rstrip("/") + "/.well-known/agent-card.json"
    try:
        card = http_get(card_url, timeout=15.0)
    except Exception as exc:  # noqa: BLE001 — surface plain
        return error_result(f"A2A discover failed: {exc}", code="api_error")
    if not isinstance(card, dict):
        return error_result(
            f"discover returned non-dict response: {type(card).__name__}",
            code="api_error",
        )
    skills = card.get("skills") or card.get("capabilities") or []
    summary_lines = [
        f"A2A agent at {base_url}:",
        f"  Name: {card.get('name', '?')}",
        f"  Description: {card.get('description', '?')}",
    ]
    if isinstance(skills, list):
        summary_lines.append(f"  Skills: {len(skills)}")
        for skill in skills[:10]:
            if isinstance(skill, str):
                summary_lines.append(f"    \u2022 {skill}")
            elif isinstance(skill, dict):
                name = skill.get("id") or skill.get("name") or "(unnamed)"
                summary_lines.append(f"    \u2022 {name}")
    return json_result(
        {"agent_card": card},
        summary="\n".join(summary_lines),
    )


def _handle_send_task(args: dict[str, Any], base_url: str) -> str:
    try:
        skill = read_str(args, "skill", required=True)
        task_path = read_str(args, "task_path") or "/api/bv7x/a2a/tasks/send"
        task_id = read_str(args, "task_id") or "1"
    except ParamError as exc:
        return error_result(str(exc), code="param_error")
    assert skill is not None
    params = args.get("params") or {}
    if not isinstance(params, dict):
        return error_result(
            f"params must be an object, got {type(params).__name__}",
            code="param_error",
        )

    url = base_url.rstrip("/") + (task_path if task_path.startswith("/") else "/" + task_path)
    body = {
        "jsonrpc": "2.0",
        "id": task_id,
        "method": "tasks/send",
        "params": {
            "skill": skill,
            **params,
        },
    }
    try:
        response = http_post(url, json=body, timeout=30.0)
    except Exception as exc:  # noqa: BLE001
        return error_result(f"A2A send_task failed: {exc}", code="api_error")
    if not isinstance(response, dict):
        return error_result(
            f"send_task returned non-dict response: {type(response).__name__}",
            code="api_error",
        )

    # JSON-RPC 2.0 envelope: either ``result`` or ``error``.
    if "error" in response and isinstance(response["error"], dict):
        err = response["error"]
        msg = str(err.get("message") or err)
        return error_result(
            f"A2A peer returned error: {msg}",
            code="api_error",
        )

    result = response.get("result")
    return json_result(
        {"rpc_id": response.get("id", task_id), "result": result},
        summary=(
            f"A2A {skill} task accepted by {base_url}"
            + (f" \u2014 result keys: {list(result.keys())}" if isinstance(result, dict) else "")
        ),
    )


def register(ctx) -> None:
    register_with_ctx(ctx, a2a_call)
