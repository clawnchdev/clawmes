"""UI artifact enrichment for the Hermes Desktop app.

The Hermes Desktop renderer (``apps/desktop`` in NousResearch/hermes-agent)
surfaces tool output through three generic systems, all driven by the *shape*
of a tool's ``details`` dict — there is no plugin-UI API, so a Python plugin
injects UI purely by emitting the right keys:

1. **Artifacts view** scrapes every string value in the result; any value that
   looks like an ``http(s)://`` URL or a file path is collected into the
   Images / Files / Links tabs. So *any* link we put in ``details`` becomes a
   clickable artifact regardless of its key name.

2. **Structured tool-card summary** renders ``details`` keys as bullet lines,
   prioritising ``title / name / path / url / status / …``.

3. **Auto-opening side preview pane** inspects, in order, the keys
   ``url, target, path, file, filepath, preview`` and *auto-opens the first one
   that looks like a URL or path* in the right-rail webview.

This module centralises the link construction + the precise key discipline:

* Explorer / DexScreener / Clanker links go under **descriptive keys**
  (``explorer_url``, ``dexscreener_url``, ``clanker_url``) so they become
  passive clickable Link artifacts **without** hijacking the preview pane on
  every call.
* A generated HTML card is attached under the **``preview``** key (via
  :func:`attach_preview`) so — and only so — the desktop auto-opens it.

Everything here is pure (no I/O, no network) so it is cheap to unit-test and
safe to call from inside any tool handler.
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

# Keys the desktop's preview router inspects, in priority order, to decide what
# to auto-open in the side rail. Exposed for tests + callers that need to reason
# about whether a details dict will trigger an auto-open.
PREVIEW_TRIGGER_KEYS: tuple[str, ...] = ("url", "target", "path", "file", "filepath", "preview")


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


def attach_preview(details: dict[str, Any], path: str) -> dict[str, Any]:
    """Set ``details['preview']`` so the desktop auto-opens ``path`` in the rail.

    Use this ONLY for genuinely panel-worthy output (a generated HTML card,
    a report). ``path`` should be an absolute filesystem path or an
    ``http(s)://`` URL — anything the desktop's ``normalizePreviewTarget`` can
    resolve. Returns ``details`` for chaining.
    """
    if path:
        details["preview"] = str(path)
    return details


def will_auto_open(details: dict[str, Any]) -> bool:
    """True if ``details`` would trigger the desktop to auto-open a preview.

    Mirrors the desktop's ``structuredPreviewCandidate`` logic: the first of
    :data:`PREVIEW_TRIGGER_KEYS` whose value looks like a URL or path wins.
    Useful in tests to assert a read-only tool won't spam the preview pane.
    """
    for key in PREVIEW_TRIGGER_KEYS:
        value = details.get(key)
        if isinstance(value, str) and _looks_like_target(value):
            return True
    return False


def _looks_like_target(value: str) -> bool:
    """Match the desktop's ``looksLikePreviewTarget`` heuristic."""
    v = value.strip()
    if v.startswith(("http://", "https://", "file://", "/", "./", "../", "~/")):
        return True
    return False
