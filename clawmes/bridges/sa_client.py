"""Python client for ``clawmes-sa-bridge`` (MetaMask Smart Accounts).

Typed wrappers for EIP-7702 / 7710 delegation work and Permit2 signing.
Methods (see PRD §21.3):

  * :meth:`delegation_create`
  * :meth:`delegation_list`
  * :meth:`delegation_revoke`
  * :meth:`delegation_execute` — used by the ``@write_tool`` gating
    pipeline as stage 3 (skip handler if delegation handles it)
  * :meth:`account_deploy`
  * :meth:`permit2_sign`
  * :meth:`health`
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from clawmes.bridges.process import BridgeProcess
from clawmes.lib.logger import logger_for

_log = logger_for("bridges.sa")


class SmartAccountsClient:
    def __init__(self, entry: Path, *, node_bin: str = "node") -> None:
        self._proc = BridgeProcess("clawmes-sa", entry, node_bin=node_bin)

    def start(self) -> None:
        self._proc.start()

    def stop(self) -> None:
        self._proc.stop()

    def delegation_create(
        self,
        *,
        delegate: str,
        permissions: list[dict[str, Any]],
        expiry: int,
    ) -> dict[str, Any]:
        return self._proc.call(
            "delegation_create",
            {"delegate": delegate, "permissions": permissions, "expiry": expiry},
        )

    def delegation_list(self) -> list[dict[str, Any]]:
        result = self._proc.call("delegation_list", {})
        return list(result.get("delegations", []))

    def delegation_revoke(self, delegation_id: str) -> str:
        result = self._proc.call("delegation_revoke", {"delegation_id": delegation_id})
        return result["tx_hash"]

    def delegation_execute(
        self,
        *,
        delegation_id: str,
        calldata: str,
        to: str,
        value: str = "0x0",
        chain_id: int,
    ) -> str:
        result = self._proc.call(
            "delegation_execute",
            {
                "delegation_id": delegation_id,
                "calldata": calldata,
                "to": to,
                "value": value,
                "chain_id": chain_id,
            },
            timeout=60.0,
        )
        return result["tx_hash"]

    def account_deploy(self, chain_id: int) -> dict[str, Any]:
        return self._proc.call("account_deploy", {"chain_id": chain_id})

    def permit2_sign(
        self,
        *,
        token: str,
        spender: str,
        amount: str,
        deadline: int,
    ) -> dict[str, Any]:
        return self._proc.call(
            "permit2_sign",
            {"token": token, "spender": spender, "amount": amount, "deadline": deadline},
        )

    def health(self) -> dict[str, Any]:
        return self._proc.call("health", {})
