"""``/agent`` — natural-language prompt → plan IR + manual confirm.

Compile a free-form prompt into a sequence of clawmes slash commands.
Nothing materializes until the user explicitly confirms — the draft
lives per-sender in memory and ``/agent show`` re-prints it before
state changes.

Pattern: parse → preview → confirm.

  * ``/agent <prompt>``    parse the prompt, store draft, show plan
  * ``/agent show``        re-print the current draft
  * ``/agent confirm``     execute every step in the draft
  * ``/agent cancel``      drop the draft without executing
  * ``/agent examples``    show supported phrasings

We deliberately avoid invoking an LLM here. A regex-based parser
covers the high-frequency intents (buy / DCA / copy / claim / burn /
leaderboard / my_launches / balance) and rejects everything else with
a clear error + examples. An LLM-backed expansion can land later as a
fallback (``/agent --ai <prompt>``) once we have telemetry on what
users actually ask for.

Multi-step prompts join with ``then`` or ``, then``:

  /agent DCA 0.001 ETH of CLAWNCH every hour then follow 0xwhale at 0.001

→ Plan:
   1. /dca add 0xa1F72459...747be 0.001 1h
   2. /copy add 0xwhale 0.001

State changes are gated behind ``/agent confirm`` so a malformed parse
never causes accidental tx submission.
"""

from __future__ import annotations

import re
from typing import Any

# CLAWNCH token address — recognized as a symbol shortcut in prompts.
_CLAWNCH_ADDR = "0xa1F72459dfA10BAD200Ac160eCd78C6b77a747be"
_KNOWN_SYMBOLS = {
    "clawnch": _CLAWNCH_ADDR,
    "$clawnch": _CLAWNCH_ADDR,
}

# Per-sender draft cache. Drafts are intentionally in-memory (no disk
# persistence) — a parse that goes stale across a restart should be
# re-prompted; we never want to materialize a confirmation against a
# state the user can't actually see.
_DRAFTS: dict[str, list[dict[str, Any]]] = {}


# ── regex intent patterns ──────────────────────────────────────────


# We match against lowercased + whitespace-normalized text. Each
# pattern captures the args we need to materialize the slash command.
# The order of patterns matters — more specific matches first.

_INTENT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # DCA: "dca <amount> eth of <token> every <interval>"
    # also: "buy <amount> eth of <token> every <interval>"
    (
        re.compile(
            r"^(?:dca|buy)\s+(?P<amount>[\d.]+)\s*eth\s+of\s+(?P<token>\S+)\s+every\s+(?P<interval>\S+)$"
        ),
        "dca_add",
    ),
    # Copy: "copy <wallet>" or "follow <wallet>" with optional "at N eth"
    (
        re.compile(
            r"^(?:copy|follow)\s+(?P<wallet>0x[a-fA-F0-9]{40})(?:\s+at\s+(?P<amount>[\d.]+)\s*eth)?$"
        ),
        "copy_add",
    ),
    # Buy (one-shot, no "every"): "buy <amount> eth of <token>"
    (
        re.compile(r"^buy\s+(?P<amount>[\d.]+)\s*eth\s+of\s+(?P<token>\S+)$"),
        "buy",
    ),
    # Claim everything: "claim my fees", "claim all", "claim fees"
    (
        re.compile(r"^claim\s+(?:my\s+|all\s+)?fees?$|^claim\s+all$"),
        "claim_all",
    ),
    # Claim one token: "claim <symbol-or-address>"
    (
        re.compile(r"^claim\s+(?P<target>\S+)$"),
        "claim_one",
    ),
    # Burn: "burn N clawnch" / "burn N"
    (
        re.compile(r"^burn\s+(?P<amount>[\d,_]+)(?:\s+clawnch)?$"),
        "burn",
    ),
    # Leaderboard variants
    (
        re.compile(r"^(?:show\s+(?:me\s+)?)?leaderboard$|^top\s+tokens$"),
        "leaderboard_tokens",
    ),
    (
        re.compile(r"^top\s+launchers$|^leaderboard\s+launchers$"),
        "leaderboard_launchers",
    ),
    # Discovery
    (
        re.compile(r"^(?:show\s+(?:me\s+)?)?(?:my\s+)?launches$"),
        "my_launches",
    ),
    (
        re.compile(r"^(?:what'?s|show)\s+(?:my\s+)?balance$|^balance$"),
        "balance",
    ),
]


def _resolve_token_arg(raw: str) -> str:
    """Resolve a token symbol/address into the canonical form for clawmes."""
    raw = raw.lower().lstrip("$")
    if raw in _KNOWN_SYMBOLS:
        return _KNOWN_SYMBOLS[raw]
    return raw  # pass through; the downstream command resolves it


def _parse_one(text: str) -> dict[str, Any] | None:
    """Match ``text`` against intent patterns. Returns a plan step dict or None."""
    text = text.strip().lower()
    for pattern, action in _INTENT_PATTERNS:
        m = pattern.match(text)
        if not m:
            continue
        gd = m.groupdict()
        if action == "dca_add":
            token = _resolve_token_arg(gd["token"])
            return {
                "action": "dca_add",
                "command": "dca",
                "args": f"add {token} {gd['amount']} {gd['interval']}",
                "summary": f"DCA {gd['amount']} ETH of {token} every {gd['interval']}",
            }
        if action == "buy":
            token = _resolve_token_arg(gd["token"])
            return {
                "action": "buy",
                "command": "buy",
                "args": f"{token} {gd['amount']}",
                "summary": f"Buy {gd['amount']} ETH of {token} (quote first; confirm separately)",
            }
        if action == "copy_add":
            amount = gd.get("amount") or "0.001"
            return {
                "action": "copy_add",
                "command": "copy",
                "args": f"add {gd['wallet']} {amount}",
                "summary": f"Follow {gd['wallet']} and copy each buy at {amount} ETH",
            }
        if action == "claim_all":
            return {
                "action": "claim_all",
                "command": "claim",
                "args": "all",
                "summary": "Claim accumulated LP fees on every token you've launched",
            }
        if action == "claim_one":
            return {
                "action": "claim_one",
                "command": "claim",
                "args": gd["target"],
                "summary": f"Claim accumulated LP fees on {gd['target']}",
            }
        if action == "burn":
            amount = gd["amount"].replace(",", "").replace("_", "")
            return {
                "action": "burn",
                "command": "burn",
                "args": amount,
                "summary": f"Burn {int(amount):,} $CLAWNCH for Clanker vault %",
            }
        if action == "leaderboard_tokens":
            return {
                "action": "leaderboard_tokens",
                "command": "leaderboard",
                "args": "",
                "summary": "Show top 10 Clawnch tokens by 24h volume",
            }
        if action == "leaderboard_launchers":
            return {
                "action": "leaderboard_launchers",
                "command": "leaderboard",
                "args": "launchers",
                "summary": "Show top 10 launchers by recent activity",
            }
        if action == "my_launches":
            return {
                "action": "my_launches",
                "command": "my_launches",
                "args": "",
                "summary": "List the tokens this agent has launched",
            }
        if action == "balance":  # pragma: no branch — last entry
            return {
                "action": "balance",
                "command": "balance",
                "args": "",
                "summary": "Show wallet balance",
            }
    return None


def _parse_prompt(prompt: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Split on ``then`` (and ``, then``) and parse each segment.

    Bare commas are NOT treated as segment separators because they
    appear inside numbers (e.g. ``burn 1,000,000 CLAWNCH``). Users
    composing multi-step prompts must say ``then``.
    """
    # Normalize "X, then Y" → "X then Y" so a single split handles both.
    cleaned = re.sub(r",\s*then\b", " then ", prompt, flags=re.IGNORECASE)
    segments = [s.strip() for s in re.split(r"\bthen\b", cleaned, flags=re.IGNORECASE) if s.strip()]
    plan: list[dict[str, Any]] = []
    errors: list[str] = []
    for seg in segments:
        step = _parse_one(seg)
        if step is None:
            errors.append(seg)
        else:
            plan.append(step)
    return plan, errors


# ── command surface ────────────────────────────────────────────────


def _record(name: str, args: str, result: str) -> None:
    try:
        from clawmes.services.command_history import record_command_call

        record_command_call(name, args, result)
    except Exception:  # noqa: BLE001
        pass


async def handle_agent(raw_args: str, *, sender_id: str = "default", **_kwargs: Any) -> str:
    raw = (raw_args or "").strip()
    if not raw:
        out = _render_usage()
    else:
        sub = raw.split()[0].lower()
        if sub == "show":
            out = _cmd_show(sender_id)
        elif sub == "confirm":
            out = await _cmd_confirm(sender_id)
        elif sub == "cancel":
            out = _cmd_cancel(sender_id)
        elif sub == "examples":
            out = _render_examples()
        else:
            # Treat the whole input as the prompt to parse.
            out = _cmd_parse(sender_id, raw)
    _record("agent", raw_args, out)
    return out


def _render_usage() -> str:
    return (
        "Natural-language plan compiler.\n"
        "\n"
        "  /agent <prompt>         Parse the prompt, store as draft, show plan\n"
        "  /agent show             Re-print the current draft\n"
        "  /agent confirm          Execute every step in the draft\n"
        "  /agent cancel           Drop the draft without executing\n"
        "  /agent examples         Show supported phrasings\n"
        "\n"
        "Example:\n"
        "  /agent DCA 0.001 ETH of CLAWNCH every hour then follow 0xWhale… at 0.001\n"
        "  /agent show                # confirm what was parsed\n"
        "  /agent confirm             # materialize the steps"
    )


def _render_examples() -> str:
    return (
        "Supported phrasings (case-insensitive):\n"
        "\n"
        "  DCA / buy schedules:\n"
        "    DCA 0.01 ETH of CLAWNCH every 1h\n"
        "    buy 0.001 ETH of 0xToken… every 4h\n"
        "\n"
        "  Copy-trading:\n"
        "    copy 0xWhale…\n"
        "    follow 0xWhale… at 0.001 eth\n"
        "\n"
        "  One-shot buys:\n"
        "    buy 0.01 ETH of CLAWNCH\n"
        "\n"
        "  Claim LP fees:\n"
        "    claim my fees           (sweeps all your launches)\n"
        "    claim CLAWNCH           (specific token)\n"
        "    claim 0xToken…\n"
        "\n"
        "  Burn:\n"
        "    burn 1,000,000 CLAWNCH\n"
        "    burn 1000000\n"
        "\n"
        "  Discovery:\n"
        "    leaderboard / top tokens\n"
        "    top launchers\n"
        "    show my launches\n"
        "    balance\n"
        "\n"
        "  Multi-step (join with `then` or `,`):\n"
        "    DCA 0.001 ETH of CLAWNCH every hour then claim my fees\n"
        "    buy 0.01 ETH of CLAWNCH, copy 0xWhale… at 0.001"
    )


def _cmd_parse(sender_id: str, prompt: str) -> str:
    plan, errors = _parse_prompt(prompt)
    if not plan and errors:
        return (
            "Couldn't parse the prompt. Segments not understood:\n"
            + "\n".join(f"  - {seg!r}" for seg in errors)
            + "\n\nTry /agent examples for supported phrasings."
        )

    # Multi-step prompts (2+ parsed steps) require HOLDER tier. Single-
    # step prompts stay free so the regex compiler is approachable.
    if len(plan) > 1:
        from clawmes.services.token_gate import Tier, check_tier_or_error

        gate_err = check_tier_or_error(Tier.HOLDER, feature="/agent multi-step prompts")
        if gate_err:
            return gate_err

    _DRAFTS[sender_id] = plan
    lines = [f"Plan parsed ({len(plan)} step(s)):", ""]
    for i, step in enumerate(plan, start=1):
        lines.append(f"  {i}. /{step['command']} {step['args']}")
        lines.append(f"       → {step['summary']}")

    if errors:
        lines.append("")
        lines.append("Could not parse:")
        for seg in errors:
            lines.append(f"  - {seg!r}")

    lines.extend(
        [
            "",
            "Next: /agent confirm to execute, /agent cancel to discard,",
            "or /agent <new prompt> to overwrite the draft.",
        ]
    )
    return "\n".join(lines)


def _cmd_show(sender_id: str) -> str:
    plan = _DRAFTS.get(sender_id)
    if not plan:
        return "No draft. Run /agent <prompt> first."
    lines = [f"Draft for {sender_id} ({len(plan)} step(s)):", ""]
    for i, step in enumerate(plan, start=1):
        lines.append(f"  {i}. /{step['command']} {step['args']}")
        lines.append(f"       → {step['summary']}")
    lines.append("")
    lines.append("Use /agent confirm to execute, /agent cancel to discard.")
    return "\n".join(lines)


def _cmd_cancel(sender_id: str) -> str:
    if sender_id not in _DRAFTS:
        return "No draft to cancel."
    del _DRAFTS[sender_id]
    return "Draft cancelled."


async def _cmd_confirm(sender_id: str) -> str:
    plan = _DRAFTS.get(sender_id)
    if not plan:
        return "No draft to confirm. Run /agent <prompt> first."

    lines = [f"Executing {len(plan)} step(s)..."]
    for i, step in enumerate(plan, start=1):
        result = await _dispatch_step(step, sender_id)
        # Cut long step outputs down for the summary; they're already
        # recorded into command_history individually.
        snippet = result.split("\n", 1)[0][:120]
        lines.append(f"  {i}. /{step['command']} {step['args']}")
        lines.append(f"       → {snippet}")

    # Clear the draft after executing so a follow-up /agent confirm
    # doesn't accidentally re-run.
    del _DRAFTS[sender_id]
    lines.append("")
    lines.append("Draft cleared. See individual command outputs in /history.")
    return "\n".join(lines)


async def _dispatch_step(step: dict[str, Any], sender_id: str) -> str:
    """Invoke the matching slash command handler with the step's args."""
    cmd = step["command"]
    args = step["args"]

    # Lazy imports per dispatch — keeps the agent module light at boot.
    if cmd == "dca":
        from clawmes.commands.dca import handle_dca

        return await handle_dca(args, sender_id=sender_id)
    if cmd == "buy":
        from clawmes.commands.buy import handle_buy

        return await handle_buy(args, sender_id=sender_id)
    if cmd == "copy":
        from clawmes.commands.copy import handle_copy

        return await handle_copy(args, sender_id=sender_id)
    if cmd == "claim":
        from clawmes.commands.claim import handle_claim

        return await handle_claim(args, sender_id=sender_id)
    if cmd == "burn":
        from clawmes.commands.burn import handle_burn

        return await handle_burn(args, sender_id=sender_id)
    if cmd == "leaderboard":
        from clawmes.commands.leaderboard import handle_leaderboard

        return await handle_leaderboard(args)
    if cmd == "my_launches":
        from clawmes.commands.my_launches import handle_my_launches

        return await handle_my_launches(args)
    if cmd == "balance":  # pragma: no branch — last branch covered by tests
        from clawmes.commands.balance import handle_balance

        return await handle_balance(args)
    return f"unknown command in plan: {cmd}"  # pragma: no cover — defensive


def _reset_for_tests() -> None:
    """Clear the in-memory drafts cache. Test-only."""
    _DRAFTS.clear()


def register(ctx) -> None:
    ctx.register_command(
        name="agent",
        handler=handle_agent,
        description="Natural-language plan compiler with manual confirm",
        args_hint="<prompt> | show | confirm | cancel | examples",
    )
