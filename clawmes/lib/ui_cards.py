"""HTML preview cards for the Hermes Desktop side rail.

The desktop's preview pane renders a local ``.html`` file in an Electron
webview when a tool result carries the path under the ``preview`` key (see
:func:`clawmes.lib.ui_artifacts.attach_preview`). This module builds small,
self-contained, **offline** HTML cards — all CSS is inline, no external
resources, every interpolated value is HTML-escaped — so crypto capabilities
get a real visual panel beside the chat instead of a wall of JSON.

Cards are written under ``${HERMES_HOME}/clawmes/cards/`` by
:func:`write_card`, which returns the absolute path to hand to
``attach_preview``.

Builders provided:

* :func:`render_card`     — the branded shell (title + body).
* :func:`kv_section`      — a labelled key/value table.
* :func:`links_section`   — a list of external links.
* :func:`portfolio_card`  — holdings table + total.
* :func:`research_card`   — token research panel.
* :func:`receipt_card`    — a transaction receipt (swap / launch / transfer).
* :func:`connect_card`    — a wallet-pairing card (URI shown big + copyable).

The visual language is intentionally terminal/crypto: near-black surface,
warm clay accent (Clawnch), monospace for addresses and numbers.
"""

from __future__ import annotations

import html
import re
import time
import uuid
from pathlib import Path
from typing import Any

from clawmes.lib.paths import state_dir

# Warm clay accent (Clawnch brand) on a near-black surface.
_ACCENT = "#d97757"
_BG = "#0d0d0f"
_SURFACE = "#161618"
_STROKE = "#2a2a2e"
_TEXT = "#ededed"
_MUTED = "#8a8a8a"

_CSS = f"""
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{
  background: {_BG}; color: {_TEXT};
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
  font-size: 13px; line-height: 1.5; padding: 16px;
}}
.card {{
  background: {_SURFACE}; border: 1px solid {_STROKE};
  border-radius: 12px; overflow: hidden;
  max-width: 560px; margin: 0 auto;
}}
.card-head {{
  padding: 14px 16px; border-bottom: 1px solid {_STROKE};
  display: flex; align-items: baseline; justify-content: space-between; gap: 8px;
}}
.card-title {{ font-size: 15px; font-weight: 650; letter-spacing: -0.01em; }}
.card-sub {{ color: {_MUTED}; font-size: 11px; }}
.card-body {{ padding: 8px 16px 16px; }}
.section {{ margin-top: 14px; }}
.section-h {{
  font-size: 10px; text-transform: uppercase; letter-spacing: 0.09em;
  color: {_MUTED}; margin-bottom: 6px;
}}
table.kv {{ width: 100%; border-collapse: collapse; }}
table.kv td {{ padding: 5px 0; vertical-align: top; border-bottom: 1px solid {_STROKE}; }}
table.kv tr:last-child td {{ border-bottom: none; }}
table.kv td.k {{ color: {_MUTED}; width: 42%; }}
table.kv td.v {{ text-align: right; font-variant-numeric: tabular-nums; }}
.mono {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }}
.pos {{ color: #5cba7d; }}
.neg {{ color: #e0686a; }}
.accent {{ color: {_ACCENT}; }}
a {{ color: {_ACCENT}; text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
.links a {{ display: block; padding: 6px 0; border-bottom: 1px solid {_STROKE}; }}
.links a:last-child {{ border-bottom: none; }}
.uri {{
  background: {_BG}; border: 1px solid {_STROKE}; border-radius: 8px;
  padding: 12px; word-break: break-all; font-size: 11px; margin-top: 4px;
}}
.qr {{ background: #fff; padding: 12px; border-radius: 8px; display: inline-block; }}
.qr svg {{ display: block; width: 200px; height: 200px; }}
.copy {{
  margin-top: 10px; background: {_ACCENT}; color: #1a1a1a; border: none;
  border-radius: 8px; padding: 8px 14px; font-weight: 650; cursor: pointer;
}}
.foot {{ color: {_MUTED}; font-size: 10px; padding: 10px 16px; border-top: 1px solid {_STROKE}; }}
"""


def _esc(value: Any) -> str:
    """HTML-escape any value for safe interpolation into a webview."""
    return html.escape(str(value), quote=True)


def render_card(title: str, body_html: str, *, subtitle: str = "", footer: str = "") -> str:
    """Wrap ``body_html`` in the branded card shell. Returns a full HTML doc."""
    sub = f'<span class="card-sub">{_esc(subtitle)}</span>' if subtitle else ""
    foot = f'<div class="foot">{_esc(footer)}</div>' if footer else ""
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{_esc(title)}</title><style>{_CSS}</style></head><body>"
        f'<div class="card"><div class="card-head">'
        f'<span class="card-title">{_esc(title)}</span>{sub}</div>'
        f'<div class="card-body">{body_html}</div>{foot}</div>'
        "</body></html>"
    )


def kv_section(heading: str, rows: list[tuple[str, str]]) -> str:
    """A labelled key/value table. ``value`` cells may contain pre-built HTML.

    Keys are escaped; values are NOT (so callers can pass ``mono``/colour spans)
    — callers must escape any untrusted value text themselves, or use
    :func:`kv_text_row` style escaping at the call site.
    """
    if not rows:
        return ""
    body = "".join(f'<tr><td class="k">{_esc(k)}</td><td class="v">{v}</td></tr>' for k, v in rows)
    return (
        f'<div class="section"><div class="section-h">{_esc(heading)}</div>'
        f'<table class="kv">{body}</table></div>'
    )


def links_section(heading: str, links: list[tuple[str, str]]) -> str:
    """A list of external links: ``[(label, url), …]``. Empty links are dropped."""
    valid = [(label, url) for label, url in links if url]
    if not valid:
        return ""
    body = "".join(f'<a href="{_esc(url)}">{_esc(label)} \u2192</a>' for label, url in valid)
    return (
        f'<div class="section"><div class="section-h">{_esc(heading)}</div>'
        f'<div class="links">{body}</div></div>'
    )


def _mono(value: Any) -> str:
    """Escaped monospace span."""
    return f'<span class="mono">{_esc(value)}</span>'


def portfolio_card(
    *,
    address: str,
    chain: str,
    total_usd: float | None,
    holdings: list[dict[str, Any]],
) -> str:
    """Render a portfolio dashboard card.

    ``holdings`` items use keys ``symbol``, ``amount`` and optional ``usd``.
    """
    total = f"${total_usd:,.2f}" if total_usd is not None else "\u2014"
    rows = [("Total value", f'<span class="accent">{_esc(total)}</span>')]
    for h in holdings:
        sym = _esc(h.get("symbol", "?"))
        amt = _esc(h.get("amount", ""))
        usd = h.get("usd")
        usd_str = f" · ${usd:,.2f}" if isinstance(usd, (int, float)) else ""
        rows.append((sym, f"{_mono(amt)}{_esc(usd_str)}"))
    body = kv_section("Holdings", rows)
    return render_card(
        "Portfolio",
        body,
        subtitle=f"{_esc(chain)} · {address[:6]}\u2026{address[-4:]}"
        if len(address) >= 10
        else _esc(address),
        footer="Generated by clawmes",
    )


def research_card(
    *,
    symbol: str,
    rows: list[tuple[str, str]],
    links: list[tuple[str, str]],
) -> str:
    """Render a token research panel: stats table + market links."""
    safe_rows = [(k, _mono(v)) for k, v in rows]
    body = kv_section("Overview", safe_rows) + links_section("Markets", links)
    return render_card(
        f"Research: {symbol}",
        body,
        subtitle="token analysis",
        footer="Not financial advice · generated by clawmes",
    )


def receipt_card(
    *,
    title: str,
    rows: list[tuple[str, str]],
    links: list[tuple[str, str]],
) -> str:
    """Render a transaction receipt card (swap / launch / transfer)."""
    safe_rows = [(k, _mono(v)) for k, v in rows]
    body = kv_section("Details", safe_rows) + links_section("Links", links)
    return render_card(title, body, subtitle="transaction", footer="generated by clawmes")


def _qr_svg(data: str) -> str:
    """Return an inline SVG QR encoding ``data`` (no Pillow; pure-Python).

    Uses qrcode's ``SvgPathImage`` factory so there is no Pillow / C-extension
    dependency. The SVG is returned as a raw string for direct embedding into a
    card. Returns ``""`` on any failure (e.g. data too large to encode) so the
    card still renders with the copyable URI.
    """
    try:
        import io

        import qrcode
        import qrcode.image.svg

        img = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
        buf = io.BytesIO()
        img.save(buf)
        return buf.getvalue().decode("utf-8")
    except Exception:  # noqa: BLE001 — QR is best-effort; fall back to URI only
        return ""


def connect_card(*, uri: str) -> str:
    """Render a wallet-pairing card with a scannable QR + copyable URI.

    The QR (black on a white tile so wallet scanners read it against the dark
    card) encodes the WalletConnect URI; the URI is also shown in a copyable
    block as a fallback for wallets that prefer paste.
    """
    safe_uri = _esc(uri)
    qr = _qr_svg(uri)
    qr_block = (
        '<div class="section"><div class="section-h">Scan with your wallet</div>'
        f'<div class="qr">{qr}</div></div>'
        if qr
        else ""
    )
    body = (
        f"{qr_block}"
        '<div class="section"><div class="section-h">WalletConnect URI</div>'
        f'<div class="uri mono" id="uri">{safe_uri}</div>'
        '<button class="copy" onclick="navigator.clipboard&&navigator.clipboard.writeText('
        "document.getElementById('uri').textContent)\">Copy URI</button>"
        "</div>"
        '<div class="section"><div class="section-h">How to connect</div>'
        "<div>Scan the QR, or open your wallet \u2192 WalletConnect \u2192 paste the URI. "
        "The pairing request appears in your wallet.</div></div>"
    )
    return render_card(
        "Connect wallet",
        body,
        subtitle="WalletConnect v2",
        footer="URI is single-use and expires shortly",
    )


def write_card(html_str: str, name: str) -> Path:
    """Write a card to ``${HERMES_HOME}/clawmes/cards/`` and return its path.

    The filename is slugified from ``name`` and suffixed with a timestamp plus
    a short random token, so even cards written within the same millisecond
    (e.g. rapid tool calls) never clobber each other. Returns the absolute path
    to pass to ``json_result(preview=...)``.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "card"
    suffix = f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}"
    path = state_dir("cards") / f"{slug}-{suffix}.html"
    path.write_text(html_str, encoding="utf-8")
    return path
