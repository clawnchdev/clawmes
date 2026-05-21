"""``/register_agent`` slash command — Clawnch agent registration.

Two-step flow:

  1. ``/register_agent <name> | <description>`` — POST
     ``/api/agents/register`` with name + active wallet + description,
     receive a challenge message.
  2. Service signs the challenge with the active wallet, then POSTs
     ``/api/agents/verify`` to receive the issued API key.

The command runs both steps automatically when a wallet is connected.
The issued ``apiKey`` is printed to the channel — the user must save
it to ``~/.hermes/.env`` as ``CLAWNCH_API_KEY`` (we don't write to
disk on the user's behalf to avoid silent file mutations).
"""

from __future__ import annotations

from typing import Any


def _record(name: str, args: str, result: str) -> None:
    try:
        from clawmes.services.command_history import record_command_call

        record_command_call(name, args, result)
    except Exception:  # noqa: BLE001
        pass


async def handle_register_agent(raw_args: str, **kwargs: Any) -> str:
    arg = (raw_args or "").strip()
    if not arg or "|" not in arg:
        return (
            "Usage: /register_agent <name> | <description>\n\n"
            "Example: /register_agent ClawBot | An agent that launches "
            "memecoins\n\n"
            "Two-step flow: POST /api/agents/register -> sign challenge "
            "with active wallet -> POST /api/agents/verify -> apiKey "
            "returned.\n\n"
            "Save the issued apiKey to ~/.hermes/.env as CLAWNCH_API_KEY "
            "to enable /launch."
        )

    name_part, description_part = arg.split("|", 1)
    name = name_part.strip()
    description = description_part.strip()
    if not name or not description:
        return "Both <name> and <description> are required (split by `|`)."

    out = await _register_flow(name=name, description=description)
    _record("register_agent", raw_args, out)
    return out


async def _register_flow(*, name: str, description: str) -> str:
    from clawmes.services.clawnch import ClawnchError, get_clawnch_service
    from clawmes.services.wallet import get_wallet_service, get_wallet_state

    state = get_wallet_state()
    if not state.connected or not state.address:
        return (
            "No wallet connected. Run /connect (or /connect_local / "
            "/connect_bankr) first — registration requires a wallet "
            "signature."
        )

    wallet_address = state.address
    svc = get_clawnch_service()

    try:
        challenge_response = svc.register_agent(
            name=name, wallet=wallet_address, description=description
        )
    except ClawnchError as exc:
        return f"Registration step 1 failed ({exc.code}): {exc.message}"
    except Exception as exc:  # noqa: BLE001
        return f"Registration step 1 failed: {exc}"

    registration_id = challenge_response.get("registrationId")
    message = challenge_response.get("message")
    if not registration_id or not message:
        return (
            "Clawnch returned an incomplete challenge — missing "
            "registrationId or message. Try again."
        )

    wallet_svc = get_wallet_service()
    mode = wallet_svc.active_mode
    if mode is None:
        return "Active wallet mode not available; reconnect via /connect."

    try:
        signature = mode.sign_personal_message(message)
    except Exception as exc:  # noqa: BLE001
        return f"Wallet signing failed: {exc}"

    try:
        verify_response = svc.verify_agent(registration_id=registration_id, signature=signature)
    except ClawnchError as exc:
        return f"Registration step 2 failed ({exc.code}): {exc.message}"
    except Exception as exc:  # noqa: BLE001
        return f"Registration step 2 failed: {exc}"

    api_key = verify_response.get("apiKey")
    agent_id = verify_response.get("agentId")
    if not api_key:
        return "Clawnch verified the signature but returned no apiKey — contact support."

    return "\n".join(
        [
            "Agent registered.",
            f"  Agent ID: {agent_id}",
            f"  Wallet:   {wallet_address}",
            f"  API key:  {api_key}",
            "",
            "Save this in ~/.hermes/.env (or your secret store):",
            f"  CLAWNCH_API_KEY={api_key}",
            "",
            "Restart Hermes so the clawnch service picks up the key, "
            "then /launch to deploy a token.",
        ]
    )


def register(ctx) -> None:
    ctx.register_command(
        name="register_agent",
        handler=handle_register_agent,
        description="Register a Clawnch agent and receive an API key",
        args_hint="<name> | <description>",
    )
