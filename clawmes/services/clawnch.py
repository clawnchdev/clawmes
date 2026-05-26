"""HTTP client for the Clawnch launchpad API.

Talks to ``https://clawn.ch/api`` to:

  * Register a new agent (two-step: ``register`` -> sign challenge ->
    ``verify``).
  * Submit a token deploy challenge (POST ``/api/deploy``).
  * Solve the captcha (sign message + read storage slot + compute
    keccak proof) using the active wallet mode.
  * Confirm the deploy (POST ``/api/deploy/confirm``) — Clawnch's
    deployer wallet pays gas and submits the Clanker tx server-side.
  * Read launches.

This is the integration that makes ``clawnch_launch`` and
``clawnch_fees`` actually work: previously both tools failed out with
``not_implemented`` because they targeted an imaginary Clawnch-native
launchpad contract. The real launchpad is the Clawnch HTTP API
orchestrating Clanker v4 on Base.

Why the API and not direct on-chain calls:

  * Clawnch holds the deployer wallet (custodial gas + atomicity);
    clawmes never has to manage launchpad-side keys.
  * Rate limiting + spam controls (24h cooldown, ETH-bypass, optional
    $CLAWNCH burn bypass) are enforced server-side; the open source
    plugin inherits them automatically.
  * Backend migration (Clanker -> ClawnchFactory v2) preserves the
    HTTP API per ``clawncher/migration-v2.md``; clawmes stays valid
    through the swap.

Auth: ``CLAWNCH_API_KEY`` env var. Issued by clawn.ch via the two-step
register flow. Unauthenticated calls are rejected by the launchpad,
so the service refuses to start premium ops until the key is present.
Reads (``get_launches``) work without a key.
"""

from __future__ import annotations

import os
import threading
from typing import Any

from clawmes.lib.http import http_get, http_post
from clawmes.lib.logger import logger_for
from clawmes.services._base import Service

_log = logger_for("services.clawnch")

#: Base URL of the Clawnch HTTP API. Override via ``CLAWNCH_BASE_URL`` for
#: staging / local dev. The service uses ``/api/...`` paths underneath.
_BASE_URL = os.environ.get("CLAWNCH_BASE_URL", "https://clawn.ch")

#: Source tag attached to every deploy made through clawmes. Lets the
#: launchpad render a "launched via clawmes" badge on launch detail pages.
#: Public attribution — observers can count clawmes-sourced launches.
_DEPLOY_SOURCE_TAG = "clawmes"


class ClawnchError(RuntimeError):
    """Raised on Clawnch API failures.

    ``code`` classification mirrors the rest of clawmes' service errors:

      * ``bad_request`` — caller-side problem (missing field, malformed
        token params, HTTP 400).
      * ``no_credentials`` — API key missing or rejected (HTTP 401/403).
      * ``rate_limited`` — Clawnch's 24h cooldown or burst limit (HTTP 429).
      * ``challenge_expired`` — captcha not solved within the 5s window
        (HTTP 408).
      * ``not_found`` — launch / agent / challenge not found (HTTP 404).
      * ``api_error`` — generic upstream failure.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ClawnchService(Service):
    """Singleton HTTP client for the Clawnch launchpad."""

    id = "clawmes.clawnch"

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._api_key: str | None = None
        self._base_url: str = _BASE_URL

    # ── lifecycle ───────────────────────────────────────────────────

    def start(self) -> None:
        with self._lock:
            self._api_key = os.environ.get("CLAWNCH_API_KEY") or None
            self._base_url = os.environ.get("CLAWNCH_BASE_URL", _BASE_URL).rstrip("/")
        if self._api_key:
            _log.info("clawnch service started (auth=key, base=%s)", self._base_url)
        else:
            _log.warning(
                "clawnch service started UNAUTHENTICATED (no CLAWNCH_API_KEY); "
                "reads work but token deploys will be rejected. Register an "
                "agent with /register_agent and set CLAWNCH_API_KEY to enable "
                "/launch (base=%s)",
                self._base_url,
            )

    def stop(self) -> None:
        with self._lock:
            self._api_key = None

    def health(self) -> dict[str, Any]:
        with self._lock:
            return {
                "id": self.id,
                "status": "authenticated" if self._api_key else "unauthenticated",
                "base_url": self._base_url,
            }

    # ── agent registration ──────────────────────────────────────────

    def register_agent(self, *, name: str, wallet: str, description: str) -> dict[str, Any]:
        """Start agent registration. Returns ``{registrationId, challenge, message}``.

        The caller must then sign ``message`` with the wallet and pass
        the signature to :meth:`verify_agent`. The pending registration
        expires server-side after a short TTL.
        """
        if not name:
            raise ClawnchError("bad_request", "name is required")
        if not wallet:
            raise ClawnchError("bad_request", "wallet is required")
        if not description:
            raise ClawnchError("bad_request", "description is required")
        body = {"name": name, "wallet": wallet, "description": description}
        return self._post("/api/agents/register", body, auth=False)

    def verify_agent(self, *, registration_id: str, signature: str) -> dict[str, Any]:
        """Complete agent registration. Returns ``{apiKey, agentId, wallet}``.

        The returned ``apiKey`` should be persisted by the caller (we
        suggest ``~/.hermes/.env`` as ``CLAWNCH_API_KEY``).
        """
        if not registration_id:
            raise ClawnchError("bad_request", "registration_id is required")
        if not signature:
            raise ClawnchError("bad_request", "signature is required")
        body = {"registrationId": registration_id, "signature": signature}
        return self._post("/api/agents/verify", body, auth=False)

    # ── deploy: phase 1 (challenge) ─────────────────────────────────

    def start_deploy(
        self,
        *,
        token_params: dict[str, Any],
        bypass_tx_hash: str | None = None,
        burn_tx_hash: str | None = None,
    ) -> dict[str, Any]:
        """Start a token deploy. Returns the captcha challenge.

        ``token_params`` must include at minimum ``name`` and ``symbol``;
        ``description`` and ``image`` are optional. The ``source`` field
        is auto-stamped as ``"clawmes"`` so launches show the clawmes
        attribution badge on /pad/.

        Pass ``bypass_tx_hash`` to skip the 24h rate limit — must be a
        confirmed tx hash of >= 0.005 ETH sent to the bypass recipient
        address on Base (call :meth:`get_bypass_recipient` for the
        current address + required fee).

        Pass ``burn_tx_hash`` to claim a vault allocation by burning
        $CLAWNCH — must be a confirmed tx hash of >= 1,000,000 CLAWNCH
        sent to the burn address within the 24h pre-launch window. The
        backend verifies the burn and applies the corresponding vault
        percentage (1M = 1%, 10M = max 10%). See ``api/lib/burn.ts``.
        """
        self._require_key()
        if not token_params.get("name"):
            raise ClawnchError("bad_request", "token_params.name is required")
        if not token_params.get("symbol"):
            raise ClawnchError("bad_request", "token_params.symbol is required")
        stamped = dict(token_params)
        stamped.setdefault("source", _DEPLOY_SOURCE_TAG)
        body: dict[str, Any] = {"tokenParams": stamped}
        if bypass_tx_hash:
            body["bypassTxHash"] = bypass_tx_hash
        if burn_tx_hash:
            body["burnTxHash"] = burn_tx_hash
        return self._post("/api/deploy", body, auth=True)

    # ── deploy: phase 2 (solve + confirm) ───────────────────────────

    def solve_challenge(self, challenge: dict[str, Any]) -> dict[str, Any]:
        """Solve a deploy challenge using the active wallet.

        Returns ``{signature, storageValue, proof}`` ready to feed into
        :meth:`confirm_deploy`. Three steps:

          1. Sign ``challenge.message`` with the active wallet mode
             (``personal_sign``).
          2. Read ``challenge.storageSlot`` from ``challenge.contractAddress``
             on Base via the rpc service.
          3. Compute ``keccak256(signature || nonce || storageValue)``
             matching the upstream ``encodePacked`` layout.
        """
        required = ("message", "nonce", "contractAddress", "storageSlot")
        for key in required:
            if not challenge.get(key):
                raise ClawnchError("bad_request", f"challenge missing required field: {key}")
        signature = self._sign_message(challenge["message"])
        storage_value = self._read_storage(
            address=challenge["contractAddress"],
            slot=challenge["storageSlot"],
        )
        proof = self._compute_proof(
            signature=signature,
            nonce=challenge["nonce"],
            storage_value=storage_value,
        )
        return {"signature": signature, "storageValue": storage_value, "proof": proof}

    def confirm_deploy(
        self,
        *,
        challenge_id: str,
        solution: dict[str, str],
        token_params: dict[str, Any],
    ) -> dict[str, Any]:
        """Submit the solved challenge. Returns ``{success, txHash, tokenAddress}``."""
        self._require_key()
        if not challenge_id:
            raise ClawnchError("bad_request", "challenge_id is required")
        if not solution:
            raise ClawnchError("bad_request", "solution is required")
        stamped = dict(token_params)
        stamped.setdefault("source", _DEPLOY_SOURCE_TAG)
        body = {
            "challengeId": challenge_id,
            "solution": solution,
            "tokenParams": stamped,
        }
        return self._post("/api/deploy/confirm", body, auth=True)

    def deploy(
        self,
        *,
        token_params: dict[str, Any],
        bypass_tx_hash: str | None = None,
        burn_tx_hash: str | None = None,
    ) -> dict[str, Any]:
        """End-to-end deploy convenience: challenge -> solve -> confirm.

        Returns the confirm response on success. Raises ``ClawnchError``
        with a classified ``code`` on any step failure. ``burn_tx_hash``
        claims a vault allocation; ``bypass_tx_hash`` skips the 24h
        cooldown — they're independent and can both be supplied.
        """
        challenge = self.start_deploy(
            token_params=token_params,
            bypass_tx_hash=bypass_tx_hash,
            burn_tx_hash=burn_tx_hash,
        )
        solution = self.solve_challenge(challenge)
        return self.confirm_deploy(
            challenge_id=challenge["challengeId"],
            solution=solution,
            token_params=token_params,
        )

    # ── reads ───────────────────────────────────────────────────────

    def get_my_launches(self) -> dict[str, Any]:
        """Return the authenticated agent's launch history."""
        self._require_key()
        return self._get("/api/agents/me")

    def get_launch(self, token_address: str) -> dict[str, Any]:
        """Return launch metadata for a deployed token."""
        if not token_address:
            raise ClawnchError("bad_request", "token_address is required")
        return self._get(f"/api/launches?address={token_address}")

    def get_bypass_recipient(self) -> dict[str, Any]:
        """Return the current ETH-bypass recipient + required fee.

        Surfaced as a separate endpoint so the /launch UX can show the
        exact address + amount when the user opts into bypass without
        guessing or hardcoding.
        """
        # Bypass-recipient discovery isn't a dedicated endpoint upstream
        # today, but the rate-limited deploy response includes it in
        # ``details.bypassOption``. Until the launchpad exposes a
        # standalone endpoint we surface a stable fallback so callers
        # don't have to trigger a real rate-limit error to discover it.
        # The fallback comes from env (set when known) or a stub.
        return {
            "recipient": os.environ.get(
                "CLAWNCH_BYPASS_RECIPIENT",
                "0xFC426DFeAe55Dae2f936a592450C9ECEa87A5736",
            ),
            "fee_eth": os.environ.get("CLAWNCH_BYPASS_FEE_ETH", "0.005"),
        }

    def get_burn_config(self) -> dict[str, Any]:
        """Return the $CLAWNCH burn config used by ``/launch burn``.

        Returns the token address (the CLAWNCH ERC-20), the burn
        address (dead address — 0x…dEaD), and the minimum burn amount
        in whole tokens. The frontend uses these to construct a
        ``transfer(burn_address, amount * 1e18)`` calldata that the
        active wallet signs.

        Stable values today (override via env for staging):

          * ``CLAWNCH_TOKEN_ADDRESS``    — default ``0xa1F724…747be``
          * ``CLAWNCH_BURN_ADDRESS``     — default ``0x000…dEaD``
          * ``CLAWNCH_MIN_BURN_TOKENS``  — default ``1_000_000`` (1% vault)
        """
        return {
            "token_address": os.environ.get(
                "CLAWNCH_TOKEN_ADDRESS",
                "0xa1F72459dfA10BAD200Ac160eCd78C6b77a747be",
            ),
            "burn_address": os.environ.get(
                "CLAWNCH_BURN_ADDRESS",
                "0x000000000000000000000000000000000000dEaD",
            ),
            "min_burn_tokens": int(os.environ.get("CLAWNCH_MIN_BURN_TOKENS", "1000000")),
        }

    # ── internals: HTTP ─────────────────────────────────────────────

    def _post(self, path: str, body: dict, *, auth: bool) -> dict[str, Any]:
        url = self._base_url + path
        headers = {"Content-Type": "application/json"}
        if auth:
            with self._lock:
                key = self._api_key
            if key:
                headers["Authorization"] = f"Bearer {key}"
        try:
            return http_post(url, json=body, headers=headers, timeout=30.0)
        except Exception as exc:  # noqa: BLE001 — classified below
            self._reclassify(exc)
            raise

    def _get(self, path: str) -> dict[str, Any]:
        url = self._base_url + path
        headers: dict[str, str] = {}
        with self._lock:
            key = self._api_key
        if key:
            headers["Authorization"] = f"Bearer {key}"
        try:
            return http_get(url, headers=headers, timeout=15.0)
        except Exception as exc:  # noqa: BLE001
            self._reclassify(exc)
            raise

    @staticmethod
    def _reclassify(exc: BaseException) -> None:
        """Map a raised HTTP exception to a ``ClawnchError``.

        ``lib.http`` raises ``httpx.HTTPStatusError`` (or a wrapped form)
        on non-2xx. We extract the upstream JSON body + ``code`` /
        ``error`` fields when present and re-raise with the canonical
        classification.
        """
        text = str(exc)
        body: dict[str, Any] = {}
        # Best-effort body extraction. httpx exceptions surface the
        # response via .response.json() on HTTPStatusError; tenacity
        # wraps inside RetryError. Walk the chain.
        cur: BaseException | None = exc
        while cur is not None:
            resp = getattr(cur, "response", None)
            if resp is not None:
                try:
                    body = resp.json()
                except Exception:  # noqa: BLE001
                    body = {}
                break
            cur = cur.__cause__
        message = body.get("error") or text
        code_hint = body.get("code")
        # HTTP-status classification fallback.
        status = None
        cur = exc
        while cur is not None:
            resp = getattr(cur, "response", None)
            if resp is not None:
                status = getattr(resp, "status_code", None)
                break
            cur = cur.__cause__

        if code_hint == "RATE_LIMITED" or status == 429:
            raise ClawnchError("rate_limited", message)
        if code_hint == "BYPASS_INVALID" or code_hint == "INSUFFICIENT_FUNDS":
            raise ClawnchError("bad_request", message)
        if status == 400:
            raise ClawnchError("bad_request", message)
        if status == 401 or status == 403:
            raise ClawnchError("no_credentials", message)
        if status == 404:
            raise ClawnchError("not_found", message)
        if status == 408:
            raise ClawnchError("challenge_expired", message)
        # Unclassified — let original exception propagate via the caller's
        # raise; we just couldn't translate it cleanly.

    def _require_key(self) -> None:
        with self._lock:
            if not self._api_key:
                raise ClawnchError(
                    "no_credentials",
                    "CLAWNCH_API_KEY is not set. Register an agent with "
                    "/register_agent, then export the issued key as "
                    "CLAWNCH_API_KEY in ~/.hermes/.env.",
                )

    # ── internals: captcha ──────────────────────────────────────────

    @staticmethod
    def _sign_message(message: str) -> str:
        """Sign ``message`` (EIP-191 ``personal_sign``) with the active wallet."""
        from clawmes.services.wallet import get_wallet_service

        svc = get_wallet_service()
        mode = svc.active_mode
        if mode is None:
            raise ClawnchError(
                "no_credentials",
                "No wallet connected. Run /connect first to sign the deploy challenge.",
            )
        try:
            return mode.sign_personal_message(message)
        except Exception as exc:  # noqa: BLE001
            raise ClawnchError("api_error", f"wallet signing failed: {exc}") from exc

    @staticmethod
    def _read_storage(*, address: str, slot: str) -> str:
        """Read a storage slot from a Base contract via the RPC service."""
        from clawmes.services.rpc import get_rpc_service

        rpc = get_rpc_service()
        try:
            raw = rpc._call(  # noqa: SLF001 — RpcService doesn't expose getStorageAt yet
                8453, "eth_getStorageAt", [address, slot, "latest"]
            )
        except Exception as exc:  # noqa: BLE001
            raise ClawnchError("api_error", f"eth_getStorageAt failed: {exc}") from exc
        if not isinstance(raw, str):
            raise ClawnchError("api_error", "eth_getStorageAt returned non-string")
        if not raw.startswith("0x"):
            raw = "0x" + raw
        # Storage slot reads are always 32-byte; pad if RPC returned a short
        # value (some providers strip leading zeros).
        hex_part = raw[2:].rjust(64, "0")
        return "0x" + hex_part

    @staticmethod
    def _compute_proof(*, signature: str, nonce: str, storage_value: str) -> str:
        """Compute ``keccak256(encodePacked(signature, nonce, storageValue))``.

        Matches the upstream verifier in ``api/deploy/confirm.ts`` which
        uses ``viem.encodePacked(['bytes', 'string', 'bytes32'], ...)``.
        Solidity ``encodePacked`` for ``bytes`` is the raw bytes (no
        length prefix); for ``string`` it's the UTF-8 bytes; for
        ``bytes32`` it's the 32-byte value. Concatenate, hash.
        """
        from Crypto.Hash import keccak

        def _hex_bytes(value: str) -> bytes:
            cleaned = value.removeprefix("0x")
            return bytes.fromhex(cleaned) if cleaned else b""

        packed = _hex_bytes(signature) + nonce.encode("utf-8") + _hex_bytes(storage_value)
        k = keccak.new(digest_bits=256)
        k.update(packed)
        return "0x" + k.hexdigest()


_instance: ClawnchService | None = None


def get_clawnch_service() -> ClawnchService:
    """Singleton accessor."""
    global _instance
    if _instance is None:
        _instance = ClawnchService()
    return _instance
