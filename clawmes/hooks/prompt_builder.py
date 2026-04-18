"""``pre_llm_call`` hook — inject dynamic per-turn context.

Hermes preserves the system prompt across turns to keep prompt cache
valid. Anything dynamic (current wallet, persona, mode, recent commands)
must be injected into the **current turn's user message** via this hook.
The return shape ``{"context": "..."}`` (or a plain string) appends to
the user message; multiple plugins' contexts are joined with double
newlines in alphabetical-by-directory order.

Cap on injected text is documented in ``HERMES_PARITY.md`` — currently
~8KB combined to stay under typical model-specific compression
thresholds.
"""

from __future__ import annotations

from typing import Any

from clawmes.lib.logger import logger_for

_log = logger_for("hooks.pre_llm_call")

# Hard cap on injected context bytes — guards against runaway state dumps.
MAX_INJECT_CHARS = 8000


def callback(*, session_key: str | None = None, **kwargs: Any) -> dict[str, str] | None:
    """Build the per-turn dynamic context block.

    Pulls from:
      * ``services.wallet.get_wallet_state()`` — connected wallet, chain
      * ``services.persona_service`` — active persona text snippet
      * ``services.mode_service`` — safemode / dangermode flag
      * ``services.session_recall`` — recent-conversation summary
      * Bundled skill registry — relevance-scored skill hints (top 2-3)

    Returns ``None`` if there is nothing to inject. Hermes is happy with
    that.
    """
    pieces: list[str] = []

    try:
        from clawmes.services.wallet import get_wallet_state

        state = get_wallet_state()
        if state.connected:
            pieces.append(
                f"[clawmes/wallet] connected={state.address} "
                f"chain={state.chain_name} mode={state.mode}"
            )
    except Exception:  # noqa: BLE001 — defensive; never break the LLM call
        _log.exception("failed to read wallet state for prompt context")

    if not pieces:
        return None

    text = "\n".join(pieces)
    if len(text) > MAX_INJECT_CHARS:
        text = text[:MAX_INJECT_CHARS] + "\n[clawmes/context truncated]"
    return {"context": text}
