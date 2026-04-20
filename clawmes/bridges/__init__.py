"""Node sub-process bridges.

Two long-lived Node processes are spawned at plugin start:

  * ``clawmes-wc-bridge`` — wraps ``@walletconnect/sign-client`` for WC v2
    pairing, signing, and session management.
  * ``clawmes-sa-bridge`` — wraps ``@metamask/smart-accounts-kit`` for
    EIP-7702/7710 delegation creation, listing, and execution.

Why subprocess? Both upstream JS libs are first-party reference impls
of in-flux specs; re-implementing in Python is a 6-month project per
bridge with continuous spec drift. Sub-process keeps the spec-tracking
burden on upstream.

Wire format: JSON-line over stdio. See PRD §21 for the full method
catalog and protocol.
"""

from __future__ import annotations

from clawmes.bridges.installer import ensure_node_bridges
from clawmes.bridges.process import BridgeProcess

__all__ = ["BridgeProcess", "ensure_node_bridges"]
