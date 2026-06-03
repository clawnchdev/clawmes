"""Doctor command: ``/doctor`` — health-check + onboarding hints.

Surfaces, in order:

  1. Wallet status (mode / address / chain / balance)
  2. RPC endpoints (which chains are on public-node defaults vs.
     a user-configured override)
  3. API keys (which third-party integrations are unlocked vs. dark)
  4. WalletConnect bridge build status (Node, dist, env)
  5. Plugin manifest summary (tools / commands / hooks / services)

Runs entirely offline — no network calls — so it's cheap to invoke
mid-session. The intent is "what do I need to set up before I can use
clawmes for real?", not "is my RPC actually reachable right now?".

Output is plain text with column-aligned check marks. CommonMark
renders fine in monospace.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from clawmes.lib.addr import short
from clawmes.lib.chains import CHAINS
from clawmes.services.wallet import get_wallet_state

# --- env-var inventory ---------------------------------------------------

# Each row is (env_var, tool_or_service_label, signup_hint).
# Grouped roughly by how essential they are for the average user.
_WALLET_KEYS: list[tuple[str, str, str]] = [
    ("WALLETCONNECT_PROJECT_ID", "WalletConnect", "https://cloud.walletconnect.com"),
    ("BANKR_API_KEY", "Bankr custodial", "https://bankr.bot"),
]

_TRADING_KEYS: list[tuple[str, str, str]] = [
    ("ZEROX_API_KEY", "0x swap (defi_swap)", "https://0x.org"),
    ("LIFI_API_KEY", "LiFi bridge", "https://li.fi"),
    ("COINGECKO_API_KEY", "CoinGecko prices", "https://www.coingecko.com/en/api"),
]

_INTEL_KEYS: list[tuple[str, str, str]] = [
    ("BASESCAN_API_KEY", "Basescan", "https://basescan.org/myapikey"),
    ("ETHERSCAN_API_KEY", "Etherscan", "https://etherscan.io/myapikey"),
    ("ARBISCAN_API_KEY", "Arbiscan", "https://arbiscan.io/myapikey"),
    (
        "OPTIMISM_ETHERSCAN_API_KEY",
        "Optimism Etherscan",
        "https://optimistic.etherscan.io/myapikey",
    ),
    ("POLYGONSCAN_API_KEY", "PolygonScan", "https://polygonscan.com/myapikey"),
    ("RESERVOIR_API_KEY", "Reservoir NFT", "https://reservoir.tools"),
    ("HERD_ACCESS_TOKEN", "Herd intel", "https://herd.eco"),
]

_GOVERNANCE_KEYS: list[tuple[str, str, str]] = [
    ("TALLY_API_KEY", "Tally governance", "https://www.tally.xyz/api"),
    ("NEYNAR_API_KEY", "Farcaster reads", "https://neynar.com"),
    ("NEYNAR_SIGNER_UUID", "Farcaster writes", "https://neynar.com"),
]

_LAUNCH_KEYS: list[tuple[str, str, str]] = [
    (
        "CLAWNCH_LAUNCHPAD_ADDRESS",
        "Clawnch launchpad (clawnch_launch / clawnch_fees)",
        "deploy / get from team",
    ),
]

_SPECIALIZED_KEYS: list[tuple[str, str, str]] = [
    ("LOBSTER_API_KEY", "Lobster privacy / cash", "https://lobster.cash"),
    ("WAYFINDER_API_KEY", "Wayfinder", "https://wayfinder.ai"),
    ("CLAWNX_API_KEY", "Clawnx", "internal"),
    ("MOLTEN_API_KEY", "Molten", "internal"),
    ("GIZA_API_KEY", "Giza ML", "https://gizatech.xyz"),
    ("NOOKPLOT_API_KEY", "NookPlot", "internal"),
    ("PAYSPONGE_API_KEY", "Paysponge", "internal"),
    ("HUMMINGBOT_API_KEY", "Hummingbot Gateway", "self-hosted"),
]


@dataclass(frozen=True)
class _Section:
    title: str
    body: str


def _format(rows: Iterable[tuple[str, str, str, str]]) -> str:
    """Pad a 4-column table: status, label, env-var, hint."""
    rows = list(rows)
    if not rows:
        return "  (none)"
    label_w = max(len(r[1]) for r in rows)
    env_w = max(len(r[2]) for r in rows)
    return "\n".join(f"  {r[0]}  {r[1].ljust(label_w)}  {r[2].ljust(env_w)}  {r[3]}" for r in rows)


def _check_keys(keys: list[tuple[str, str, str]]) -> list[tuple[str, str, str, str]]:
    """Resolve each key into a row tuple ``(status, label, env, hint)``."""
    out = []
    for env, label, hint in keys:
        is_set = bool(os.environ.get(env))
        status = "[ok]   " if is_set else "[----] "
        out.append((status, label, env, "" if is_set else hint))
    return out


# --- sections ------------------------------------------------------------


def _wallet_section() -> _Section:
    state = get_wallet_state()
    if not state.connected:
        body = "  (no wallet connected)\n  Pair via /connect, /connect_local, or /connect_bankr"
        return _Section("WALLET", body)

    addr = state.address or "(unknown)"
    body = "\n".join(
        [
            f"  Mode:    {state.mode}",
            f"  Address: {addr}  ({short(addr)})",
            f"  Chain:   {state.chain_name} (id {state.chain_id})",
            f"  Balance: {state.balance_summary()}",
        ]
    )
    return _Section("WALLET", body)


def _rpc_section() -> _Section:
    """Walk the chain registry directly + check env vars.

    Read straight from ``os.environ`` rather than the running RPC
    service so doctor can be invoked before services are started
    (e.g. from a fresh Python interpreter, or from tests). Either
    way the result is the same: the env-var presence determines
    whether the chain is on a custom endpoint or the public-node
    default.
    """
    rows = []
    for chain_id, chain in CHAINS.items():
        override = os.environ.get(f"CLAWMES_RPC_{chain_id}")
        is_default = override is None
        status = "[default]" if is_default else "[custom] "
        env = f"CLAWMES_RPC_{chain_id}"
        label = f"{chain.short_name} ({chain_id})"
        hint = "rate-limits aggressively" if is_default else "user override"
        rows.append((status, label, env, hint))

    on_defaults = sum(1 for r in rows if r[0].startswith("[default]"))
    summary = ""
    if on_defaults:
        summary = (
            f"\n  {on_defaults} chain(s) on public-node defaults — "
            "set CLAWMES_RPC_<chain_id> for production traffic."
        )
    return _Section("RPC ENDPOINTS", _format(rows) + summary)


def _api_keys_section() -> _Section:
    groups = [
        ("Wallet modes", _WALLET_KEYS),
        ("Trading", _TRADING_KEYS),
        ("Intel / explorers", _INTEL_KEYS),
        ("Governance / social", _GOVERNANCE_KEYS),
        ("Launches", _LAUNCH_KEYS),
        ("Specialized", _SPECIALIZED_KEYS),
    ]
    chunks = []
    for heading, keys in groups:
        chunks.append(f"  ── {heading} ──")
        chunks.append(_format(_check_keys(keys)))
    return _Section("API KEYS", "\n".join(chunks))


def _bridge_section() -> _Section:
    import shutil

    rows = []

    # Node toolchain
    node_path = shutil.which("node")
    rows.append(
        (
            "[ok]   " if node_path else "[----] ",
            "Node.js runtime",
            "",
            node_path or "install Node 20+ from https://nodejs.org",
        )
    )

    # Bundled WC source + dist
    sources_root = Path(__file__).parent.parent / "bridges" / "sources" / "wc"
    dist_entry = sources_root / "dist" / "index.mjs"
    rows.append(
        (
            "[ok]   " if dist_entry.exists() else "[----] ",
            "WC bridge built",
            "",
            str(dist_entry)
            if dist_entry.exists()
            else "run `npm ci && npm run build` in bridges/sources/wc",
        )
    )

    # Project ID — a bundled default ships so WalletConnect works out of the
    # box; the env var overrides it. So this is always "ok"; we just note when
    # the default is in use.
    pid = os.environ.get("WALLETCONNECT_PROJECT_ID")
    rows.append(
        (
            "[ok]   ",
            "WC project ID",
            "WALLETCONNECT_PROJECT_ID",
            "" if pid else "bundled default (set WALLETCONNECT_PROJECT_ID to use your own)",
        )
    )

    return _Section("WALLETCONNECT BRIDGE", _format(rows))


def _plugin_section() -> _Section:
    """Report registered counts.

    Tools + hooks are read from the manifest (the source of truth
    enforced by ``test_plugin_manifest.py``). Commands are counted
    by dry-running ``commands.register_all`` against a recording
    fake context, since the manifest doesn't track them.
    """
    manifest_path = Path(__file__).parent.parent / "plugin.yaml"
    if not manifest_path.exists():
        return _Section("PLUGIN MANIFEST", "  (plugin.yaml missing!)")

    text = manifest_path.read_text(encoding="utf-8")

    def _count_block(field: str) -> int:
        n = 0
        in_block = False
        for line in text.splitlines():
            if line.startswith(f"{field}:"):
                in_block = True
                continue
            if in_block:
                if line and not line.startswith((" ", "\t", "-")):
                    in_block = False
                    continue
                if line.lstrip().startswith("- "):
                    n += 1
        return n

    tools = _count_block("provides_tools")
    hooks = _count_block("provides_hooks")
    commands = _count_commands()

    body = "\n".join(
        [
            f"  Tools registered:    {tools}",
            f"  Commands registered: {commands}",
            f"  Hooks registered:    {hooks}",
            f"  Manifest:            {manifest_path}",
        ]
    )
    return _Section("PLUGIN MANIFEST", body)


def _count_commands() -> int:
    """Dry-run command registration to count what we expose."""

    class _Recorder:
        def __init__(self) -> None:
            self.n = 0

        def register_command(self, **_kw):
            self.n += 1

    from clawmes import commands as commands_pkg

    rec = _Recorder()
    try:
        commands_pkg.register_all(rec)
    except Exception:  # noqa: BLE001 — best-effort count
        return 0
    return rec.n


# --- entrypoint ----------------------------------------------------------


def _render(sections: list[_Section]) -> str:
    out = ["clawmes doctor", "─" * 40, ""]
    for s in sections:
        out.append(s.title)
        out.append(s.body)
        out.append("")
    return "\n".join(out).rstrip() + "\n"


async def handle_doctor(raw_args: str) -> str:
    sections = [
        _wallet_section(),
        _rpc_section(),
        _api_keys_section(),
        _bridge_section(),
        _plugin_section(),
    ]
    return _render(sections)


def register(ctx) -> None:
    ctx.register_command(
        name="doctor",
        handler=handle_doctor,
        description="Health-check: wallet, RPC, API keys, bridge, manifest",
    )
