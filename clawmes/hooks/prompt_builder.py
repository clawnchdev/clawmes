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

Sources we read:
  * ``services.wallet.get_wallet_state()`` — connected wallet + chain
  * ``services.persona_service``           — active persona snippet
  * ``services.mode_service``              — readonly / danger mode flag

Sources scheduled for follow-ups:
  * ``services.session_recall`` — past-conversation summary (TODO)
  * Bundled skill registry      — top-N relevance-scored hints (TODO)
"""

from __future__ import annotations

from typing import Any

from clawmes.lib.logger import logger_for

_log = logger_for("hooks.pre_llm_call")

# Hard cap on injected context bytes — guards against runaway state dumps.
MAX_INJECT_CHARS = 8000


def callback(*, session_key: str | None = None, **kwargs: Any) -> dict[str, str] | None:
    """Build the per-turn dynamic context block.

    Each source is read inside its own try/except so a failing source
    can't block the others; an empty result returns ``None`` so Hermes
    knows we have nothing to add this turn.
    """
    pieces: list[str] = []

    _append_wallet(pieces)
    _append_mode(pieces)
    _append_persona(pieces)

    if not pieces:
        return None

    text = "\n".join(pieces)
    if len(text) > MAX_INJECT_CHARS:
        text = text[:MAX_INJECT_CHARS] + "\n[clawmes/context truncated]"
    return {"context": text}


def _append_wallet(pieces: list[str]) -> None:
    try:
        from clawmes.services.wallet import get_wallet_state

        state = get_wallet_state()
        if state.connected:
            pieces.append(
                f"[clawmes/wallet] connected={state.address} "
                f"chain={state.chain_name} mode={state.mode}"
            )
    except Exception:  # noqa: BLE001
        _log.exception("failed to read wallet state for prompt context")


def _append_mode(pieces: list[str]) -> None:
    try:
        from clawmes.services.mode_service import get_mode_service

        mode = get_mode_service().mode
        if mode == "readonly":
            pieces.append(
                "[clawmes/mode] readonly mode active — every write tool will be "
                "blocked at the gate. Tell the user if they ask why a tx isn't "
                "submitting; suggest /safemode off when appropriate."
            )
        elif mode == "danger":
            pieces.append(
                "[clawmes/mode] danger mode active — readonly check is bypassed. "
                "Policy gating still applies. Be extra explicit with the user "
                "before submitting any large or irreversible transaction."
            )
    except Exception:  # noqa: BLE001
        _log.exception("failed to read mode for prompt context")


def _append_persona(pieces: list[str]) -> None:
    try:
        from clawmes.services.persona_service import get_persona_service

        snippet = get_persona_service().active_snippet()
        if snippet:
            pieces.append(f"[clawmes/persona]\n{snippet.strip()}")
    except Exception:  # noqa: BLE001
        _log.exception("failed to read persona for prompt context")
