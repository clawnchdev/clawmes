"""Link enrichment for the Hermes Desktop app.

The Hermes Desktop renderer (``apps/desktop`` in NousResearch/hermes-agent)
has no plugin-UI API, so a Python plugin influences the UI purely through the
*shape* of a tool's result. Verified against the desktop source, two layers
read clawmes output differently — and the distinction is load-bearing:

* The **Artifacts view** (``artifacts/index.tsx``) deep-recurses the entire
  parsed result JSON, so any ``http(s)://`` value — even nested inside
  ``details`` — is collected as a clickable **Link artifact**. This is the
  layer this module targets: explorer / DexScreener / Clanker links live under
  descriptive keys in ``details`` and surface here.

* The **chat tool-card** structured extractors (``tool-fallback-model.ts``
  ``toolPreviewTarget`` / ``toolImageUrl``) read only the **top level** of the
  result envelope (``result.preview`` / ``result.url`` / ``result.image_url``),
  NOT ``details``. So a preview/card path must be emitted at the envelope top
  level — see :func:`clawmes.lib.tool_result.json_result`'s ``preview=`` arg —
  *not* placed in ``details``. (An earlier ``attach_preview`` helper put it in
  ``details``; tracing the desktop source proved that's one level too deep, so
  it was removed.)

This module therefore only constructs the descriptive ``details`` links
(``explorer_url`` / ``dexscreener_url`` / ``clanker_url``). Everything here is
pure (no I/O, no network), cheap to unit-test, and safe to call from any tool.
"""

from __future__ import annotations

from typing import Any

from clawmes.lib.chains import get_chain

# DexScreener addresses chains by a URL slug rather than chain id. Only the
# chains DexScreener actually indexes are mapped; everything else yields no
# DexScreener link (we never emit a URL that would 404).
_DEXSCREENER_SLUGS: dict[int, str] = {
    1: "ethereum",
    8453: "base",
    42161: "arbitrum",
    10: "optimism",
    137: "polygon",
    324: "zksync",
    534352: "scroll",
    81457: "blast",
}

# Clanker (the launchpad token explorer clawmes deploys through) is Base-only.
_CLANKER_CHAIN_ID = 8453
_CLANKER_BASE_URL = "https://clanker.world/clanker"
_DEXSCREENER_BASE_URL = "https://dexscreener.com"


def _is_tx_hash(value: str) -> bool:
    """True for a ``0x`` + 64 hex-char transaction hash."""
    if not isinstance(value, str) or not value.startswith("0x"):
        return False
    body = value[2:]
    return len(body) == 64 and all(c in "0123456789abcdefABCDEF" for c in body)


def _is_address(value: str) -> bool:
    """True for a ``0x`` + 40 hex-char EVM address."""
    if not isinstance(value, str) or not value.startswith("0x"):
        return False
    body = value[2:]
    return len(body) == 40 and all(c in "0123456789abcdefABCDEF" for c in body)


def _explorer_base(chain_id: int) -> str | None:
    """Return the block-explorer base URL for ``chain_id``, or None if unknown."""
    try:
        return get_chain(chain_id).block_explorer_url
    except KeyError:
        return None


def explorer_tx_url(tx_hash: str, chain_id: int) -> str | None:
    """Block-explorer URL for a transaction, or None if inputs are invalid."""
    if not _is_tx_hash(tx_hash):
        return None
    base = _explorer_base(chain_id)
    return f"{base}/tx/{tx_hash}" if base else None


def explorer_address_url(address: str, chain_id: int) -> str | None:
    """Block-explorer URL for an address, or None if inputs are invalid."""
    if not _is_address(address):
        return None
    base = _explorer_base(chain_id)
    return f"{base}/address/{address}" if base else None


def explorer_token_url(token: str, chain_id: int) -> str | None:
    """Block-explorer token page URL, or None if inputs are invalid."""
    if not _is_address(token):
        return None
    base = _explorer_base(chain_id)
    return f"{base}/token/{token}" if base else None


def dexscreener_url(token: str, chain_id: int) -> str | None:
    """DexScreener token page URL, or None if the chain isn't indexed."""
    if not _is_address(token):
        return None
    slug = _DEXSCREENER_SLUGS.get(chain_id)
    return f"{_DEXSCREENER_BASE_URL}/{slug}/{token}" if slug else None


def clanker_url(token: str, chain_id: int = _CLANKER_CHAIN_ID) -> str | None:
    """Clanker token page URL (Base only), or None otherwise."""
    if chain_id != _CLANKER_CHAIN_ID or not _is_address(token):
        return None
    return f"{_CLANKER_BASE_URL}/{token}"


def enrich_tx_links(details: dict[str, Any], *, tx_hash: str, chain_id: int) -> dict[str, Any]:
    """Add an ``explorer_url`` for ``tx_hash`` to ``details`` (in place).

    No-op when the hash/chain can't produce a valid link, and never overwrites
    an ``explorer_url`` a tool already set. Returns ``details`` for chaining.
    """
    if "explorer_url" not in details:
        url = explorer_tx_url(tx_hash, chain_id)
        if url:
            details["explorer_url"] = url
    return details


def enrich_token_links(
    details: dict[str, Any],
    *,
    token: str,
    chain_id: int,
    include_clanker: bool = True,
) -> dict[str, Any]:
    """Add market/explorer links for a token to ``details`` (in place).

    Adds ``dexscreener_url`` and ``token_explorer_url`` (and ``clanker_url`` on
    Base when ``include_clanker``). Existing keys are preserved. Returns
    ``details`` for chaining.
    """
    dex = dexscreener_url(token, chain_id)
    if dex and "dexscreener_url" not in details:
        details["dexscreener_url"] = dex

    tok = explorer_token_url(token, chain_id)
    if tok and "token_explorer_url" not in details:
        details["token_explorer_url"] = tok

    if include_clanker:
        clank = clanker_url(token, chain_id)
        if clank and "clanker_url" not in details:
            details["clanker_url"] = clank

    return details
