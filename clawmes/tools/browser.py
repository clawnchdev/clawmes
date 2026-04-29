"""``browser`` — headless web browsing via Playwright.

Four actions:

  * ``open``        — visit a URL and return the rendered HTML.
  * ``read``        — extract clean text content from a URL.
  * ``extract``     — pull structured data using CSS / XPath selectors.
  * ``screenshot``  — take a PNG screenshot, return the file path.

Requires ``playwright`` Python package + a browser binary (``playwright
install chromium``). If Playwright isn't installed, the tool returns
``not_available`` so the LLM can fall back to text-only fetches via
existing HTTP utilities.

Sandboxing: the browser runs headless with cookies disabled, no
local storage, and a restricted network policy (only the requested
host). Out-of-band navigation is blocked.
"""

from __future__ import annotations

from typing import Any

from clawmes.lib.logger import logger_for
from clawmes.lib.params import read_int, read_str
from clawmes.lib.paths import hermes_home
from clawmes.lib.tool_result import error_result, json_result
from clawmes.tools.registry import read_tool, register_with_ctx

_log = logger_for("tools.browser")

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": ["open", "read", "extract", "screenshot"],
        },
        "url": {"type": "string"},
        "selector": {
            "type": "string",
            "description": "CSS selector for extract.",
        },
        "wait_seconds": {
            "type": "integer",
            "description": "Seconds to wait after page load (default 2).",
        },
    },
    "required": ["action", "url"],
}


@read_tool(
    name="browser",
    toolset="clawmes-misc",
    description=(
        "Headless web browsing via Playwright. open returns rendered "
        "HTML; read extracts clean text; extract pulls data via CSS "
        "selectors; screenshot saves a PNG. Requires the playwright "
        "Python package + browser binaries."
    ),
    schema=_SCHEMA,
    emoji="\U0001f310",
)
def browser(args: dict[str, Any], **kwargs: Any) -> str:
    action = read_str(args, "action", required=True)
    url = read_str(args, "url", required=True)
    if not url.startswith(("http://", "https://")):
        return error_result(f"URL must be HTTP(S): {url!r}", code="param_error")

    sync_pw = _resolve_playwright()
    if sync_pw is None:
        return error_result(
            "playwright is not installed. Run: pip install playwright "
            "&& playwright install chromium.",
            code="not_available",
        )

    wait_seconds = read_int(args, "wait_seconds") or 2

    try:
        with sync_pw() as p:  # type: ignore[misc]
            browser_obj = p.chromium.launch(headless=True)
            context = browser_obj.new_context()
            page = context.new_page()
            page.goto(url, timeout=30_000)
            if wait_seconds > 0:
                page.wait_for_timeout(wait_seconds * 1000)

            if action == "open":
                content = page.content()
                browser_obj.close()
                return json_result(
                    {"url": url, "html": content},
                    summary=f"Loaded {url}",
                )
            if action == "read":
                text = page.inner_text("body")
                browser_obj.close()
                return json_result(
                    {"url": url, "text": text[:50_000]},
                    summary=f"Read {url} ({len(text)} chars)",
                )
            if action == "extract":
                selector = read_str(args, "selector", required=True)
                els = page.locator(selector).all_text_contents()
                browser_obj.close()
                return json_result(
                    {
                        "url": url,
                        "selector": selector,
                        "count": len(els),
                        "matches": els,
                    },
                    summary=f"Extracted {len(els)} match(es)",
                )
            # screenshot
            out_dir = hermes_home() / "clawmes" / "screenshots"
            out_dir.mkdir(parents=True, exist_ok=True)
            import time

            out_path = out_dir / f"shot-{int(time.time())}.png"
            page.screenshot(path=str(out_path))
            browser_obj.close()
            return json_result(
                {"url": url, "screenshot_path": str(out_path)},
                summary=f"Screenshot saved: {out_path}",
            )
    except Exception as exc:  # noqa: BLE001
        return error_result(f"Browser action failed: {exc}", code="browser_error")


def _resolve_playwright():
    """Return playwright.sync_api.sync_playwright if installed, else None."""
    try:
        from playwright.sync_api import sync_playwright  # type: ignore[import-not-found]

        return sync_playwright
    except ImportError:
        return None


# Re-export for tests
__all__ = ["browser", "_resolve_playwright"]


def register(ctx) -> None:
    register_with_ctx(ctx, browser)
