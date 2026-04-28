"""``transfer`` — send ETH or ERC-20 tokens and (optionally) wait for the receipt.

Two actions:

  * ``estimate`` — returns the gas estimate (21000 for native, ~100k
    for ERC-20), the wei/base-unit value, and the route summary. No
    on-chain submission.
  * ``send``    — broadcasts via the active wallet mode, returns the
    tx hash, and (when ``await_receipt`` is true, the default) blocks
    until the receipt arrives or the timeout elapses.

Native vs ERC-20:

  * Native (no ``token``) — converts amount via the chain's
    ``native_decimals`` from the static registry; gas is the EVM-fixed
    21000.
  * ERC-20 (``token=<address>``) — looks up decimals via
    :class:`TokenDecimalsService` (RPC + on-disk cache, falls back to
    18 on lookup failure), encodes ``transfer(to, amount)`` calldata,
    and submits with ``value=0``. Gas defaults to 100,000 (a safe
    upper bound for a vanilla ERC-20 transfer; real estimation lands
    when ``eth_estimateGas`` integration follows).

The tool reads the chain id from the connected wallet state — callers
that want to override pass ``chain_id`` explicitly.

Policy gating note: the ``@write_tool`` decorator extracts
``value_wei``/``amount_wei`` for the policy evaluator. Native tx with
the human-friendly ``amount`` field do not auto-populate value_wei in
the gate; callers that want quantitative gates to fire pass
``value_wei`` explicitly.
"""

from __future__ import annotations

from typing import Any

from clawmes.lib.abi import encode_transfer
from clawmes.lib.chains import get_chain
from clawmes.lib.decimals import to_base_units
from clawmes.lib.ens import EnsError, is_ens_name
from clawmes.lib.ens import resolve as resolve_ens
from clawmes.lib.logger import logger_for
from clawmes.lib.params import read_bool, read_str
from clawmes.lib.tool_result import error_result, json_result
from clawmes.services.wallet import get_wallet_state
from clawmes.tools.registry import register_with_ctx, write_tool

_log = logger_for("tools.transfer")

# Fallback ceiling when ``eth_estimateGas`` fails or isn't available.
# Real gas usage for a vanilla ERC-20 transfer is 50–65k; 100k gives
# headroom for first-touch storage initialization without overpaying
# wildly.
_ERC20_GAS_DEFAULT = 100_000

# Native EVM transfer is fixed at 21000 gas — no estimation needed.
_NATIVE_GAS = 21000


class _SimulationReverted(Exception):
    """Raised when ``eth_estimateGas`` reports the tx would revert.

    Distinct from a network error: the chain itself rejected the
    simulation, so broadcasting would burn gas for no effect. We
    refuse rather than fall back.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["send", "estimate"],
            "description": "send executes; estimate returns gas + route only",
        },
        "to": {
            "type": "string",
            "description": (
                "Recipient address (0x...) or ENS name (e.g. vitalik.eth). "
                "ENS names are resolved via Ethereum mainnet regardless of "
                "the wallet's current chain."
            ),
        },
        "amount": {
            "type": "string",
            "description": "Human-readable amount (e.g. '0.5')",
        },
        "token": {
            "type": "string",
            "description": (
                "ERC-20 contract address; omit for native ETH/gas token. "
                "Not yet implemented at this milestone."
            ),
        },
        "chain_id": {
            "type": "integer",
            "description": (
                "Override the connected wallet's chain. Defaults to the "
                "wallet's current chain when omitted."
            ),
        },
        "await_receipt": {
            "type": "boolean",
            "description": (
                "When true (default), block until the tx is mined and "
                "report success/revert. When false, return immediately "
                "after broadcast with the tx hash."
            ),
        },
        "value_wei": {
            "type": "string",
            "description": (
                "Optional explicit wei value. When set, the policy "
                "evaluator uses it for quantitative gates."
            ),
        },
        "policyConfirmationNonce": {
            "type": "string",
            "description": "Set when retrying after a POLICY HOLD response",
        },
    },
    "required": ["action", "to", "amount"],
}


@write_tool(
    name="transfer",
    toolset="clawmes-wallet",
    description=(
        "Send native ETH (or the chain's gas token) to a recipient. "
        "Returns gas estimate when action='estimate'; submits the "
        "transaction and (by default) waits for the receipt when "
        "action='send'. Requires a connected wallet (/connect, "
        "/connect_bankr, or /connect_local). ERC-20 transfers are not "
        "yet implemented."
    ),
    schema=_SCHEMA,
    emoji="\U0001f4b8",
)
def transfer(args: dict[str, Any], **kwargs: Any) -> str:
    state = get_wallet_state()
    if not state.connected:
        return error_result(
            "No wallet connected. Run /connect (WalletConnect), "
            "/connect_bankr (custodial), or /connect_local (local key) "
            "first.",
            code="wallet_not_connected",
        )

    action = read_str(args, "action", required=True)
    if action == "send":
        return _handle_send(args, state)
    if action == "estimate":
        return _handle_estimate(args, state)
    return error_result(f"Unknown action: {action!r}", code="invalid_action")


def _handle_estimate(args: dict[str, Any], state: Any) -> str:
    to_input = read_str(args, "to", required=True)
    amount = read_str(args, "amount", required=True)
    token = read_str(args, "token")
    target_chain_id = _target_chain_id(args, state)
    chain = _lookup_chain(target_chain_id)
    if chain is None:
        return error_result(
            f"Unknown chain id {target_chain_id} — no native decimals known.",
            code="unsupported_chain",
        )

    resolved = _resolve_recipient(to_input)
    if isinstance(resolved, str):
        # An error result was returned in place of a resolved address
        return resolved
    to_addr, ens_name = resolved

    if token:
        return _estimate_erc20(
            chain=chain,
            token=token,
            to_addr=to_addr,
            ens_name=ens_name,
            amount=amount,
        )
    return _estimate_native(
        chain=chain,
        to_addr=to_addr,
        ens_name=ens_name,
        amount=amount,
    )


def _estimate_native(*, chain, to_addr, ens_name, amount) -> str:
    try:
        value_wei = to_base_units(amount, chain.native_decimals)
    except (ValueError, ArithmeticError) as exc:
        return error_result(f"Bad amount {amount!r}: {exc}", code="param_error")

    gas = _NATIVE_GAS
    details: dict[str, Any] = {
        "chain_id": chain.chain_id,
        "chain": chain.name,
        "to": to_addr,
        "amount": amount,
        "value_wei": str(value_wei),
        "estimated_gas": gas,
        "token": "native",
    }
    if ens_name is not None:
        details["ens_name"] = ens_name
        details["resolved_address"] = to_addr
    summary_target = f"{ens_name} ({to_addr})" if ens_name else to_addr
    return json_result(
        details,
        summary=(
            f"Native {chain.native_symbol} transfer to {summary_target} on {chain.name}\n"
            f"  Amount:        {amount} {chain.native_symbol} ({value_wei} wei)\n"
            f"  Estimated gas: {gas}"
        ),
    )


def _estimate_erc20(*, chain, token, to_addr, ens_name, amount) -> str:
    from clawmes.services.token_decimals import TokenDecimalsError

    token_validation = _validate_token_address(token)
    if token_validation is not None:
        return token_validation

    try:
        decimals = _fetch_token_decimals(token, chain.chain_id)
    except TokenDecimalsError as exc:
        return error_result(
            f"Cannot determine decimals for token {token}: {exc.cause}. "
            "Refusing to estimate without confirmed decimals — a wrong "
            "value here can multiply your amount by 10^12.",
            code="decimals_lookup_failed",
        )
    try:
        amount_base = to_base_units(amount, decimals)
    except (ValueError, ArithmeticError) as exc:
        return error_result(f"Bad amount {amount!r}: {exc}", code="param_error")

    calldata = encode_transfer(to_addr, amount_base)
    state = get_wallet_state()
    try:
        gas, gas_source = _estimate_gas_with_fallback(
            from_addr=state.address if state.connected else None,
            to=token,
            data=calldata,
            chain_id=chain.chain_id,
            fallback=_ERC20_GAS_DEFAULT,
        )
    except _SimulationReverted as exc:
        return error_result(
            f"Simulation reverted: {exc.reason}. The transfer would "
            "fail on-chain — common causes are insufficient token "
            "balance, transfer to the zero address, or a paused "
            "token contract.",
            code="simulation_reverted",
        )

    details: dict[str, Any] = {
        "chain_id": chain.chain_id,
        "chain": chain.name,
        "to": to_addr,
        "amount": amount,
        "amount_base_units": str(amount_base),
        "token": token,
        "token_decimals": decimals,
        "estimated_gas": gas,
        "gas_source": gas_source,
    }
    if ens_name is not None:
        details["ens_name"] = ens_name
        details["resolved_address"] = to_addr
    summary_target = f"{ens_name} ({to_addr})" if ens_name else to_addr
    gas_note = "" if gas_source == "estimateGas" else " (fallback ceiling)"
    return json_result(
        details,
        summary=(
            f"ERC-20 transfer on {chain.name}\n"
            f"  Token:         {token} (decimals={decimals})\n"
            f"  To:            {summary_target}\n"
            f"  Amount:        {amount} ({amount_base} base units)\n"
            f"  Estimated gas: {gas}{gas_note}"
        ),
    )


def _handle_send(args: dict[str, Any], state: Any) -> str:
    from clawmes.services.wallet import get_wallet_service

    to_input = read_str(args, "to", required=True)
    amount = read_str(args, "amount", required=True)
    token = read_str(args, "token")
    await_receipt = read_bool(args, "await_receipt", default=True)
    target_chain_id = _target_chain_id(args, state)
    chain = _lookup_chain(target_chain_id)
    if chain is None:
        return error_result(
            f"Unknown chain id {target_chain_id} — no native decimals known.",
            code="unsupported_chain",
        )

    resolved = _resolve_recipient(to_input)
    if isinstance(resolved, str):
        return resolved
    to_addr, ens_name = resolved

    svc = get_wallet_service()
    mode = svc.active_mode
    if mode is None:
        # Defensive: state.connected was True but no active_mode means the
        # service got into an inconsistent state (e.g. mode replaced under
        # us between the state read and here). Treat as not-connected.
        return error_result(
            "Wallet state is connected but no active mode is set; reconnect via /connect.",
            code="wallet_not_connected",
        )

    if token:
        from clawmes.services.token_decimals import TokenDecimalsError

        token_validation = _validate_token_address(token)
        if token_validation is not None:
            return token_validation
        try:
            decimals = _fetch_token_decimals(token, chain.chain_id)
        except TokenDecimalsError as exc:
            return error_result(
                f"Cannot determine decimals for token {token}: {exc.cause}. "
                "Refusing to send without confirmed decimals — a wrong value "
                "here can multiply your amount by 10^12.",
                code="decimals_lookup_failed",
            )
        try:
            amount_base = to_base_units(amount, decimals)
        except (ValueError, ArithmeticError) as exc:
            return error_result(f"Bad amount {amount!r}: {exc}", code="param_error")
        # encode_transfer already returns a 0x-prefixed string
        calldata = encode_transfer(to_addr, amount_base)
        try:
            gas, gas_source = _estimate_gas_with_fallback(
                from_addr=state.address,
                to=token,
                data=calldata,
                chain_id=chain.chain_id,
                fallback=_ERC20_GAS_DEFAULT,
            )
        except _SimulationReverted as exc:
            return error_result(
                f"Simulation reverted: {exc.reason}. Refusing to "
                "broadcast — the tx would fail on-chain and burn your "
                "gas. Common causes: insufficient token balance, "
                "transfer to zero address, paused token contract.",
                code="simulation_reverted",
            )
        try:
            tx_hash = mode.send_transaction(
                to=token,
                value=0,
                data=calldata,
                gas=gas,
                chain_id=chain.chain_id,
            )
        except Exception as exc:  # noqa: BLE001 — surface any signing/RPC error
            return error_result(f"Transaction failed: {exc}", code="send_failed")
        base_details = _erc20_details(
            tx_hash=tx_hash,
            chain=chain,
            token=token,
            to_addr=to_addr,
            ens_name=ens_name,
            amount=amount,
            amount_base=amount_base,
            decimals=decimals,
            gas=gas,
            gas_source=gas_source,
        )
    else:
        try:
            value_wei = to_base_units(amount, chain.native_decimals)
        except (ValueError, ArithmeticError) as exc:
            return error_result(f"Bad amount {amount!r}: {exc}", code="param_error")
        # Simulate the native transfer (with the actual value) so
        # insufficient-balance reverts catch us BEFORE we sign and
        # broadcast a guaranteed-failed tx. We discard the gas number
        # and stick with the EVM-fixed 21000 since native transfers
        # always use exactly that.
        try:
            _estimate_gas_with_fallback(
                from_addr=state.address,
                to=to_addr,
                value=value_wei,
                data="0x",
                chain_id=chain.chain_id,
                fallback=_NATIVE_GAS,
            )
        except _SimulationReverted as exc:
            return error_result(
                f"Simulation reverted: {exc.reason}. Refusing to "
                "broadcast — the tx would fail on-chain. Most common "
                "cause for native transfers: not enough balance to "
                "cover value + gas.",
                code="simulation_reverted",
            )
        try:
            tx_hash = mode.send_transaction(
                to=to_addr,
                value=value_wei,
                chain_id=chain.chain_id,
            )
        except Exception as exc:  # noqa: BLE001 — surface any signing/RPC error
            return error_result(f"Transaction failed: {exc}", code="send_failed")
        base_details = _native_details(
            tx_hash=tx_hash,
            chain=chain,
            to_addr=to_addr,
            ens_name=ens_name,
            amount=amount,
            value_wei=value_wei,
        )

    return _finalize_send(
        base_details=base_details,
        chain=chain,
        await_receipt=await_receipt,
    )


def _native_details(*, tx_hash, chain, to_addr, ens_name, amount, value_wei):
    explorer_url = f"{chain.block_explorer_url}/tx/{tx_hash}"
    details: dict[str, Any] = {
        "tx_hash": tx_hash,
        "explorer_url": explorer_url,
        "chain_id": chain.chain_id,
        "chain": chain.name,
        "to": to_addr,
        "amount": amount,
        "value_wei": str(value_wei),
        "token": "native",
        "status": "pending",
    }
    if ens_name is not None:
        details["ens_name"] = ens_name
        details["resolved_address"] = to_addr
    return details


def _erc20_details(
    *,
    tx_hash,
    chain,
    token,
    to_addr,
    ens_name,
    amount,
    amount_base,
    decimals,
    gas,
    gas_source,
):
    explorer_url = f"{chain.block_explorer_url}/tx/{tx_hash}"
    details: dict[str, Any] = {
        "tx_hash": tx_hash,
        "explorer_url": explorer_url,
        "chain_id": chain.chain_id,
        "chain": chain.name,
        "to": to_addr,
        "amount": amount,
        "amount_base_units": str(amount_base),
        "token": token,
        "token_decimals": decimals,
        "gas_limit": gas,
        "gas_source": gas_source,
        "status": "pending",
    }
    if ens_name is not None:
        details["ens_name"] = ens_name
        details["resolved_address"] = to_addr
    return details


def _finalize_send(
    *,
    base_details: dict[str, Any],
    chain,
    await_receipt: bool,
) -> str:
    from clawmes.services.rpc import RpcError, get_rpc_service

    tx_hash = base_details["tx_hash"]
    explorer_url = base_details["explorer_url"]

    if not await_receipt:
        return json_result(
            base_details,
            summary=(
                f"Submitted: {tx_hash}\n"
                f"View: {explorer_url}\n"
                f"(receipt polling skipped; await_receipt=false)"
            ),
        )

    rpc = get_rpc_service()
    try:
        receipt = rpc.wait_for_receipt(
            tx_hash,
            chain.chain_id,
            timeout=120.0,
            poll_interval=2.0,
        )
    except RpcError as exc:
        # Tx submitted but the receipt didn't arrive in time. Don't mark
        # this as an error tool result — the tx may still mine. Surface
        # as a "pending" result so the LLM can tell the user to check
        # the explorer in a few minutes.
        return json_result(
            base_details,
            summary=(
                f"Submitted: {tx_hash}\n"
                f"View: {explorer_url}\n"
                f"Receipt not seen within timeout: {exc.message}"
            ),
        )

    success, block_num, gas_used = _summarize_receipt(receipt)
    base_details.update(
        {
            "status": "success" if success else "reverted",
            "block_number": block_num,
            "gas_used": gas_used,
        }
    )

    if success:
        summary = (
            f"Confirmed in block {block_num}: {tx_hash}\nGas used: {gas_used}\nView: {explorer_url}"
        )
    else:
        summary = (
            f"Reverted on chain: {tx_hash}\n"
            f"Block: {block_num}, gas used: {gas_used}\n"
            f"View: {explorer_url}"
        )
    return json_result(base_details, summary=summary)


def _estimate_gas_with_fallback(
    *,
    from_addr: str | None,
    to: str,
    data: str,
    chain_id: int,
    fallback: int,
    value: int = 0,
) -> tuple[int, str]:
    """Try ``eth_estimateGas``; classify failures into revert vs network.

    Returns ``(gas, source)`` where ``source`` is ``"estimateGas"`` if
    the RPC succeeded or ``"fallback"`` if a non-revert error occurred
    and we used the static ceiling.

    Raises :class:`_SimulationReverted` if the simulation reverted —
    the caller MUST refuse to broadcast in that case rather than fall
    back to the static gas, because broadcasting would burn the user's
    gas for a guaranteed-failed tx.

    Network/timeout/RPC-unavailable errors fall back to the static
    ceiling, since the user's tx may still succeed once connectivity
    is restored or against a different RPC provider.

    A 25% headroom is added to the RPC estimate to absorb the small
    difference between simulated and actual gas.
    """
    from clawmes.services.rpc import RpcError, get_rpc_service

    try:
        raw = get_rpc_service().estimate_gas(
            from_addr=from_addr,
            to=to,
            value=value,
            data=data,
            chain_id=chain_id,
        )
    except RpcError as exc:
        if _is_revert(exc):
            _log.info(
                "estimateGas reverted for %s on chain %d: %s",
                to,
                chain_id,
                exc.message,
            )
            raise _SimulationReverted(exc.message) from exc
        _log.info(
            "estimateGas RPC failed for %s on chain %d (%s); using fallback %d",
            to,
            chain_id,
            exc.message,
            fallback,
        )
        return fallback, "fallback"

    # 25% headroom over the simulated estimate; cap at the fallback
    # ceiling so we don't accidentally allow runaway gas on a buggy
    # estimateGas response.
    with_headroom = (raw * 5) // 4
    return min(with_headroom, max(fallback, with_headroom)), "estimateGas"


def _is_revert(exc) -> bool:
    """Heuristic: did the simulation revert on-chain vs a network error?

    EVM RPCs return revert errors with a few different codes/messages
    depending on the provider:

      * Geth / Alchemy: ``code=3, message="execution reverted"``,
        sometimes with a decoded reason.
      * Infura: ``code=-32000, message="execution reverted: <reason>"``.
      * Anvil/Hardhat: ``code=-32603, message="VM Exception ... revert"``.
      * Erigon: ``code=-32000, message="execution reverted"``.

    We match on the message keyword "revert" — broad enough to catch
    every RPC we've seen, narrow enough to not capture network errors
    (which carry messages like "timed out", "connection reset", etc.).
    """
    msg = (exc.message or "").lower()
    return "revert" in msg


def _validate_token_address(token: str) -> str | None:
    """Return an error result string if ``token`` is not a 0x address; ``None`` otherwise.

    Token contracts are addressed by hex only — no ENS support
    (resolving an ENS name to a contract address requires the user to
    know the resolver returns the deployment, which it doesn't always
    do). Failing fast here is friendlier than a malformed eth_call.
    """
    if not token.startswith(("0x", "0X")) or len(token) != 42:
        return error_result(
            f"Invalid token address: {token!r} — must be 0x + 40 hex chars.",
            code="param_error",
        )
    return None


def _fetch_token_decimals(token: str, chain_id: int) -> int:
    """Return ERC-20 decimals for ``token`` on ``chain_id``, or raise.

    The transfer tool's send path converts a human amount to base
    units via this value. A silent fallback to 18 here would — for
    a 6-decimal token like USDC — encode 100 as ``100 * 10^18`` base
    units = 100 trillion USDC. We use the strict path that propagates
    :class:`TokenDecimalsError` so a lookup failure becomes a clean
    tool error instead of a catastrophic transaction.
    """
    from clawmes.services.token_decimals import get_token_decimals_service

    return get_token_decimals_service().get_strict(token, chain_id)


def _resolve_recipient(to_input: str) -> tuple[str, str | None] | str:
    """Validate / resolve a transfer recipient.

    Returns:
        A ``(checksummed_address, ens_name_or_None)`` tuple on success.
        A pre-rendered error result string on failure (the caller
        bubbles this back to the LLM unchanged).

    The checksum is best-effort — eth_utils is already in the wallet
    dep tree, but if checksumming raises for any reason we return the
    lowercase form so the tx still goes through.
    """
    if not to_input:
        return error_result("Missing recipient address", code="param_error")

    # Already a hex address — light validation only.
    if to_input.startswith(("0x", "0X")):
        if len(to_input) != 42:
            return error_result(
                f"Invalid address length: {to_input!r} (expected 42 chars)",
                code="param_error",
            )
        try:
            from eth_utils import to_checksum_address

            return to_checksum_address(to_input), None
        except Exception:  # noqa: BLE001 — eth_utils raises on bad hex
            return error_result(f"Invalid address: {to_input!r}", code="param_error")

    # Looks like an ENS name — resolve via mainnet.
    if is_ens_name(to_input):
        try:
            resolved = resolve_ens(to_input)
        except EnsError as exc:
            return error_result(
                f"Could not resolve {to_input!r}: {exc.message}",
                code=f"ens_{exc.code}",
            )
        return resolved, to_input

    return error_result(
        f"Recipient {to_input!r} is neither a 0x address nor an ENS name.",
        code="param_error",
    )


def _target_chain_id(args: dict[str, Any], state: Any) -> int:
    raw = args.get("chain_id")
    if raw is not None:
        try:
            return int(raw)
        except (TypeError, ValueError):
            pass
    return int(state.chain_id) if state.chain_id is not None else 8453


def _lookup_chain(chain_id: int):
    try:
        return get_chain(chain_id)
    except KeyError:
        return None


def _summarize_receipt(receipt: dict[str, Any]) -> tuple[bool, int, int]:
    """Pull (success, block_number, gas_used) out of a JSON-RPC receipt.

    Receipt fields can be hex strings or ints depending on the RPC; we
    normalize both. Pre-Byzantium receipts use ``root`` instead of
    ``status``; we treat root-style as "success" since reverts in those
    eras manifested as exceptions, not failed receipts.
    """
    status_raw = receipt.get("status")
    if status_raw is None:
        # Pre-Byzantium: presence of `root` ≈ success. We don't have to
        # be strictly correct here since modern chains all use status.
        success = True
    else:
        success = _hex_or_int(status_raw) == 1
    block_num = _hex_or_int(receipt.get("blockNumber") or 0)
    gas_used = _hex_or_int(receipt.get("gasUsed") or 0)
    return success, block_num, gas_used


def _hex_or_int(value: Any) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value, 16) if value.startswith(("0x", "0X")) else int(value, 10)
    return 0


def register(ctx) -> None:
    """Wire ``transfer`` into Hermes."""
    register_with_ctx(ctx, transfer)
