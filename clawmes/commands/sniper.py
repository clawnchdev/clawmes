"""``/sniper`` — auto-buy newly-launched Clawnch tokens.

Watches the Clawnch launches feed on the registry tick. When a new
token appears that matches the configured filters, submits an ETH-
funded buy at the configured size via ``defi_swap``.

UNLIMITED tier feature (Clawmes Unlimited — 100M+ $CLAWNCH).

Surface:

  * ``/sniper add <eth_amount> [--max-buys N] [--source X]
    [--symbol-filter regex] [--max-mcap <usd>] [--max-age <seconds>]
    [--slippage <bps>]``
  * ``/sniper list`` / ``pause`` / ``resume`` / ``cancel`` / ``edit``
  * ``/sniper tick`` — manual evaluation (testing)
  * ``/sniper status``
  * ``/sniper history <id>``

Filters (all optional; absent ⇒ no filter):

  * ``--source <name>`` — only snipe launches with this source
    attribution (``clawmes``, ``clawncher``, ``4claw``, ``moltbook``).
  * ``--symbol-filter <regex>`` — case-insensitive regex applied to
    the launch's symbol. Use this to target specific themes
    (``"DOG|CAT"``) or avoid noise (``"^(?!.*scam).*"``).
  * ``--max-mcap <usd>`` — skip if launch's reported market cap
    exceeds this value. Defends against sniping pump'n'dumps.
  * ``--max-age <seconds>`` — ignore launches older than this when
    the sniper first sees them. Default 600 (10 min) — anything
    older was probably caught by someone else already.

Safety:

  * Per-snipe ``slippage_bps`` (default 100 = 1%).
  * ``--max-buys`` (default 10) — auto-cancels after N snipes so a
    runaway launchpad doesn't drain your wallet.
  * Each snipe records on history; failures don't auto-pause the
    config (the user can /sniper pause manually).

Storage: ``${HERMES_HOME}/clawmes/sniper/configs.json``.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from clawmes.lib.http import http_get

_CLAWNCH_API_BASE = "https://www.clawn.ch"
_DEFAULT_SLIPPAGE_BPS = 100
_DEFAULT_MAX_BUYS = 10
_DEFAULT_MAX_AGE_SECONDS = 600  # ignore launches older than 10 minutes


# ── state I/O ───────────────────────────────────────────────────────


def _configs_path() -> Path:
    from clawmes.lib.paths import state_dir

    return state_dir("sniper") / "configs.json"


def _load_state() -> dict[str, Any]:
    path = _configs_path()
    if not path.exists():
        return {"configs": []}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {"configs": []}
    if not isinstance(data, dict) or not isinstance(data.get("configs"), list):
        return {"configs": []}
    return data


def _save_state(state: dict[str, Any]) -> None:
    path = _configs_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now_epoch() -> int:
    return int(time.time())


def _new_id() -> str:
    return f"snipe_{uuid.uuid4().hex[:10]}"


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


def _split_flags(parts: list[str]) -> tuple[list[str], dict[str, str]]:
    """Positional + ``--flag value`` pairs. Bare ``--flag`` parses to empty string."""
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


# ── dispatch ────────────────────────────────────────────────────────


async def handle_sniper(raw_args: str, *, sender_id: str = "default", **_kwargs: Any) -> str:
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
    _record("sniper", raw_args, out)
    return out


def _render_usage() -> str:
    return (
        "Sniper — auto-buy newly-launched Clawnch tokens.\n"
        "  (Clawmes Unlimited tier — hold 100M+ $CLAWNCH to /sniper add.)\n"
        "\n"
        "  /sniper add <eth_amount>\n"
        "      [--max-buys <n>] [--source <name>]\n"
        "      [--symbol-filter <regex>] [--max-mcap <usd>]\n"
        "      [--max-age <seconds>] [--slippage <bps>]\n"
        "                          Configure a new sniper\n"
        "  /sniper list            Show your configs + snipe count\n"
        "  /sniper pause <id>      Suspend a config\n"
        "  /sniper resume <id>     Re-arm a paused config\n"
        "  /sniper cancel <id>     Remove a config entirely\n"
        "  /sniper edit <id> <field> <value>\n"
        "                          Change one field on a config\n"
        "  /sniper tick            Manually poll launches (testing)\n"
        "  /sniper status          Global summary + service health\n"
        "  /sniper history <id>    Past snipes for one config\n"
        "\n"
        "Filters (--source / --symbol-filter / --max-mcap / --max-age) are\n"
        "optional. The default config snipes any new launch within the past\n"
        "10 minutes at the configured ETH amount.\n"
        "\n"
        "Example:\n"
        "  /sniper add 0.005 --max-buys 5 --source clawmes --symbol-filter DOG|CAT"
    )


# ── /sniper add ─────────────────────────────────────────────────────


def _cmd_add(sender_id: str, parts: list[str]) -> str:
    # UNLIMITED gate — required to configure a sniper at all.
    from clawmes.services.token_gate import Tier, check_tier_or_error

    gate_err = check_tier_or_error(Tier.UNLIMITED, feature="/sniper")
    if gate_err:
        return gate_err

    pos, flags = _split_flags(parts)
    if not pos:
        return "Usage: /sniper add <eth_amount> [--max-buys N] [--source X] ..."

    try:
        eth_amount = float(pos[0])
    except ValueError:
        return f"eth_amount must be a number (got {pos[0]!r})."
    if eth_amount <= 0:
        return f"eth_amount must be positive (got {eth_amount})."

    max_buys = _DEFAULT_MAX_BUYS
    if "max-buys" in flags:
        try:
            max_buys = int(flags["max-buys"])
        except ValueError:
            return f"--max-buys must be an integer (got {flags['max-buys']!r})."
        if max_buys < 1:
            return f"--max-buys must be >= 1 (got {max_buys})."

    slippage_bps = _DEFAULT_SLIPPAGE_BPS
    if "slippage" in flags:
        try:
            slippage_bps = int(flags["slippage"])
        except ValueError:
            return f"--slippage must be an integer (got {flags['slippage']!r})."
        if slippage_bps < 0 or slippage_bps > 10_000:
            return f"--slippage must be 0–10000 (got {slippage_bps})."

    max_age = _DEFAULT_MAX_AGE_SECONDS
    if "max-age" in flags:
        try:
            max_age = int(flags["max-age"])
        except ValueError:
            return f"--max-age must be an integer (got {flags['max-age']!r})."
        if max_age < 1:
            return f"--max-age must be >= 1 (got {max_age})."

    max_mcap: float | None = None
    if "max-mcap" in flags:
        try:
            max_mcap = float(flags["max-mcap"])
        except ValueError:
            return f"--max-mcap must be a number (got {flags['max-mcap']!r})."
        if max_mcap <= 0:
            return f"--max-mcap must be positive (got {max_mcap})."

    source = flags.get("source", "").strip() or None
    symbol_filter = flags.get("symbol-filter", "").strip() or None
    # Validate the regex at config time so we don't silently no-op later.
    if symbol_filter:
        try:
            re.compile(symbol_filter, re.IGNORECASE)
        except re.error as exc:
            return f"--symbol-filter is not a valid regex: {exc}"

    # Auto-sell: "<gain_pct>:<loss_pct>" — take-profit and stop-loss
    # thresholds for each sniped token. After a successful snipe, the
    # token is added to the auto-sell watch list. On subsequent ticks
    # the scheduler polls the price; when either threshold is crossed,
    # we sell our balance. Still UNLIMITED-gated (covered by the
    # outer /sniper gate).
    auto_sell: dict[str, float] | None = None
    if "auto-sell" in flags:
        raw = flags["auto-sell"]
        parts_as = raw.split(":")
        if len(parts_as) != 2:
            return f"--auto-sell must be '<gain_pct>:<loss_pct>' (got {raw!r})."
        try:
            gain_pct = float(parts_as[0])
            loss_pct = float(parts_as[1])
        except ValueError:
            return f"--auto-sell values must be numbers (got {raw!r})."
        if gain_pct <= 0 or loss_pct <= 0:
            return f"--auto-sell values must be positive (got {raw!r})."
        auto_sell = {"gain_pct": gain_pct, "loss_pct": loss_pct}

    state = _load_state()
    config_id = _new_id()
    config = {
        "id": config_id,
        "sender_id": sender_id,
        "eth_amount": eth_amount,
        "max_buys": max_buys,
        "buys_made": 0,
        "slippage_bps": slippage_bps,
        "source_filter": source,
        "symbol_filter": symbol_filter,
        "max_mcap_usd": max_mcap,
        "max_age_seconds": max_age,
        "auto_sell": auto_sell,
        "auto_sell_watches": [],
        "status": "active",
        "created_at": _now_iso(),
        "last_seen_epoch": _now_epoch(),
        "snipes": [],
    }
    state["configs"].append(config)
    _save_state(state)

    auto_sell_line = (
        f"  Auto-sell:      +{auto_sell['gain_pct']}% / -{auto_sell['loss_pct']}%\n"
        if auto_sell
        else ""
    )
    return (
        f"Sniper added: {config_id}\n"
        f"  Per snipe:      {eth_amount} ETH\n"
        f"  Max buys:       {max_buys}\n"
        f"  Slippage:       {slippage_bps} bps\n"
        f"  Source filter:  {source or 'any'}\n"
        f"  Symbol filter:  {symbol_filter or 'any'}\n"
        f"  Max mcap:       {f'${max_mcap}' if max_mcap is not None else 'any'}\n"
        f"  Max age:        {max_age}s (ignore launches older than this)\n"
        + auto_sell_line
        + "\n"
        + "The sniper service polls /api/launches on the registry tick (~60s)\n"
        + "and fires a buy on each newly-detected launch that matches the\n"
        + "filters. Auto-cancels after max-buys is reached."
    )


# ── /sniper list / mutate / cancel / edit ──────────────────────────


def _cmd_list(sender_id: str) -> str:
    state = _load_state()
    mine = [c for c in state["configs"] if c.get("sender_id") == sender_id]
    if not mine:
        return "No sniper configs. Add one with /sniper add <eth_amount>."
    lines = [f"Sniper configs for {sender_id} ({len(mine)}):", ""]
    for c in mine:
        src = c.get("source_filter") or "any"
        sym = c.get("symbol_filter") or "any"
        buys = c.get("buys_made", 0)
        max_b = c.get("max_buys", _DEFAULT_MAX_BUYS)
        lines.append(
            f"  {c['id']}  {c.get('status'):<10s}"
            f"  {c['eth_amount']} ETH/snipe"
            f"  src={src}  sym={sym}"
            f"  ({buys}/{max_b} snipes)"
        )
    return "\n".join(lines)


def _cmd_mutate(sender_id: str, parts: list[str], *, status: str, verb: str) -> str:
    if not parts:
        return f"Usage: /sniper {verb.removesuffix('d')} <id>"
    cid = parts[0]
    state = _load_state()
    for c in state["configs"]:
        if c.get("id") == cid and c.get("sender_id") == sender_id:
            c["status"] = status
            _save_state(state)
            return f"Sniper {cid} {verb}."
    return f"No sniper config found with id {cid!r}."


def _cmd_cancel(sender_id: str, parts: list[str]) -> str:
    if not parts:
        return "Usage: /sniper cancel <id>"
    cid = parts[0]
    state = _load_state()
    before = len(state["configs"])
    state["configs"] = [
        c for c in state["configs"] if not (c.get("id") == cid and c.get("sender_id") == sender_id)
    ]
    if len(state["configs"]) == before:
        return f"No sniper config found with id {cid!r}."
    _save_state(state)
    return f"Cancelled sniper {cid}."


_EDITABLE = {
    "eth_amount",
    "max_buys",
    "slippage_bps",
    "source_filter",
    "symbol_filter",
    "max_mcap_usd",
    "max_age_seconds",
}


def _cmd_edit(sender_id: str, parts: list[str]) -> str:
    if len(parts) < 3:
        return (
            "Usage: /sniper edit <id> <field> <value>\n"
            f"Editable fields: {', '.join(sorted(_EDITABLE))}"
        )
    cid, field, value = parts[0], parts[1], parts[2]
    if field not in _EDITABLE:
        return f"Unknown field {field!r}. Editable: {', '.join(sorted(_EDITABLE))}"

    state = _load_state()
    config = _find(state, cid, sender_id)
    if config is None:
        return f"No sniper config found with id {cid!r}."

    if field == "eth_amount":
        try:
            v = float(value)
        except ValueError:
            return f"eth_amount must be a number (got {value!r})."
        if v <= 0:
            return f"eth_amount must be positive (got {v})."
        config["eth_amount"] = v
    elif field == "max_buys":
        try:
            v = int(value)
        except ValueError:
            return f"max_buys must be an integer (got {value!r})."
        if v < 1:
            return f"max_buys must be >= 1 (got {v})."
        config["max_buys"] = v
    elif field == "slippage_bps":
        try:
            v = int(value)
        except ValueError:
            return f"slippage_bps must be an integer (got {value!r})."
        if v < 0 or v > 10_000:
            return f"slippage_bps must be 0–10000 (got {v})."
        config["slippage_bps"] = v
    elif field == "source_filter":
        config["source_filter"] = value if value.lower() != "none" else None
    elif field == "symbol_filter":
        if value.lower() == "none":
            config["symbol_filter"] = None
        else:
            try:
                re.compile(value, re.IGNORECASE)
            except re.error as exc:
                return f"symbol_filter is not a valid regex: {exc}"
            config["symbol_filter"] = value
    elif field == "max_mcap_usd":
        if value.lower() == "none":
            config["max_mcap_usd"] = None
        else:
            try:
                v = float(value)
            except ValueError:
                return f"max_mcap_usd must be a number or 'none' (got {value!r})."
            if v <= 0:
                return f"max_mcap_usd must be positive (got {v})."
            config["max_mcap_usd"] = v
    else:  # max_age_seconds
        try:
            v = int(value)
        except ValueError:
            return f"max_age_seconds must be an integer (got {value!r})."
        if v < 1:
            return f"max_age_seconds must be >= 1 (got {v})."
        config["max_age_seconds"] = v

    _save_state(state)
    return f"Sniper {cid}: {field} = {value}."


def _find(state: dict[str, Any], cid: str, sender_id: str) -> dict[str, Any] | None:
    for c in state["configs"]:
        if c.get("id") == cid and c.get("sender_id") == sender_id:
            return c
    return None


# ── /sniper tick — the engine ──────────────────────────────────────


async def _cmd_tick() -> str:
    n, lines = _run_due_with_lines()
    if n == 0:
        return "No new launches matched any active sniper config."
    return "\n".join([f"Fired {n} snipe(s):"] + lines)


def _run_due_sync() -> int:
    n, _ = _run_due_with_lines()
    return n


def _run_due_with_lines() -> tuple[int, list[str]]:
    state = _load_state()
    active = [c for c in state["configs"] if c.get("status") == "active"]
    if not active:
        return 0, []

    # Fetch the launches feed once per tick — every active config reuses
    # the same response. /api/launches returns recent-first; we filter
    # client-side per config.
    try:
        body = http_get(
            f"{_CLAWNCH_API_BASE}/api/launches",
            params={"limit": "50"},
            timeout=15.0,
        )
    except Exception as exc:  # noqa: BLE001
        return 0, [f"  fetch error: {exc}"]
    launches = _extract_launches(body)
    if not launches:
        # Even with no launches, advance last_seen_epoch on each config
        # so the next tick doesn't replay anything.
        now = _now_epoch()
        for config in active:
            config["last_seen_epoch"] = now
        _save_state(state)
        return 0, []

    now = _now_epoch()
    total_fired = 0
    lines: list[str] = []
    for config in active:
        try:
            count = _process_config(config, launches, now, lines)
        except Exception as exc:  # noqa: BLE001
            lines.append(f"  {config['id']}  error: {exc}")
            continue
        total_fired += count
        config["last_seen_epoch"] = now
        # Auto-exhaust when max_buys is reached.
        if config["buys_made"] >= int(config.get("max_buys") or _DEFAULT_MAX_BUYS):
            config["status"] = "exhausted"

    # After all snipe processing, walk every config (including any that
    # just transitioned to exhausted) and evaluate pending auto-sell
    # watches. Auto-sell is decoupled from new-snipe filtering so a
    # config can finish sniping but still close out its open positions.
    for config in state["configs"]:
        try:
            sold = _evaluate_auto_sell_watches(config, lines)
            total_fired += sold
        except Exception as exc:  # noqa: BLE001
            lines.append(f"  {config['id']}  auto-sell error: {exc}")

    if total_fired > 0 or any(lines):
        _save_state(state)
    return total_fired, lines


def _process_config(
    config: dict[str, Any],
    launches: list[dict[str, Any]],
    now_epoch: int,
    lines: list[str],
) -> int:
    """Match launches against config filters and submit snipes."""
    last_seen = int(config.get("last_seen_epoch", 0))
    source_filter = config.get("source_filter")
    symbol_filter = config.get("symbol_filter")
    max_mcap = config.get("max_mcap_usd")
    max_age = int(config.get("max_age_seconds") or _DEFAULT_MAX_AGE_SECONDS)
    max_buys = int(config.get("max_buys") or _DEFAULT_MAX_BUYS)
    sym_pat = re.compile(symbol_filter, re.IGNORECASE) if symbol_filter else None
    count = 0

    for launch in launches:
        if config["buys_made"] >= max_buys:
            break

        addr = launch.get("contractAddress") or launch.get("tokenAddress") or launch.get("address")
        if not isinstance(addr, str) or not addr.startswith("0x"):
            continue

        # Parse launch timestamp. Accept epoch ints or ISO strings.
        launch_epoch = _parse_launch_epoch(launch)
        if launch_epoch <= last_seen:
            continue  # already-processed launch
        if now_epoch - launch_epoch > max_age:
            continue  # too old

        source = launch.get("source") or ""
        if source_filter and source.lower() != source_filter.lower():
            continue

        symbol = launch.get("symbol") or launch.get("ticker") or ""
        if sym_pat and not sym_pat.search(symbol):
            continue

        if max_mcap is not None:
            mcap = launch.get("marketCap") or launch.get("fdv") or 0
            try:
                if float(mcap) > float(max_mcap):
                    continue
            except (TypeError, ValueError):
                pass  # missing/unparsable mcap → don't reject

        # All filters passed → snipe.
        result = _submit_snipe(config, addr.lower())
        config["snipes"].append(
            {
                "at": _now_iso(),
                "token": addr.lower(),
                "symbol": symbol,
                "result": result,
            }
        )
        if result.get("status") == "ok":
            config["buys_made"] += 1
            count += 1
            # On successful snipe, register an auto-sell watch if
            # configured. The watch records the buy-time price as
            # anchor so subsequent ticks can compare deltas.
            if config.get("auto_sell"):
                buy_price = _fetch_price(addr.lower())
                if buy_price is not None and buy_price > 0:
                    config.setdefault("auto_sell_watches", []).append(
                        {
                            "token": addr.lower(),
                            "symbol": symbol,
                            "buy_price_usd": buy_price,
                            "created_at": _now_iso(),
                            "status": "active",
                        }
                    )
        lines.append(
            f"  {config['id']}  {result.get('status')}  "
            f"{_short(addr)} ({symbol})  {result.get('detail', '')}"
        )
    return count


def _parse_launch_epoch(launch: dict[str, Any]) -> int:
    """Best-effort parse of a launch's timestamp into epoch seconds."""
    for key in ("timestamp", "createdAt", "deployedAt", "ts"):
        raw = launch.get(key)
        if isinstance(raw, int):
            # Heuristic: ms vs seconds. Anything > 10^11 is likely ms.
            return raw // 1000 if raw > 10**11 else raw
        if isinstance(raw, str):
            try:
                dt = datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
                return int(dt.timestamp())
            except ValueError:
                try:
                    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                    return int(dt.timestamp())
                except ValueError:
                    continue
    return 0  # unparseable → treated as old; max-age filter will skip


def _fetch_price(token: str) -> float | None:
    """USD price of ``token`` via ``defi_price``. Returns ``None`` on error.

    Used by the auto-sell engine to anchor buy prices and detect
    threshold crossings. We never raise — a failed price read is
    treated as "skip the watch for this tick."
    """
    try:
        from clawmes.tools.defi_price import defi_price

        raw = defi_price({"action": "quote", "symbol": token, "quote_currency": "USD"})
    except Exception:  # noqa: BLE001
        return None
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if payload.get("isError"):
        return None
    details = payload.get("details") or {}
    price = details.get("price_usd") or details.get("price")
    try:
        return float(price)
    except (TypeError, ValueError):
        return None


def _evaluate_auto_sell_watches(config: dict[str, Any], lines: list[str]) -> int:
    """Walk every active auto-sell watch on ``config`` and sell if triggered.

    For each watch:

      * Read current USD price via ``_fetch_price``.
      * Compute pct change from buy-time anchor.
      * If pct >= ``gain_pct`` (take-profit) or pct <= ``-loss_pct``
        (stop-loss), sell our balance and mark the watch ``filled``.

    Returns the count of sells executed.
    """
    watches = config.get("auto_sell_watches", [])
    auto_sell = config.get("auto_sell")
    if not watches or not auto_sell:
        return 0
    gain_pct = float(auto_sell.get("gain_pct", 0))
    loss_pct = float(auto_sell.get("loss_pct", 0))
    sold = 0
    for watch in watches:
        if watch.get("status") != "active":
            continue
        current = _fetch_price(watch["token"])
        if current is None or current <= 0:
            continue
        buy_price = float(watch["buy_price_usd"])
        delta_pct = (current - buy_price) / buy_price * 100.0
        triggered = (
            (delta_pct >= gain_pct and "take_profit")
            or (delta_pct <= -loss_pct and "stop_loss")
            or None
        )
        if not triggered:
            continue
        # Threshold crossed — sell our balance back to ETH.
        result = _submit_token_sell(config, watch["token"])
        watch["closed_at"] = _now_iso()
        watch["closed_at_price_usd"] = current
        watch["delta_pct"] = delta_pct
        watch["close_reason"] = triggered
        watch["close_result"] = result
        if result.get("status") == "ok":
            watch["status"] = "filled"
            sold += 1
        else:
            watch["status"] = "close_failed"
        lines.append(
            f"  {config['id']}  auto-sell {result.get('status')} "
            f"({triggered}, {delta_pct:+.1f}%)  "
            f"{_short(watch['token'])} ({watch.get('symbol', '')})"
        )
    return sold


def _submit_token_sell(config: dict[str, Any], token: str) -> dict[str, Any]:
    """Sell our entire balance of ``token`` back to ETH."""
    from clawmes.services.wallet import get_wallet_state

    wstate = get_wallet_state()
    if not wstate.connected:
        return {"status": "no_wallet", "detail": "no wallet connected", "tx_hash": ""}

    balance_wei = _read_our_token_balance(token, wstate.address)
    if balance_wei <= 0:
        return {
            "status": "no_balance",
            "detail": "no balance to sell",
            "tx_hash": "",
        }

    try:
        from clawmes.tools.defi_swap import defi_swap

        raw = defi_swap(
            {
                "action": "swap",
                "sell_token": token,
                "buy_token": "ETH",
                "sell_amount_wei": str(balance_wei),
                "slippage_bps": int(config.get("slippage_bps") or _DEFAULT_SLIPPAGE_BPS),
            }
        )
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "detail": str(exc), "tx_hash": ""}

    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return {"status": "error", "detail": f"bad swap response: {raw}", "tx_hash": ""}
    if payload.get("isError"):
        msg = payload.get("content", [{}])[0].get("text", "swap failed")
        return {"status": "error", "detail": msg, "tx_hash": ""}

    details = payload.get("details") or {}
    tx_hash = details.get("tx_hash") or details.get("txHash") or ""
    return {"status": "ok", "detail": f"sold via tx {tx_hash[:14]}…", "tx_hash": tx_hash}


def _read_our_token_balance(token: str, address: str | None) -> int:
    """Read our balance of ``token`` via ``balanceOf`` eth_call."""
    if not address:
        return 0
    try:
        from clawmes.lib.abi import decode_uint, encode_balance_of
        from clawmes.services.rpc import get_rpc_service

        rpc = get_rpc_service()
        raw = rpc.eth_call(
            to=token,
            data=encode_balance_of(address),
            chain_id=8453,
        )
        return decode_uint(raw)
    except Exception:  # noqa: BLE001
        return 0


def _submit_snipe(config: dict[str, Any], token: str) -> dict[str, Any]:
    """Submit one swap for a sniped launch."""
    from clawmes.services.wallet import get_wallet_state

    wstate = get_wallet_state()
    if not wstate.connected:
        return {"status": "no_wallet", "detail": "no wallet connected", "tx_hash": ""}

    try:
        from clawmes.tools.defi_swap import defi_swap

        raw = defi_swap(
            {
                "action": "swap",
                "sell_token": "ETH",
                "buy_token": token,
                "sell_amount": str(config["eth_amount"]),
                "slippage_bps": int(config.get("slippage_bps") or _DEFAULT_SLIPPAGE_BPS),
            }
        )
    except Exception as exc:  # noqa: BLE001
        return {"status": "error", "detail": str(exc), "tx_hash": ""}

    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return {"status": "error", "detail": f"bad swap response: {raw}", "tx_hash": ""}
    if payload.get("isError"):
        msg = payload.get("content", [{}])[0].get("text", "swap failed")
        return {"status": "error", "detail": msg, "tx_hash": ""}

    details = payload.get("details") or {}
    tx_hash = details.get("tx_hash") or details.get("txHash") or ""
    return {"status": "ok", "detail": f"tx {tx_hash[:14]}…", "tx_hash": tx_hash}


def _extract_launches(body: Any) -> list[dict[str, Any]]:
    if isinstance(body, list):
        return [x for x in body if isinstance(x, dict)]
    if isinstance(body, dict):
        for key in ("launches", "data", "results"):
            inner = body.get(key)
            if isinstance(inner, list):
                return [x for x in inner if isinstance(x, dict)]
    return []


# ── /sniper status / history ───────────────────────────────────────


def _cmd_status(_sender_id: str) -> str:
    state = _load_state()
    configs = state.get("configs", [])
    if not configs:
        return "No sniper configs exist. The scheduler service is idle."

    by_status: dict[str, int] = {}
    total_snipes = 0
    successful = 0
    for c in configs:
        s = c.get("status", "?")
        by_status[s] = by_status.get(s, 0) + 1
        for snipe in c.get("snipes", []):
            total_snipes += 1
            if (snipe.get("result") or {}).get("status") == "ok":
                successful += 1

    lines = [
        f"/sniper status ({len(configs)} config(s)):",
        "",
        f"  By status:   {', '.join(f'{k}={v}' for k, v in sorted(by_status.items()))}",
        f"  Total snipes: {total_snipes} attempted, {successful} successful",
    ]
    try:
        from clawmes.services.sniper_scheduler import get_sniper_scheduler_service

        svc = get_sniper_scheduler_service()
        h = svc.health()
        lines.append(
            f"  Service:     {h.get('status')} "
            f"(ticks={h.get('ticks')}, total_fired={h.get('total_runs')})"
        )
    except Exception:  # noqa: BLE001
        pass
    return "\n".join(lines)


def _cmd_history(sender_id: str, parts: list[str]) -> str:
    if not parts:
        return "Usage: /sniper history <id>"
    cid = parts[0]
    state = _load_state()
    config = _find(state, cid, sender_id)
    if config is None:
        return f"No sniper config found with id {cid!r}."
    snipes = config.get("snipes", [])
    if not snipes:
        return f"Sniper {cid}: no snipes yet."
    lines = [f"Snipes for {cid} ({len(snipes)}):", ""]
    for snipe in snipes[-25:]:
        result = snipe.get("result") or {}
        status = result.get("status", "?")
        token = snipe.get("token", "")
        symbol = snipe.get("symbol", "")
        when = snipe.get("at", "")
        lines.append(f"  {when}  {status:<10s}  {symbol:<10s}  {_short(token)}")
    return "\n".join(lines)


def register(ctx) -> None:
    ctx.register_command(
        name="sniper",
        handler=handle_sniper,
        description="Auto-buy newly-launched Clawnch tokens (Clawmes Unlimited tier)",
        args_hint="add <eth> | list | pause | resume | cancel | edit | tick | status | history",
    )
