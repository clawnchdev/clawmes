"""``clawnchconnect`` — LLM-facing wallet connection tool.

When a write tool fails with ``wallet_not_connected``, the LLM should
call ``clawnchconnect`` to surface the connect flow to the user instead
of telling them to run a slash command. The tool returns either:

  * The current state — when ``mode='status'`` or already connected.
  * A WalletConnect pairing URI — when ``mode='walletconnect'``. The
    LLM is expected to render the URI in a code block so the user can
    scan / open the deep link from their phone wallet.
  * The connected address + chain — when ``mode='bankr'`` and the
    Bankr API key is configured.
  * A pointer to ``/connect_local`` — when ``mode='local'``. We
    deliberately don't accept a password through the tool because
    every kwarg eventually touches transcripts/logs; passwords belong
    in interactive flows only.

Read tool, not write — connecting doesn't broadcast anything on chain.
"""

from __future__ import annotations

from typing import Any

from clawmes.lib.params import read_enum
from clawmes.lib.tool_result import error_result, json_result
from clawmes.services.wallet import get_wallet_state
from clawmes.tools.registry import read_tool, register_with_ctx

_VALID_MODES = ["status", "walletconnect", "bankr", "local"]

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "mode": {
            "type": "string",
            "enum": _VALID_MODES,
            "description": (
                "status: return the current wallet state, no side "
                "effects. walletconnect: start a WC v2 pairing — "
                "returns a URI the user must scan with their phone "
                "wallet. bankr: connect to Bankr (requires "
                "BANKR_API_KEY env). local: NOT supported via tool — "
                "directs the user to run /connect_local instead."
            ),
        },
    },
    "required": ["mode"],
}


@read_tool(
    name="clawnchconnect",
    toolset="clawmes-wallet",
    description=(
        "Connect a wallet (WalletConnect, Bankr custodial) or report the "
        "current connection state. Use this when a write tool returned "
        "'wallet_not_connected' so the user can pair without being told "
        "to run a slash command. For local-key wallets, direct the user "
        "to run /connect_local interactively (passwords don't belong in "
        "tool args)."
    ),
    schema=_SCHEMA,
    emoji="\U0001f517",
)
def clawnchconnect(args: dict[str, Any], **kwargs: Any) -> str:
    mode_arg = read_enum(args, "mode", _VALID_MODES, required=True)

    if mode_arg == "status":
        return _status_result()

    state = get_wallet_state()
    if state.connected and state.mode == mode_arg:
        # Idempotent: same-mode reconnect is a no-op + status report.
        return _status_result()

    if mode_arg == "walletconnect":
        return _connect_walletconnect()
    if mode_arg == "bankr":
        return _connect_bankr()
    # Only "local" remains — read_enum already validated against the
    # four-element set, so this is exhaustive.
    return _connect_local_pointer()


def _status_result() -> str:
    state = get_wallet_state()
    if not state.connected:
        return json_result(
            {
                "connected": False,
                "mode": None,
                "address": None,
                "chain_id": None,
                "chain": None,
            },
            summary=(
                "No wallet connected. Available modes: walletconnect "
                "(pair via QR), bankr (custodial, requires "
                "BANKR_API_KEY), local (run /connect_local interactively)."
            ),
        )
    return json_result(
        {
            "connected": True,
            "mode": state.mode,
            "address": state.address,
            "chain_id": state.chain_id,
            "chain": state.chain_name,
        },
        summary=(
            f"Connected via {state.mode}: {state.address} on "
            f"{state.chain_name or f'chain {state.chain_id}'}"
        ),
    )


def _connect_walletconnect() -> str:
    from clawmes.bridges.process import BridgeError
    from clawmes.services.wallet import WalletConfigError, get_wallet_service

    svc = get_wallet_service()
    try:
        state = svc.connect_walletconnect()
    except WalletConfigError as exc:
        return error_result(str(exc), code="wallet_config_error")
    except BridgeError as exc:
        if exc.code == "config_error":
            return error_result(
                "WalletConnect requires WALLETCONNECT_PROJECT_ID. Get one "
                "free at https://cloud.walletconnect.com and set it in "
                "~/.hermes/.env.",
                code="config_error",
            )
        return error_result(f"WalletConnect bridge error: {exc}", code="bridge_error")

    uri = state.balances.get("_pair_uri", "")
    if not uri:
        return error_result(
            "WalletConnect pairing started but no URI was returned.",
            code="bridge_error",
        )
    return json_result(
        {
            "mode": "walletconnect",
            "pair_uri": uri,
            "instructions": (
                "Show this URI to the user as a QR code or as a deep "
                "link. Once they approve in their wallet, the session "
                "auto-completes and subsequent tool calls will succeed."
            ),
        },
        summary=(
            "WalletConnect pairing started. Show the user this URI:\n\n"
            f"```\n{uri}\n```\n\n"
            "Once approved on the phone, retry the original action."
        ),
    )


def _connect_bankr() -> str:
    from clawmes.services.bankr_service import BankrError
    from clawmes.services.wallet import get_wallet_service

    svc = get_wallet_service()
    try:
        state = svc.connect_bankr()
    except BankrError as exc:
        if exc.code == "no_credentials":
            return error_result(
                "Bankr requires BANKR_API_KEY. Sign up at "
                "https://bankr.bot and set the key in ~/.hermes/.env.",
                code="no_credentials",
            )
        return error_result(f"Bankr connect failed: {exc.message}", code=exc.code)

    return json_result(
        {
            "mode": "bankr",
            "address": state.address,
            "chain_id": state.chain_id,
            "chain": state.chain_name,
        },
        summary=(
            f"Connected to Bankr custodial wallet.\n"
            f"  Address: {state.address}\n"
            f"  Chain:   {state.chain_name}"
        ),
    )


def _connect_local_pointer() -> str:
    return error_result(
        "Local-key wallets require an interactive password. Tell the "
        "user to run /connect_local in their terminal, then retry the "
        "original action.",
        code="interactive_required",
    )


def register(ctx) -> None:
    """Wire ``clawnchconnect`` into Hermes."""
    register_with_ctx(ctx, clawnchconnect)
