"""``@premium_feature`` decorator and supporting helpers.

Wraps a callable so the premium gate runs before the underlying
function. If the active wallet's tier is sufficient (or a valid
one-shot burn token is present), the call goes through unchanged.
Otherwise the wrapper returns a structured ``premium_required``
error result with the cost + upgrade paths.

Why a decorator + a registry instead of inline checks: most premium
features are tools (return ``json_result`` / ``error_result`` strings)
or commands (return plain strings). The decorator unifies the gate
shape so the LLM and command handler see the same denial format —
predictable parsing, predictable retry behavior.

Usage:

.. code-block:: python

    from clawmes.lib.premium import premium_feature

    @premium_feature(feature_id="bv7x_oracle_premium")
    def bv7x_oracle(args, **kwargs):
        ...

The decorator also surfaces the registry — :func:`registered_features`
returns the set of decorated features. Tests assert this matches
:data:`clawmes.lib.clawnch.FEATURES` so we can't ship a feature
catalog out of sync with the actual code.
"""

from __future__ import annotations

import functools
import inspect
import json
from collections.abc import Callable
from typing import Any, TypeVar

from clawmes.lib import clawnch as clawnch_const
from clawmes.lib.logger import logger_for

_log = logger_for("lib.premium")

# Function-level registry of features wrapped by the decorator. Tests
# assert this is the exact same set as ``clawnch.FEATURES`` so the
# catalog and the implementation can't drift apart.
_REGISTERED: set[str] = set()

F = TypeVar("F", bound=Callable[..., Any])


def registered_features() -> frozenset[str]:
    """Return the set of feature IDs wrapped by ``@premium_feature``."""
    return frozenset(_REGISTERED)


def _gate_denial(feature_id: str) -> dict[str, Any]:
    """Build the dict body of a ``premium_required`` denial.

    Includes the required tier, a human-readable label, the burn cost
    (when published), and the two unlock paths.
    """
    feature = clawnch_const.FEATURES.get(feature_id, {})
    cost = clawnch_const.burn_price(feature_id)
    body: dict[str, Any] = {
        "feature_id": feature_id,
        "label": feature.get("label", feature_id),
        "required_tier": feature.get("tier", "pro"),
        "unlock_paths": [
            {
                "type": "stake",
                "url": "https://clawn.ch/stake",
                "summary": (
                    f"Stake CLAWNCH at Bronze tier or higher; "
                    f"≥{clawnch_const.PRO_THRESHOLD:,} weighted unlocks Pro, "
                    f"≥{clawnch_const.MAX_THRESHOLD:,} weighted unlocks Max."
                ),
            },
        ],
    }
    if cost is not None:
        body["unlock_paths"].append(
            {
                "type": "burn",
                "url": f"https://clawn.ch/burn?feature={feature_id}",
                "cost_clawnch": cost,
                "summary": (
                    f"One-shot: burn {cost:,} CLAWNCH to {clawnch_const.BURN_ADDRESS}. "
                    f"Sign the tx, then call `/burn_and_call {feature_id} <tx_hash>` "
                    f"to redeem."
                ),
            }
        )
    return body


def _gate_text(feature_id: str) -> str:
    """Human-readable rendering of the denial, included in tool-result content."""
    body = _gate_denial(feature_id)
    lines = [
        f"`{feature_id}` requires Clawnch premium ({body['required_tier']}).",
        f"  {body['label']}",
        "",
        "Unlock paths:",
    ]
    for path in body["unlock_paths"]:
        if path["type"] == "stake":
            lines.append(f"  • Stake: {path['url']}  — {path['summary']}")
        elif path["type"] == "burn":
            lines.append(f"  • Burn: {path['url']}  — {path['cost_clawnch']:,} CLAWNCH one-shot")
    return "\n".join(lines)


def _denial_envelope(feature_id: str, *, tool_shape: bool) -> Any:
    """Wrap a denial in the appropriate response envelope.

    Tools return JSON strings (the tool-result shape). Commands return
    plain strings (rendered straight into the channel). ``tool_shape``
    picks which envelope to use.
    """
    body = _gate_denial(feature_id)
    text = _gate_text(feature_id)
    if tool_shape:
        return json.dumps(
            {
                "content": [{"type": "text", "text": text}],
                "details": body,
                "isError": True,
            }
        )
    return text


def gate(feature_id: str, *, tool_shape: bool = True) -> str | None:
    """Check premium access for ``feature_id``; return ``None`` on grant.

    Use this when a single tool dispatches multiple sub-actions and only
    some are premium-gated (the ``@premium_feature`` decorator gates the
    whole function, which is too coarse). When access is denied, the
    return value is the same envelope the decorator would produce —
    callers just return it directly.

    Raises ``ValueError`` if ``feature_id`` isn't in the catalog (same
    fail-fast posture as the decorator).
    """
    if feature_id not in clawnch_const.FEATURES:
        raise ValueError(
            f"gate: '{feature_id}' is not in clawmes.lib.clawnch.FEATURES; add it to the catalog."
        )
    if _allow(feature_id):
        return None
    return _denial_envelope(feature_id, tool_shape=tool_shape)


def premium_feature(
    *,
    feature_id: str,
    tool_shape: bool = True,
) -> Callable[[F], F]:
    """Gate a tool or command on the wallet's Clawnch premium tier.

    Parameters
    ----------
    feature_id:
        Stable identifier listed in :data:`clawmes.lib.clawnch.FEATURES`.
        Determines the required tier and (when published) the per-call
        burn price.
    tool_shape:
        ``True`` (default) — denial returns a JSON tool-result string
        with ``isError = true``. Use for ``@write_tool`` / ``@read_tool``
        decorated callables. ``False`` — denial returns a plain
        human-readable string; use for slash commands.

    Raises
    ------
    ValueError
        At decoration time, if ``feature_id`` isn't in the catalog.
        We want to fail fast at import — a typo'd feature ID would
        otherwise present as a silent "always-denied" path that's
        hard to spot.
    """
    if feature_id not in clawnch_const.FEATURES:
        raise ValueError(
            f"premium_feature: '{feature_id}' is not in clawmes.lib.clawnch.FEATURES; "
            f"add it to the catalog before decorating."
        )

    def decorate(fn: F) -> F:
        _REGISTERED.add(feature_id)

        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def aw(*args: Any, **kwargs: Any) -> Any:
                if _allow(feature_id):
                    return await fn(*args, **kwargs)
                return _denial_envelope(feature_id, tool_shape=tool_shape)

            return aw  # type: ignore[return-value]

        @functools.wraps(fn)
        def sw(*args: Any, **kwargs: Any) -> Any:
            if _allow(feature_id):
                return fn(*args, **kwargs)
            return _denial_envelope(feature_id, tool_shape=tool_shape)

        return sw  # type: ignore[return-value]

    return decorate


def _allow(feature_id: str) -> bool:
    """``True`` when the active wallet has access to ``feature_id``.

    Defensive — any service-lookup error logs and returns ``False``
    rather than letting an exception escape the gate. Better to fail
    closed (premium denied) than to bypass the gate on a broken
    service init.
    """
    try:
        from clawmes.services.clawnch_premium import get_clawnch_premium_service

        return get_clawnch_premium_service().has_access(feature_id)
    except Exception:  # noqa: BLE001
        _log.exception("premium gate raised for %s — failing closed", feature_id)
        return False
