"""Natural-language policy parser.

Converts a small set of common policy phrasings into :class:`Policy`
objects. The grammar is intentionally narrow — three patterns cover
~90% of the policy expressions users actually type. Phrases that
don't match any pattern raise :class:`ParseError` with a hint.

Supported patterns:

  1. **Amount threshold** —
     ``approve <tool> under <amount> <unit>``
     ``confirm <tool> over <amount> <unit>``
     ``block <tool> over <amount> <unit>``
     ``approve transfers under 0.05 ETH``  (catch-all for "approve")

     Decision mapping:
       - ``approve <tool> under N`` → confirm above N (because the
         policy gate fires when value >= threshold)
       - ``confirm <tool> over N``  → confirm above N
       - ``block <tool> over N``    → block above N

  2. **Rate limit** — ``max <N> <tool> per hour``
     Creates a confirm policy with ``max_per_hour=N``.

  3. **Catch-all by tool** — ``block all <tool>`` /
     ``block any <tool>``
     Creates a block policy with no quantitative gates.

Tool aliases recognized:
  ``transfer / transfers``    → ``transfer``
  ``swap / swaps``            → ``defi_swap``
  ``approval / approvals``    → ``approvals``
  ``stake / staking``         → ``defi_stake``
  ``lend / lending / borrow`` → ``defi_lend``

Token aliases for amount conversion:
  ``ETH``    → 18 decimals (also WETH, MATIC on Polygon)
  ``USDC``   → 6 decimals
  ``USDT``   → 6 decimals
  ``DAI``    → 18 decimals

Anything else falls back to 18 decimals with a hint logged.
"""

from __future__ import annotations

import re
from decimal import Decimal

from clawmes.lib.logger import logger_for
from clawmes.policy.types import Policy

_log = logger_for("policy.parser")


class ParseError(ValueError):
    """Raised when the input doesn't match any supported policy pattern."""


# Tool aliases -------------------------------------------------------------

_TOOL_ALIASES: dict[str, str] = {
    "transfer": "transfer",
    "transfers": "transfer",
    "send": "transfer",
    "sends": "transfer",
    "swap": "defi_swap",
    "swaps": "defi_swap",
    "trade": "defi_swap",
    "trades": "defi_swap",
    "approval": "approvals",
    "approvals": "approvals",
    "approve": "approvals",  # noun form ("approvals") for the tool
    "stake": "defi_stake",
    "staking": "defi_stake",
    "lend": "defi_lend",
    "lending": "defi_lend",
    "borrow": "defi_lend",
    "borrowing": "defi_lend",
    "bridge": "bridge",
    "bridges": "bridge",
}

# Token unit decimals ------------------------------------------------------

_UNIT_DECIMALS: dict[str, int] = {
    "eth": 18,
    "weth": 18,
    "matic": 18,
    "btc": 8,
    "wbtc": 8,
    "usdc": 6,
    "usdt": 6,
    "dai": 18,
}


# Regex patterns -----------------------------------------------------------

_AMOUNT_RE = r"(?P<amount>\d+(?:\.\d+)?)"
_UNIT_RE = r"(?P<unit>[A-Za-z]+)"
_TOOL_RE = r"(?P<tool>[a-z_]+)"

_PATTERN_AMOUNT_THRESHOLD = re.compile(
    rf"^\s*(?P<verb>approve|confirm|block)\s+"
    rf"{_TOOL_RE}\s+(?P<comparator>under|below|less\s+than|over|above|more\s+than)\s+"
    rf"{_AMOUNT_RE}\s*{_UNIT_RE}\s*$",
    re.IGNORECASE,
)

_PATTERN_RATE = re.compile(
    rf"^\s*(?P<verb>max|limit|cap|confirm)\s+(?P<n>\d+)\s+"
    rf"{_TOOL_RE}\s+per\s+(?:hour|hr|h)\s*$",
    re.IGNORECASE,
)

_PATTERN_CATCH_ALL = re.compile(
    rf"^\s*block\s+(?:all|any)\s+{_TOOL_RE}\s*$",
    re.IGNORECASE,
)


# Public entry point -------------------------------------------------------


def parse_policy(text: str) -> Policy:
    """Parse a single natural-language policy expression.

    Returns the constructed :class:`Policy`. Raises :class:`ParseError`
    with an explanatory message when no pattern matches.
    """
    s = text.strip()
    if not s:
        raise ParseError("empty policy expression")

    # Normalize "less than"/"more than"/"per hr" to canonical forms with
    # single spaces — the regexes are tolerant but normalization makes
    # debugging easier.
    s = re.sub(r"\s+", " ", s)

    for parser in (_parse_amount_threshold, _parse_rate_limit, _parse_catch_all):
        result = parser(s)
        if result is not None:
            return result

    raise ParseError(
        f"could not parse {text!r}. Supported patterns:\n"
        '  - "approve <tool> under <amount> <unit>"\n'
        '  - "block <tool> over <amount> <unit>"\n'
        '  - "max <N> <tool> per hour"\n'
        '  - "block all <tool>"'
    )


# Pattern handlers ---------------------------------------------------------


def _parse_amount_threshold(text: str) -> Policy | None:
    m = _PATTERN_AMOUNT_THRESHOLD.match(text)
    if not m:
        return None

    verb = m.group("verb").lower()
    tool_word = m.group("tool").lower()
    comparator = m.group("comparator").lower()
    amount = m.group("amount")
    unit = m.group("unit").lower()

    tool = _resolve_tool(tool_word)
    if tool is None:
        raise ParseError(f"unknown tool {tool_word!r} in policy expression")

    decimals = _resolve_decimals(unit)
    threshold_wei = _to_wei(amount, decimals)

    # Map verb + comparator → decision
    # "approve under N" means: confirm above N (allowed under, asks above)
    # "confirm over N"  means: confirm above N
    # "block over N"    means: block above N
    # "approve over N" / "block under N" / "confirm under N" don't make
    # sense in our gate model — reject.
    is_under = comparator in ("under", "below", "less than", "less  than")
    is_over = comparator in ("over", "above", "more than", "more  than")

    if verb == "approve" and is_under:
        decision = "confirm"
    elif verb == "confirm" and is_over:
        decision = "confirm"
    elif verb == "block" and is_over:
        decision = "block"
    else:
        raise ParseError(
            f"verb/comparator combination {verb!r} + {comparator!r} not supported. "
            'Try "approve <tool> under N" or "block <tool> over N".'
        )

    return Policy(
        name=f"{verb}-{tool}-{comparator.replace(' ', '-')}-{amount}{unit}",
        decision=decision,
        applies_to_tools=(tool,),
        max_amount_wei=threshold_wei,
        description=text,
    )


def _parse_rate_limit(text: str) -> Policy | None:
    m = _PATTERN_RATE.match(text)
    if not m:
        return None

    n = int(m.group("n"))
    tool_word = m.group("tool").lower()
    tool = _resolve_tool(tool_word)
    if tool is None:
        raise ParseError(f"unknown tool {tool_word!r} in rate-limit expression")

    return Policy(
        name=f"rate-{tool}-{n}-per-hour",
        decision="confirm",
        applies_to_tools=(tool,),
        max_per_hour=n,
        description=text,
    )


def _parse_catch_all(text: str) -> Policy | None:
    m = _PATTERN_CATCH_ALL.match(text)
    if not m:
        return None

    tool_word = m.group("tool").lower()
    tool = _resolve_tool(tool_word)
    if tool is None:
        raise ParseError(f"unknown tool {tool_word!r} in catch-all expression")

    return Policy(
        name=f"block-all-{tool}",
        decision="block",
        applies_to_tools=(tool,),
        description=text,
    )


# Helpers ------------------------------------------------------------------


def _resolve_tool(word: str) -> str | None:
    return _TOOL_ALIASES.get(word.lower())


def _resolve_decimals(unit: str) -> int:
    decimals = _UNIT_DECIMALS.get(unit.lower())
    if decimals is not None:
        return decimals
    _log.debug(
        "unknown token unit %r; assuming 18 decimals (override in config if wrong)",
        unit,
    )
    return 18


def _to_wei(amount_str: str, decimals: int) -> int:
    """Convert a human-readable amount to base units (wei).

    Truncates rather than rounds — matches the convention in
    :mod:`clawmes.lib.decimals`.
    """
    quantum = Decimal(10) ** decimals
    return int((Decimal(amount_str) * quantum).to_integral_value(rounding="ROUND_DOWN"))
