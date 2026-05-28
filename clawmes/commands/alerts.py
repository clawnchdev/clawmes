"""``/alerts`` — price + wallet-activity alerts.

Notification-only triggers. Two alert types:

  * ``price <token> <above|below> <usd>`` — fires when the token's
    USD price crosses the threshold. Polls ``defi_price`` on the
    service tick (~60s).
  * ``wallet <address>`` — fires on any new ERC-20 receipt to the
    watched wallet. Polls Basescan's ``account.tokentx`` endpoint,
    same pattern as ``/copy``.

Alerts do **not** submit transactions or move funds. They just record
a fired-event on the alert's history; when the user runs
``/alerts list`` or ``/alerts history <id>`` they see what fired and
when. Notification delivery to Telegram / Slack / etc. happens via
the Hermes channel layer reading clawmes command history (out of
scope for this command).

Surface:

  * ``/alerts add price <token> <above|below> <usd>``
  * ``/alerts add wallet <address>``
  * ``/alerts list``
  * ``/alerts pause <id>`` / ``resume`` / ``cancel`` / ``edit``
  * ``/alerts tick``  — manual tick (testing)
  * ``/alerts status``  — global summary + service health
  * ``/alerts history <id>``

Storage: ``${HERMES_HOME}/clawmes/alerts/alerts.json``.

State machine: alerts auto-deactivate once they fire (``status: 'fired'``)
so a price crossing doesn't repeatedly notify on every subsequent tick.
Wallet alerts re-arm after each fire so an active wallet keeps notifying
on each new tx.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from clawmes.lib.http import http_get

_BASESCAN_BASE = "https://api.basescan.org/api"


# ── state I/O ───────────────────────────────────────────────────────


def _alerts_path() -> Path:
    from clawmes.lib.paths import state_dir

    return state_dir("alerts") / "alerts.json"


def _load_state() -> dict[str, Any]:
    path = _alerts_path()
    if not path.exists():
        return {"alerts": []}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {"alerts": []}
    if not isinstance(data, dict) or not isinstance(data.get("alerts"), list):
        return {"alerts": []}
    return data


def _save_state(state: dict[str, Any]) -> None:
    path = _alerts_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now_epoch() -> int:
    return int(time.time())


def _new_id() -> str:
    return f"alert_{uuid.uuid4().hex[:10]}"


def _short(value: str) -> str:
    if not isinstance(value, str) or len(value) <= 12:
        return str(value)
    return f"{value[:6]}…{value[-4:]}"


def _record(name: str, args: str, result: str) -> None:
    try:
        from clawmes.services.command_history import record_command_call

        record_command_call(name, args, result)
    except Exception:  # noqa: BLE001
        pass


# ── command dispatch ───────────────────────────────────────────────


async def handle_alerts(raw_args: str, *, sender_id: str = "default", **_kwargs: Any) -> str:
    raw = (raw_args or "").strip()
    if not raw:
        out = _render_usage()
    else:
        parts = raw.split()
        sub = parts[0].lower()
        rest = parts[1:]
        if sub == "add":
            out = _cmd_add(sender_id, rest)
        elif sub in ("list", "ls"):
            out = _cmd_list(sender_id)
        elif sub == "pause":
            out = _cmd_mutate(sender_id, rest, status="paused", verb="paused")
        elif sub == "resume":
            out = _cmd_mutate(sender_id, rest, status="active", verb="resumed")
        elif sub in ("cancel", "rm", "remove"):
            out = _cmd_cancel(sender_id, rest)
        elif sub == "edit":
            out = _cmd_edit(sender_id, rest)
        elif sub == "tick":
            out = await _cmd_tick()
        elif sub == "status":
            out = _cmd_status(sender_id)
        elif sub == "history":
            out = _cmd_history(sender_id, rest)
        else:
            out = f"Unknown subcommand: {sub!r}\n\n" + _render_usage()
    _record("alerts", raw_args, out)
    return out


def _render_usage() -> str:
    return (
        "Price + wallet alerts. Notification-only; no transactions submitted.\n"
        "\n"
        "  /alerts add price <token> <above|below> <usd>\n"
        "                          Fire when token price crosses threshold\n"
        "  /alerts add wallet <address>\n"
        "                          Fire on any new ERC-20 receipt to the address\n"
        "  /alerts list            Show your alerts + last fire time\n"
        "  /alerts pause <id>      Suspend without removing\n"
        "  /alerts resume <id>     Re-arm\n"
        "  /alerts cancel <id>     Remove entirely\n"
        "  /alerts edit <id> <field> <value>\n"
        "                          Change one field on a price alert\n"
        "  /alerts status          Global summary + service health\n"
        "  /alerts history <id>    Past fires for one alert\n"
        "  /alerts tick            Manually poll (testing)\n"
        "\n"
        "Example:\n"
        "  /alerts add price CLAWNCH above 0.00002\n"
        "  /alerts add wallet 0xWhale…"
    )


# ── /alerts add ─────────────────────────────────────────────────────


def _cmd_add(sender_id: str, parts: list[str]) -> str:
    if not parts:
        return (
            "Usage: /alerts add price <token> <above|below> <usd>\n"
            "   or: /alerts add wallet <address>"
        )

    # Free-tier cap: 3 active alerts per sender.
    from clawmes.services.token_gate import check_cap_or_error

    state_check = _load_state()
    active_mine = sum(
        1
        for a in state_check["alerts"]
        if a.get("sender_id") == sender_id and a.get("status") == "active"
    )
    cap_err = check_cap_or_error("alerts", active_count=active_mine, feature="alert")
    if cap_err:
        return cap_err

    kind = parts[0].lower()
    pos, flags = _split_alert_flags(parts[1:])
    webhook_url = flags.get("webhook")
    if webhook_url:
        from clawmes.services.token_gate import Tier, check_tier_or_error

        gate_err = check_tier_or_error(Tier.HOLDER, feature="/alerts --webhook delivery")
        if gate_err:
            return gate_err
        if not (webhook_url.startswith("http://") or webhook_url.startswith("https://")):
            return f"--webhook must be an http(s):// URL (got {webhook_url!r})."

    if kind == "price":
        return _add_price(sender_id, pos, webhook_url=webhook_url)
    if kind == "wallet":
        return _add_wallet(sender_id, pos, webhook_url=webhook_url)
    return f"Unknown alert type {kind!r}. Use 'price' or 'wallet'."


def _split_alert_flags(parts: list[str]) -> tuple[list[str], dict[str, str]]:
    """Same shape as ``/copy._split_flags`` — value flags consume the next token
    unless it's another flag, in which case the current flag is treated as bare."""
    positional: list[str] = []
    flags: dict[str, str] = {}
    i = 0
    while i < len(parts):
        tok = parts[i]
        if tok.startswith("--"):
            name = tok[2:]
            next_tok = parts[i + 1] if i + 1 < len(parts) else None
            if next_tok is not None and not next_tok.startswith("--"):
                flags[name] = next_tok
                i += 2
            else:
                flags[name] = ""
                i += 1
        else:
            positional.append(tok)
            i += 1
    return positional, flags


def _add_price(sender_id: str, parts: list[str], *, webhook_url: str | None = None) -> str:
    if len(parts) < 3:
        return "Usage: /alerts add price <token> <above|below> <usd> [--webhook <url>]"
    token, direction, usd_raw = parts[0], parts[1].lower(), parts[2]
    if direction not in ("above", "below"):
        return f"direction must be 'above' or 'below' (got {direction!r})."
    try:
        threshold_usd = float(usd_raw)
    except ValueError:
        return f"usd threshold must be a number (got {usd_raw!r})."
    if threshold_usd <= 0:
        return f"usd threshold must be positive (got {threshold_usd})."

    state = _load_state()
    alert_id = _new_id()
    alert = {
        "id": alert_id,
        "sender_id": sender_id,
        "type": "price",
        "token": token,
        "direction": direction,
        "threshold_usd": threshold_usd,
        "webhook_url": webhook_url,
        "status": "active",
        "created_at": _now_iso(),
        "fires": [],
    }
    state["alerts"].append(alert)
    _save_state(state)
    webhook_line = f"  Webhook:   {webhook_url}\n" if webhook_url else ""
    return (
        f"Alert added: {alert_id}\n"
        f"  Type:      price\n"
        f"  Token:     {token}\n"
        f"  Trigger:   price {direction} ${threshold_usd}\n"
        + webhook_line
        + "\n"
        + "The alert scheduler polls prices on the registry cadence (~60s)\n"
        + "and records a fire when the threshold is crossed. Use /alerts list\n"
        + "to see all your alerts."
    )


def _add_wallet(sender_id: str, parts: list[str], *, webhook_url: str | None = None) -> str:
    if not parts:
        return "Usage: /alerts add wallet <address> [--webhook <url>]"
    address = parts[0]
    if not (address.startswith("0x") and len(address) == 42):
        return f"wallet must be a 0x… address (got {address!r})."

    # Seed last_seen_block so the first tick doesn't replay history.
    last_block = _current_block_height() - 10
    if last_block < 0:
        last_block = 0

    state = _load_state()
    alert_id = _new_id()
    alert = {
        "id": alert_id,
        "sender_id": sender_id,
        "type": "wallet",
        "wallet": address.lower(),
        "webhook_url": webhook_url,
        "status": "active",
        "created_at": _now_iso(),
        "last_seen_block": last_block,
        "fires": [],
    }
    state["alerts"].append(alert)
    _save_state(state)
    return (
        f"Alert added: {alert_id}\n"
        f"  Type:      wallet\n"
        f"  Wallet:    {address}\n"
        f"  Trigger:   any new ERC-20 receipt\n"
        f"  Last block: {last_block}\n"
        "\n"
        "The alert scheduler polls Basescan on the registry cadence.\n"
        "Each detected receipt records a fire on this alert's history."
    )


# ── /alerts list / mutate / cancel ─────────────────────────────────


def _cmd_list(sender_id: str) -> str:
    state = _load_state()
    mine = [a for a in state["alerts"] if a.get("sender_id") == sender_id]
    if not mine:
        return "No alerts. Add one with /alerts add ..."
    lines = [f"Alerts for {sender_id} ({len(mine)}):", ""]
    for a in mine:
        fires = len(a.get("fires", []))
        if a.get("type") == "price":
            trigger = f"price {a.get('direction')} ${a.get('threshold_usd')}"
            target = a.get("token", "?")
        else:
            trigger = "wallet receipt"
            target = _short(a.get("wallet", "?"))
        lines.append(
            f"  {a['id']}  {a.get('status'):<8s}"
            f"  {a.get('type'):<7s}  {target}"
            f"  {trigger}  ({fires} fires)"
        )
    return "\n".join(lines)


def _cmd_mutate(sender_id: str, parts: list[str], *, status: str, verb: str) -> str:
    if not parts:
        return f"Usage: /alerts {verb.removesuffix('d')} <id>"
    aid = parts[0]
    state = _load_state()
    for a in state["alerts"]:
        if a.get("id") == aid and a.get("sender_id") == sender_id:
            a["status"] = status
            _save_state(state)
            return f"Alert {aid} {verb}."
    return f"No alert found with id {aid!r}."


def _cmd_cancel(sender_id: str, parts: list[str]) -> str:
    if not parts:
        return "Usage: /alerts cancel <id>"
    aid = parts[0]
    state = _load_state()
    before = len(state["alerts"])
    state["alerts"] = [
        a for a in state["alerts"] if not (a.get("id") == aid and a.get("sender_id") == sender_id)
    ]
    if len(state["alerts"]) == before:
        return f"No alert found with id {aid!r}."
    _save_state(state)
    return f"Cancelled alert {aid}."


# ── /alerts edit ───────────────────────────────────────────────────


def _cmd_edit(sender_id: str, parts: list[str]) -> str:
    if len(parts) < 3:
        return (
            "Usage: /alerts edit <id> <field> <value>\n"
            "Price alert fields: direction, threshold_usd, token"
        )
    aid, field, value = parts[0], parts[1], parts[2]
    state = _load_state()
    alert = _find(state, aid, sender_id)
    if alert is None:
        return f"No alert found with id {aid!r}."

    # Wallet alerts have no editable fields beyond pause/resume — the
    # surface is intentionally narrow. Tell the user explicitly.
    if alert.get("type") != "price":
        return "Only price alerts are editable. Cancel + re-add to change a wallet alert."

    if field == "direction":
        if value.lower() not in ("above", "below"):
            return f"direction must be 'above' or 'below' (got {value!r})."
        alert["direction"] = value.lower()
    elif field == "threshold_usd":
        try:
            v = float(value)
        except ValueError:
            return f"threshold_usd must be a number (got {value!r})."
        if v <= 0:
            return f"threshold_usd must be positive (got {v})."
        alert["threshold_usd"] = v
    elif field == "token":
        alert["token"] = value
    else:
        return f"Unknown field {field!r}. Editable: direction, threshold_usd, token."

    _save_state(state)
    return f"Alert {aid}: {field} = {value}."


def _find(state: dict[str, Any], aid: str, sender_id: str) -> dict[str, Any] | None:
    for a in state["alerts"]:
        if a.get("id") == aid and a.get("sender_id") == sender_id:
            return a
    return None


# ── /alerts tick — the watcher ─────────────────────────────────────


async def _cmd_tick() -> str:
    n, lines = _run_due_with_lines()
    if n == 0:
        return "No alerts fired."
    return "\n".join([f"Fired {n} alert(s):"] + lines)


def _run_due_sync() -> int:
    n, _ = _run_due_with_lines()
    return n


def _run_due_with_lines() -> tuple[int, list[str]]:
    state = _load_state()
    lines: list[str] = []
    total = 0
    for alert in state["alerts"]:
        if alert.get("status") != "active":
            continue
        try:
            fired = _check_alert(alert)
            if fired:
                lines.append(
                    f"  {alert['id']}  {alert.get('type')}  fired: {fired.get('detail', '')}"
                )
                alert["fires"].append({"at": _now_iso(), **fired})
                # Price alerts auto-deactivate so we don't repeatedly
                # notify on every tick after the threshold is crossed.
                # Wallet alerts re-arm naturally because
                # last_seen_block advances past the seen tx.
                if alert.get("type") == "price":
                    alert["status"] = "fired"
                # HOLDER-tier webhook delivery — POST the fire payload
                # to the configured URL. Failures are recorded but
                # don't roll back the fire itself.
                webhook_url = alert.get("webhook_url")
                if webhook_url:
                    delivery = _post_webhook(webhook_url, alert, fired)
                    alert["fires"][-1]["webhook"] = delivery
                total += 1
        except Exception as exc:  # noqa: BLE001
            lines.append(f"  {alert['id']}  error: {exc}")
    if total > 0 or any(lines):
        _save_state(state)
    return total, lines


def _post_webhook(url: str, alert: dict[str, Any], fired: dict[str, Any]) -> dict[str, Any]:
    """POST the fire payload to ``url``. Returns a delivery-status dict.

    Never raises. Webhook failure must not block the alert tick.
    """
    try:
        import urllib.request as _req

        body = json.dumps(
            {
                "alert_id": alert.get("id"),
                "sender_id": alert.get("sender_id"),
                "type": alert.get("type"),
                "fired_at": _now_iso(),
                "detail": fired.get("detail"),
                "fire": fired,
            }
        ).encode("utf-8")
        req = _req.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "User-Agent": "clawmes-alerts/1.0",
            },
        )
        with _req.urlopen(req, timeout=10.0) as resp:  # noqa: S310 — user-provided URL
            status_code = resp.getcode()
        return {"status": "ok", "http_status": status_code}
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "detail": str(exc)}


def _check_alert(alert: dict[str, Any]) -> dict[str, Any] | None:
    """Return a fire-event dict if the alert should fire, else None."""
    if alert.get("type") == "price":
        return _check_price_alert(alert)
    if alert.get("type") == "wallet":
        return _check_wallet_alert(alert)
    return None


def _check_price_alert(alert: dict[str, Any]) -> dict[str, Any] | None:
    """Hit defi_price for a USD quote, compare to threshold, return fire."""
    try:
        from clawmes.tools.defi_price import defi_price

        raw = defi_price(
            {
                "action": "quote",
                "symbol": alert["token"],
                "quote_currency": "USD",
            }
        )
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "detail": f"price fetch: {exc}"}
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return {"status": "error", "detail": f"bad price response: {raw}"}
    if payload.get("isError"):
        msg = payload.get("content", [{}])[0].get("text", "price failed")
        return {"status": "error", "detail": msg}
    details = payload.get("details") or {}
    price_usd = details.get("price_usd") or details.get("price") or 0
    try:
        price_usd = float(price_usd)
    except (TypeError, ValueError):
        return None

    threshold = float(alert["threshold_usd"])
    direction = alert["direction"]
    crossed = (direction == "above" and price_usd >= threshold) or (
        direction == "below" and price_usd <= threshold
    )
    if not crossed:
        return None
    return {
        "status": "fired",
        "detail": f"{alert['token']} = ${price_usd} ({direction} ${threshold})",
        "price_usd": price_usd,
    }


def _check_wallet_alert(alert: dict[str, Any]) -> dict[str, Any] | None:
    """Poll Basescan for new ERC-20 receipts to the watched wallet."""
    wallet = alert["wallet"]
    start_block = int(alert.get("last_seen_block", 0)) + 1
    txs = _basescan_token_receipts(wallet, start_block=start_block)
    if not txs:
        return None

    # Take the most recent block from what we saw.
    max_block = max(int(tx.get("blockNumber", 0)) for tx in txs)
    alert["last_seen_block"] = max_block

    # Summarize: how many txs, what tokens.
    tokens = sorted({tx.get("contractAddress") for tx in txs if tx.get("contractAddress")})
    sample = ", ".join(_short(t) for t in tokens[:3])
    if len(tokens) > 3:
        sample += f" (+{len(tokens) - 3} more)"
    return {
        "status": "fired",
        "detail": f"{len(txs)} new tx(s), token(s): {sample}",
        "tx_count": len(txs),
        "sample_tokens": tokens[:5],
    }


def _basescan_token_receipts(wallet: str, *, start_block: int) -> list[dict[str, Any]]:
    """Same shape as the /copy poller — incoming ERC-20 transfers only."""
    params = {
        "module": "account",
        "action": "tokentx",
        "address": wallet,
        "startblock": str(start_block),
        "endblock": "99999999",
        "sort": "asc",
    }
    api_key = os.environ.get("BASESCAN_API_KEY")
    if api_key:
        params["apikey"] = api_key
    body = http_get(_BASESCAN_BASE, params=params, timeout=20.0)
    if not isinstance(body, dict):
        return []
    if str(body.get("status")) != "1":
        return []
    result = body.get("result")
    if not isinstance(result, list):
        return []
    return [
        x for x in result if isinstance(x, dict) and (x.get("to") or "").lower() == wallet.lower()
    ]


def _current_block_height() -> int:
    params = {"module": "proxy", "action": "eth_blockNumber"}
    api_key = os.environ.get("BASESCAN_API_KEY")
    if api_key:
        params["apikey"] = api_key
    try:
        body = http_get(_BASESCAN_BASE, params=params, timeout=10.0)
    except Exception:  # noqa: BLE001
        return 0
    if not isinstance(body, dict):
        return 0
    result = body.get("result")
    if not isinstance(result, str) or not result.startswith("0x"):
        return 0
    try:
        return int(result, 16)
    except (ValueError, TypeError):
        return 0


# ── /alerts status / history ───────────────────────────────────────


def _cmd_status(_sender_id: str) -> str:
    state = _load_state()
    alerts = state.get("alerts", [])
    if not alerts:
        return "No alerts exist. The scheduler service is idle."

    by_status: dict[str, int] = {}
    by_type: dict[str, int] = {}
    total_fires = 0
    for a in alerts:
        s = a.get("status", "?")
        by_status[s] = by_status.get(s, 0) + 1
        t = a.get("type", "?")
        by_type[t] = by_type.get(t, 0) + 1
        total_fires += len(a.get("fires", []))

    lines = [
        f"/alerts status ({len(alerts)} alert(s)):",
        "",
        f"  By status:    {', '.join(f'{k}={v}' for k, v in sorted(by_status.items()))}",
        f"  By type:      {', '.join(f'{k}={v}' for k, v in sorted(by_type.items()))}",
        f"  Total fires:  {total_fires}",
    ]
    try:
        from clawmes.services.alerts_scheduler import get_alerts_scheduler_service

        svc = get_alerts_scheduler_service()
        h = svc.health()
        lines.append(
            f"  Service:      {h.get('status')} "
            f"(ticks={h.get('ticks')}, total_fires={h.get('total_runs')})"
        )
    except Exception:  # noqa: BLE001
        pass
    return "\n".join(lines)


def _cmd_history(sender_id: str, parts: list[str]) -> str:
    if not parts:
        return "Usage: /alerts history <id>"
    aid = parts[0]
    state = _load_state()
    alert = _find(state, aid, sender_id)
    if alert is None:
        return f"No alert found with id {aid!r}."
    fires = alert.get("fires", [])
    if not fires:
        return f"Alert {aid}: no fires yet."
    lines = [f"Fires for {aid} ({len(fires)}):", ""]
    for fire in fires[-25:]:
        when = fire.get("at", "")
        detail = fire.get("detail", "")
        lines.append(f"  {when}  {detail}")
    return "\n".join(lines)


def register(ctx) -> None:
    ctx.register_command(
        name="alerts",
        handler=handle_alerts,
        description="Price + wallet activity alerts (notification-only)",
        args_hint="add price|wallet ... | list | pause | resume | cancel | edit | tick | status | history",
    )
