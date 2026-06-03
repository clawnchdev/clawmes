"""Python client for ``clawmes-wc-bridge`` (WalletConnect v2).

Typed wrappers around :class:`clawmes.bridges.process.BridgeProcess`
methods. Exposed methods match those documented in PRD §21.2:

  * :meth:`pair`               — generate a pairing URI
  * :meth:`session_status`     — current session info
  * :meth:`disconnect`         — drop session
  * :meth:`request_signature`  — eth_sendTransaction / eth_signTypedData_v4 / personal_sign
  * :meth:`switch_chain`       — request chain switch
  * :meth:`health`             — bridge process health

Notifications surface via :meth:`notifications` returning the underlying
queue; the wallet service reads it to drive ``pairing_approved``,
``session_expired``, etc.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from clawmes.bridges.process import BridgeProcess
from clawmes.lib.logger import logger_for

_log = logger_for("bridges.wc")

# Clawnch's shared Reown (WalletConnect v2) project ID. A WC project ID is a
# *public, per-application* client identifier — dapps ship a single one for all
# their users; it's embedded in browser bundles and is NOT a secret. Bundling a
# default here means wallet connection works out of the box instead of erroring
# until each user creates their own at cloud.walletconnect.com.
#
# Tradeoff: all default-id traffic counts against this one Reown project's relay
# quota. Power users (or anyone wanting their own analytics / to avoid the
# shared quota) override it by setting WALLETCONNECT_PROJECT_ID in
# ~/.hermes/.env, which always wins (see :func:`_bridge_env`).
_DEFAULT_WALLETCONNECT_PROJECT_ID = "f3a18ce66d092a392f3075ff566db1cf"


def _bridge_env() -> dict[str, str]:
    """Environment for the WC bridge subprocess.

    Inherits the parent environment and guarantees a WalletConnect project id:
    the ``WALLETCONNECT_PROJECT_ID`` env var wins when set (and non-empty),
    otherwise the bundled default is used. The ``or`` (not ``setdefault``) means
    an explicitly-empty env var also falls back to the default.
    """
    env = dict(os.environ)
    env["WALLETCONNECT_PROJECT_ID"] = (
        os.environ.get("WALLETCONNECT_PROJECT_ID") or _DEFAULT_WALLETCONNECT_PROJECT_ID
    )
    return env


class WalletConnectClient:
    def __init__(self, entry: Path, *, node_bin: str = "node") -> None:
        self._proc = BridgeProcess("clawmes-wc", entry, node_bin=node_bin, env=_bridge_env())

    def start(self) -> None:
        self._proc.start()

    def stop(self) -> None:
        self._proc.stop()

    def pair(self) -> dict[str, Any]:
        """Generate a WC pairing URI; user scans on phone."""
        return self._proc.call("pair", {})

    def session_status(self) -> dict[str, Any]:
        return self._proc.call("session_status", {})

    def disconnect(self) -> None:
        self._proc.call("disconnect", {})

    def request_signature(
        self,
        *,
        method: str,
        params: list[Any],
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Forward an EVM RPC sign request to the user's phone.

        ``method`` is one of ``eth_sendTransaction``,
        ``eth_signTypedData_v4``, ``personal_sign``. Returns the
        signature or tx hash as a hex string.
        """
        result = self._proc.call(
            "request_signature",
            {"method": method, "params": params, "metadata": metadata or {}},
            timeout=180.0,  # human-in-the-loop — generous
        )
        return result["signature_or_hash"]

    def switch_chain(self, chain_id: int) -> bool:
        result = self._proc.call("switch_chain", {"chain_id": chain_id})
        return bool(result.get("ok"))

    def health(self) -> dict[str, Any]:
        return self._proc.call("health", {})

    def notifications(self):
        return self._proc.notifications()
