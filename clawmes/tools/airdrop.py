"""``airdrop`` — airdrop eligibility checking + claim execution.

Three actions:

  * ``eligibility`` — query a Merkle-distributor contract or REST
    endpoint to check if the wallet is eligible.
  * ``claim``       — call ``claim(index, account, amount, proof)``
    on the distributor contract. Caller provides the proof, typically
    obtained from the airdrop's API in the eligibility step.
  * ``list``        — placeholder for tracked airdrops. Most airdrop
    aggregators (DefiLlama Airdrops, Earnifi) require auth so this
    is currently not_implemented.

Most airdrops use the canonical OpenZeppelin Merkle distributor
pattern (claim function with index, account, amount, proof). This
tool encodes that signature; airdrops with custom distributors need
the user to supply ``calldata`` directly.
"""

from __future__ import annotations

from typing import Any

from clawmes.lib.abi import encode_address, encode_uint
from clawmes.lib.http import http_get
from clawmes.lib.logger import logger_for
from clawmes.lib.params import read_int, read_str
from clawmes.lib.tool_result import error_result, json_result
from clawmes.services.wallet import get_wallet_state
from clawmes.tools.registry import register_with_ctx, write_tool

_log = logger_for("tools.airdrop")

# claim(uint256,address,uint256,bytes32[]) selector. Same across
# every Merkle distributor that follows the OZ pattern.
SELECTOR_CLAIM = "0x2e7ba6ef"

_AIRDROP_GAS_DEFAULT = 200_000

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["eligibility", "claim", "list"],
        },
        "distributor": {
            "type": "string",
            "description": "Merkle distributor contract address.",
        },
        "endpoint": {
            "type": "string",
            "description": (
                "Eligibility-check API URL. Most airdrops have one "
                "(e.g. https://drop.example.com/api/eligibility?address=0x...). "
                "Tool issues a GET; the response shape depends on the "
                "airdrop. Common pattern: {eligible: true, amount: ..., "
                "proof: [...]}."
            ),
        },
        "index": {
            "type": "integer",
            "description": "Merkle index (claim ID). Required for claim.",
        },
        "amount": {
            "type": "string",
            "description": "Claim amount in base units. Required for claim.",
        },
        "proof": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Merkle proof — array of bytes32 hex strings.",
        },
        "calldata": {
            "type": "string",
            "description": (
                "Override calldata for non-standard distributors that "
                "don't match the OZ Merkle signature."
            ),
        },
        "chain_id": {
            "type": "integer",
            "description": "Chain id. Defaults to wallet's chain.",
        },
        "policyConfirmationNonce": {
            "type": "string",
            "description": "Set when retrying after POLICY HOLD.",
        },
    },
    "required": ["action"],
}


@write_tool(
    name="airdrop",
    toolset="clawmes-defi",
    description=(
        "Airdrop eligibility checking + claim execution. eligibility "
        "queries a per-airdrop API; claim calls the Merkle distributor's "
        "claim() with the supplied proof. Custom distributors can use "
        "the calldata override. list is a stub (most aggregators need "
        "auth)."
    ),
    schema=_SCHEMA,
    emoji="\U0001f381",
)
def airdrop(args: dict[str, Any], **kwargs: Any) -> str:
    action = read_str(args, "action", required=True)
    state = get_wallet_state()

    if action == "list":
        return error_result(
            "Airdrop list/discovery requires a third-party aggregator "
            "(DefiLlama Airdrops, Earnifi). Check those directly with "
            "your wallet address.",
            code="not_implemented",
        )

    if not state.connected or not state.address:
        return error_result(
            "No wallet connected. Run /connect first.",
            code="wallet_not_connected",
        )

    chain_id = _resolve_chain_id(args, state)

    if action == "eligibility":
        return _handle_eligibility(args, state)
    return _handle_claim(args, state, chain_id)


def _handle_eligibility(args, state) -> str:
    endpoint = read_str(args, "endpoint", required=True)
    if not endpoint.startswith("https://"):
        return error_result("endpoint must be HTTPS for security.", code="param_error")
    try:
        # The airdrop API likely takes ?address=0x... but conventions
        # vary. We pass it both as query param and let the user
        # provide a fully-formed URL if they need a different shape.
        url = endpoint
        if "?" not in url:
            url = f"{url}?address={state.address}"
        result = http_get(url, timeout=15.0)
    except Exception as exc:  # noqa: BLE001
        return error_result(f"Eligibility check failed: {exc}", code="api_error")
    return json_result(
        {"endpoint": endpoint, "address": state.address, "result": result},
        summary=(f"Eligibility for {state.address}:\n  raw response: {result}"),
    )


def _handle_claim(args, state, chain_id: int) -> str:
    distributor = read_str(args, "distributor", required=True)
    if not distributor.startswith(("0x", "0X")) or len(distributor) != 42:
        return error_result(
            f"Invalid distributor address: {distributor!r}",
            code="param_error",
        )

    custom_calldata = read_str(args, "calldata")
    if custom_calldata:
        calldata = custom_calldata
    else:
        index = read_int(args, "index")
        amount_raw = read_str(args, "amount")
        proof = args.get("proof") or []
        if index is None or amount_raw is None or not isinstance(proof, list):
            return error_result(
                "claim needs index, amount, and proof (or calldata override)",
                code="param_error",
            )
        try:
            amount = int(amount_raw)
        except (TypeError, ValueError):
            return error_result(f"Bad amount {amount_raw!r}", code="param_error")
        calldata = _encode_claim(index, state.address, amount, proof)
        if isinstance(calldata, str) and calldata.startswith("__error__"):
            return calldata[len("__error__") :]

    from clawmes.services.wallet import get_wallet_service

    svc = get_wallet_service()
    mode = svc.active_mode
    if mode is None:
        return error_result(
            "No active wallet mode; reconnect via /connect.",
            code="wallet_not_connected",
        )
    try:
        tx_hash = mode.send_transaction(
            to=distributor,
            value=0,
            data=calldata,
            gas=_AIRDROP_GAS_DEFAULT,
            chain_id=chain_id,
        )
    except Exception as exc:  # noqa: BLE001
        return error_result(f"Claim failed: {exc}", code="send_failed")
    return json_result(
        {
            "tx_hash": tx_hash,
            "distributor": distributor,
            "chain_id": chain_id,
        },
        summary=f"Airdrop claim submitted: {tx_hash}",
    )


def _encode_claim(index: int, account: str, amount: int, proof: list) -> str:
    """Build calldata for the OZ Merkle distributor's claim().

    Signature: claim(uint256 index, address account, uint256 amount,
                     bytes32[] merkleProof)

    The bytes32[] dynamic array requires:
      - selector
      - index (32 bytes)
      - account (32 bytes)
      - amount (32 bytes)
      - offset to proof data (32 bytes, = 0x80)
      - proof.length (32 bytes)
      - proof[0]...proof[N-1] (each 32 bytes)
    """
    try:
        # Validate proof entries are hex
        proof_clean = []
        for p in proof:
            if not isinstance(p, str):
                return "__error__" + error_result(
                    f"Proof entries must be hex strings: {p!r}",
                    code="param_error",
                )
            cleaned = p.removeprefix("0x").rjust(64, "0")
            int(cleaned, 16)  # validate hex
            proof_clean.append(cleaned)
    except (ValueError, AttributeError):
        return "__error__" + error_result("Proof contains non-hex entries", code="param_error")

    head = (
        SELECTOR_CLAIM
        + encode_uint(index)
        + encode_address(account)
        + encode_uint(amount)
        + encode_uint(0x80)  # offset to proof array
    )
    tail = encode_uint(len(proof_clean)) + "".join(proof_clean)
    return head + tail


def _resolve_chain_id(args: dict[str, Any], state) -> int:
    explicit = read_int(args, "chain_id")
    if explicit is not None:
        return explicit
    return int(state.chain_id) if state.chain_id is not None else 1


def register(ctx) -> None:
    """Wire ``airdrop`` into Hermes."""
    register_with_ctx(ctx, airdrop)
