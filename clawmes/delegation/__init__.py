"""EIP-7710 / EIP-7715 on-chain delegation.

clawmes compiles user spending policies into cryptographically-enforced
on-chain delegations using the MetaMask Delegation Framework (v1.3.0),
so the agent can execute transactions autonomously but only within limits
that are checked by on-chain *caveat enforcer* contracts — not merely by
the app-layer policy gate.

Two actors:

  * **delegator** — the user's wallet, which holds the funds. For on-chain
    enforcement it must be a smart account (ERC-7579) or an EIP-7702-upgraded
    EOA, because the DelegationManager calls ``executeFromExecutor`` on it.
  * **delegate** — the agent's own key (an EOA generated + stored locally by
    :mod:`clawmes.delegation.agent_key`). It signs and pays gas for the
    ``redeemDelegations`` transaction.

The delegator signs an EIP-712 ``Delegation`` struct (once) granting the
delegate scoped permission; the signed struct is stored locally and redeemed
by the agent whenever a matching write tool fires — see
:func:`clawmes.delegation.executor.try_delegation_execution`, wired as
stage 3 of the ``@write_tool`` gate.

This is a **pure-Python** implementation (``eth-account`` + ``eth-abi``) —
no Node/SDK subprocess. Its ABI encoders and EIP-712 signatures are verified
byte-for-byte against viem's output (the reference openclawnch stack) in
:mod:`tests.delegation`.

References:
  * EIP-7710 — https://eips.ethereum.org/EIPS/eip-7710
  * EIP-7715 — https://eips.ethereum.org/EIPS/eip-7715
  * EIP-7702 — https://eips.ethereum.org/EIPS/eip-7702
  * MetaMask Delegation Framework — https://github.com/MetaMask/delegation-framework
"""

from __future__ import annotations
