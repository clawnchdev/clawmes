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
  * Rate limiting + spam controls (24h cooldown, ETH-bypass, the
    mandatory 1M $CLAWNCH launch burn) are enforced server-side; the
    open source plugin inherits them automatically.
  * Backend migration (Clanker -> ClawnchFactory v2) preserves the
    HTTP API per ``clawncher/migration-v2.md``; clawmes stays valid
    through the swap.

Robinhood Chain (chain id 4663) is the second launch surface. The
Clanker path does not exist there; launches run through Bags.fm behind
Clawnch's launch router:

  * ``POST /api/robinhood/ticket`` — non-custodial: returns an unsigned
    ``launch()`` tx (agent pays the Bags creation fee + gas) plus the
    EIP-712 ticket that proves Clawnch-agent provenance.
    See :meth:`ClawnchService.rh_ticket`.
  * ``POST /api/robinhood/launch`` — record the launch
    (``mode="confirm"``) or run it server-side from a verified ETH
    deposit (``mode="deposit"``). See :meth:`rh_confirm_launch` /
    :meth:`rh_deposit_launch`.
  * ``GET/POST /api/robinhood/claim`` — claimable fees + an unsigned
    ``BagsFeeShare.claim(true)`` tx. See :meth:`rh_claimable` /
    :meth:`rh_claim`.
  * ``GET /api/robinhood/launches`` — the RHC launch feed.

Auth: ``CLAWNCH_API_KEY`` env var. Issued by clawn.ch via the two-step
register flow. Unauthenticated calls are rejected by the launchpad,
so the service refuses to start premium ops until the key is present.
Reads (``get_launches``, ``rh_launches``, ``rh_claimable``) work
without a key.
"""

from __future__ import annotations

import os
import threading
from typing import Any

from clawmes.lib.addr import is_hex_address
from clawmes.lib.http import http_get, http_post
from clawmes.lib.logger import logger_for
from clawmes.services._base import Service

_log = logger_for("services.clawnch")

#: Base URL of the Clawnch HTTP API. Override via ``CLAWNCH_BASE_URL`` for
#: staging / local dev. The service uses ``/api/...`` paths underneath.
#: Defaults to the ``www`` canonical host: the apex ``clawn.ch`` 307-redirects
#: to ``www.clawn.ch`` and our HTTP client doesn't follow cross-host redirects,
#: so targeting the apex would fail every request.
_BASE_URL = os.environ.get("CLAWNCH_BASE_URL", "https://www.clawn.ch")

#: Source tag attached to every deploy made through clawmes. Lets the
#: launchpad render a "launched via clawmes" badge on launch detail pages.
#: Public attribution — observers can count clawmes-sourced launches.
_DEPLOY_SOURCE_TAG = "clawmes"

#: Chain id of Robinhood Chain — the Bags-powered launch surface.
RH_CHAIN_ID = 4663

#: $CLAWNCH ERC-20 on Robinhood Chain. Distinct from the Base token
#: (``0xa1F724…747be``) — the RHC deployment is itself a Bags.fm token.
#: Override via ``CLAWNCH_RH_TOKEN_ADDRESS`` for staging.
RH_CLAWNCH_TOKEN_DEFAULT = "0x6a50F139F3eD4C9c7bDa0D067c5Ed09De1EEBbeA"

#: Canonical RHC link bases — Blockscout explorer + Bags.fm trade pages.
RH_EXPLORER_BASE_URL = "https://robinhoodchain.blockscout.com"
RH_TRADE_BASE_URL = "https://bags.fm/token"

#: Minimum ETH deposit for the deposit launch path. Mirrors
#: ``DEPOSIT_MIN_WEI`` in clawn.ch's ``api/lib/launch-router.ts``; the live
#: Bags ``creationFee`` can raise the effective floor above this.
RH_DEPOSIT_MIN_WEI = 20_000_000_000_000_000

#: Name / symbol caps enforced by the RHC ticket + deposit endpoints
#: (narrower than the Base prepare path's 64 / 16).
_RH_NAME_MAX = 32
_RH_SYMBOL_MAX = 10

#: RHC upstream codes that pass through verbatim (already actionable) and
#: win over the generic HTTP-status classification in ``_reclassify`` —
#: a 403 ``not_claimer`` must not surface as ``no_credentials``.
_RH_PASSTHROUGH_CODES = frozenset(
    {
        "wallet_mismatch",
        "not_claimer",
        "no_fee_share",
        "duplicate_deposit",
        "deposit_invalid",
        "deposit_launch_failed",
        "tx_failed",
        "not_agentic_launch",
        "wrong_mode",
    }
)


def is_tx_hash(value: Any) -> bool:
    """True for a ``0x`` + 64 hex-char transaction hash."""
    if not isinstance(value, str) or not value.startswith("0x"):
        return False
    body = value[2:]
    return len(body) == 64 and all(c in "0123456789abcdefABCDEF" for c in body)


def rh_trade_url(token: str) -> str | None:
    """Bags.fm trade page for a Robinhood Chain token, or None if invalid."""
    if not isinstance(token, str) or not is_hex_address(token):
        return None
    return f"{RH_TRADE_BASE_URL}/{token}"


def rh_explorer_token_url(token: str) -> str | None:
    """Blockscout token page (Robinhood Chain), or None if invalid."""
    if not isinstance(token, str) or not is_hex_address(token):
        return None
    return f"{RH_EXPLORER_BASE_URL}/token/{token}"


def rh_explorer_tx_url(tx_hash: str) -> str | None:
    """Blockscout tx page (Robinhood Chain), or None if invalid."""
    if not is_tx_hash(tx_hash):
        return None
    return f"{RH_EXPLORER_BASE_URL}/tx/{tx_hash}"


class ClawnchError(RuntimeError):
    """Raised on Clawnch API failures.

    ``code`` classification mirrors the rest of clawmes' service errors:

      * ``bad_request`` — caller-side problem (missing field, malformed
        token params, HTTP 400).
      * ``no_credentials`` — API key missing or rejected (HTTP 401/403).
      * ``rate_limited`` — Clawnch's 24h cooldown or burst limit (HTTP 429).
      * ``burn_required`` — launch attempted without the mandatory
        1,000,000 $CLAWNCH burn (HTTP 402). ``meta`` carries the
        upstream requirements (``minBurnTokens``, ``burnAddress``).
      * ``challenge_expired`` — captcha not solved within the 5s window
        (HTTP 408).
      * ``not_found`` — launch / agent / challenge not found (HTTP 404).
      * ``unsupported_chain`` — the requested operation doesn't exist on
        the requested chain (e.g. a Base-only deploy path asked to run
        on Robinhood Chain).
      * ``api_error`` — generic upstream failure.

    Robinhood-chain responses carry a few extra caller-actionable codes
    through verbatim (``wallet_mismatch``, ``not_claimer``,
    ``no_fee_share``, ``duplicate_deposit``, ``deposit_invalid``,
    ``tx_failed``, ``not_agentic_launch``, ``tx_not_found``) — see
    :func:`ClawnchError._from_rh_body`.
    """

    def __init__(self, code: str, message: str, *, meta: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.meta: dict[str, Any] = meta or {}

    #: Upstream RHC ``code`` → clawmes classification. Codes not listed
    #: pass through verbatim (they're already actionable): e.g.
    #: ``not_claimer``, ``no_fee_share``, ``duplicate_deposit``.
    _RH_CODE_MAP: dict[str, str] = {
        "unauthorized": "no_credentials",
        "invalid_wallet": "bad_request",
        "invalid_name": "bad_request",
        "invalid_symbol": "bad_request",
        "invalid_fee_recipient": "bad_request",
        "invalid_tx_hash": "bad_request",
        "invalid_token": "bad_request",
        "invalid_address": "bad_request",
        "invalid_agent": "bad_request",
        "invalid_mode": "bad_request",
        "missing_required": "bad_request",
        "rate_limited": "rate_limited",
        "tx_not_found": "not_found",
        # Server-side or transient problems — the caller can't fix these
        # by changing the request.
        "misconfigured": "api_error",
        "store_unavailable": "api_error",
        "ticket_error": "api_error",
        "launch_error": "api_error",
        "claim_error": "api_error",
        "launches_error": "api_error",
    }

    @classmethod
    def _from_rh_body(cls, body: dict[str, Any]) -> ClawnchError:
        """Build a ClawnchError from an ``{ok: false, error, code}`` body."""
        code = str(body.get("code") or "api_error")
        message = str(body.get("error") or "Clawnch Robinhood request failed")
        meta = body.get("meta")
        return cls(
            cls._RH_CODE_MAP.get(code, code),
            message,
            meta=meta if isinstance(meta, dict) else {},
        )


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
        chain: str = "base",
    ) -> dict[str, Any]:
        """End-to-end deploy convenience: challenge -> solve -> confirm.

        Returns the confirm response on success. Raises ``ClawnchError``
        with a classified ``code`` on any step failure. ``burn_tx_hash``
        claims a vault allocation; ``bypass_tx_hash`` skips the 24h
        cooldown — they're independent and can both be supplied.

        **Base only.** This is the custodial Clanker path (captcha +
        server-side deployer). Robinhood Chain has no Clanker deployment:
        launches there run through the launch-router ticket / deposit
        flow (:meth:`rh_ticket` → :meth:`rh_confirm_launch`, or
        :meth:`rh_deposit_launch`). Asking for ``chain="robinhood"``
        here raises ``unsupported_chain`` rather than silently deploying
        through the Base path.
        """
        if self._is_rh_chain(chain):
            raise ClawnchError(
                "unsupported_chain",
                "The custodial deploy path is Base-only (Clanker). Robinhood "
                "Chain launches go through the Bags launch router: get an "
                "unsigned launch tx with rh_ticket (ticket path) or deposit "
                "ETH and call rh_deposit_launch (deposit path).",
            )
        if chain and chain.strip().lower() not in ("base", "8453"):
            raise ClawnchError(
                "bad_request",
                f"unknown chain {chain!r} — expected 'base' or 'robinhood'",
            )
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

    # ── non-custodial deploy (GET /api/prepare/deploy) ──────────────

    def prepare_deploy(
        self,
        *,
        from_address: str,
        name: str,
        symbol: str,
        description: str | None = None,
        image: str | None = None,
        twitter: str | None = None,
        website: str | None = None,
        telegram: str | None = None,
        farcaster: str | None = None,
        discord: str | None = None,
        burn_tx_hash: str | None = None,
        chain: str | None = None,
    ) -> dict[str, Any]:
        """Get unsigned factory calldata for a non-custodial deploy.

        ``chain`` selects the launch surface. Pass ``None`` (default) or
        ``"base"`` for the Base/Clanker path.

        **Robinhood Chain raises ``unsupported_chain``.** RHC launches
        run through the launch router (``POST /api/robinhood/ticket`` /
        ``POST /api/robinhood/launch``) — ``/api/prepare/deploy`` serves
        the Base path only and ignores a ``chain`` query param (the
        clawn.ch server routes by its own deployment env), so honoring a
        robinhood request here could hand back Base calldata. Use
        :meth:`rh_ticket` / :meth:`rh_confirm_launch` /
        :meth:`rh_deposit_launch` instead.

        Returns the envelope shape:

            {
                "ok": True,
                "data": {"to": "0x…", "data": "0x…", "value": "0x…", "chainId": 8453},
                "meta": {
                    "platformFeeBps": 2000,
                    "userFeeBps": 8000,
                    "vaultPercentage": 0,
                    "chain": "base",
                    ...
                },
            }

        On any 4xx (``ok: false``) Clawnch error, raises ``ClawnchError``
        with the upstream ``code`` mapped to one of the standard
        clawmes error classifications.

        Unlike :meth:`deploy`, this path is non-custodial:
        - No API key required (``/api/prepare/deploy`` is public).
        - No captcha solving — the wallet signs the deploy tx directly.
        - The user's wallet pays gas.
        - Same 20% platform fee preserved in the rewards array.

        ``burn_tx_hash`` is **mandatory upstream on Base**. Every launch
        requires a verified 1,000,000+ $CLAWNCH burn from
        ``from_address`` to the dead address within 24h. Calling without
        one raises ``ClawnchError`` with ``code="burn_required"`` whose
        ``meta`` carries ``minBurnTokens`` + ``burnAddress``.
        """
        if not from_address:
            raise ClawnchError("bad_request", "from_address is required")
        if not name:
            raise ClawnchError("bad_request", "name is required")
        if not symbol:
            raise ClawnchError("bad_request", "symbol is required")
        if self._is_rh_chain(chain):
            raise ClawnchError(
                "unsupported_chain",
                "prepare_deploy is Base-only: /api/prepare/deploy does not "
                "route by the 'chain' query param (clawn.ch decides the "
                "backend server-side), so a Robinhood request could come "
                "back as Base calldata. Robinhood Chain launches use the "
                "launch router instead — POST /api/robinhood/ticket "
                "(rh_ticket) or POST /api/robinhood/launch "
                "(rh_confirm_launch / rh_deposit_launch).",
            )
        if chain and chain.strip().lower() not in ("base", "8453"):
            raise ClawnchError(
                "bad_request",
                f"unknown chain {chain!r} — expected 'base' or 'robinhood'",
            )

        params: dict[str, str] = {
            "from": from_address,
            "name": name,
            "symbol": symbol,
        }
        if description:
            params["description"] = description
        if image:
            params["image"] = image
        if twitter:
            params["twitter"] = twitter
        if website:
            params["website"] = website
        if telegram:
            params["telegram"] = telegram
        if farcaster:
            params["farcaster"] = farcaster
        if discord:
            params["discord"] = discord
        if burn_tx_hash:
            params["burnTxHash"] = burn_tx_hash

        # Public endpoint — no auth header sent.
        body = self._get("/api/prepare/deploy", params=params)
        if not isinstance(body, dict):
            raise ClawnchError("api_error", "prepare_deploy returned non-dict body")
        if body.get("ok") is False:
            code = str(body.get("code") or "api_error")
            msg = str(body.get("error") or "prepare_deploy failed")
            # Map a couple of upstream codes to clawmes' classification.
            if code == "rate_limited":
                raise ClawnchError("rate_limited", msg)
            if code == "burn_required":
                meta = body.get("meta")
                raise ClawnchError(
                    "burn_required",
                    msg,
                    meta=meta if isinstance(meta, dict) else {},
                )
            if code in (
                "invalid_from",
                "invalid_name",
                "invalid_symbol",
                "missing_required",
                "invalid_burn",
            ):
                raise ClawnchError("bad_request", msg)
            raise ClawnchError("api_error", msg)
        return body

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

    def get_burn_config(self, *, chain: str = "base") -> dict[str, Any]:
        """Return the $CLAWNCH burn config used by ``/burn`` + ``/launch burn``.

        ``chain`` selects the launch surface:

          * ``"base"`` (default) — Clanker path. Returns the token
            address (the CLAWNCH ERC-20), the burn address (dead
            address — 0x…dEaD), and the minimum burn amount in whole
            tokens. The frontend uses these to construct a
            ``transfer(burn_address, amount * 1e18)`` calldata that the
            active wallet signs.
          * ``"robinhood"`` — Bags path. **No burn** (Bags doesn't
            burn); launches pay a live ETH creation fee instead, so
            ``burn_address`` is ``None`` and ``min_burn_tokens`` is 0.
            Returns the RHC CLAWNCH token
            (``0x6a50F139F3eD4C9c7bDa0D067c5Ed09De1EEBbeA``) — a
            *different* deployment from the Base one.

        Base semantics: the minimum burn is **required for every
        launch** (deploys without a verified burn are rejected with
        ``burn_required``); it doubles as the vault claim (1M = 1%
        vault, up to 10M = 10%).

        Stable values today (override via env for staging):

          * ``CLAWNCH_TOKEN_ADDRESS``     — default ``0xa1F724…747be`` (Base)
          * ``CLAWNCH_BURN_ADDRESS``      — default ``0x000…dEaD`` (Base)
          * ``CLAWNCH_MIN_BURN_TOKENS``   — default ``1_000_000`` (Base)
          * ``CLAWNCH_RH_TOKEN_ADDRESS``  — default ``0x6a50F1…EBbeA`` (RHC)
        """
        key = (chain or "base").strip().lower()
        if key in ("robinhood", "rh", "4663", "robinhood-chain"):
            return {
                "chain": "robinhood",
                "chain_id": RH_CHAIN_ID,
                "token_address": os.environ.get(
                    "CLAWNCH_RH_TOKEN_ADDRESS", RH_CLAWNCH_TOKEN_DEFAULT
                ),
                "burn_address": None,
                "min_burn_tokens": 0,
                "burn_required": False,
                "note": (
                    "Robinhood Chain launches (Bags.fm) do not burn $CLAWNCH. "
                    "The launch cost is the live Bags creation fee "
                    "(~0.02 ETH, read from the factory at launch time)."
                ),
            }
        if key not in ("base", "8453"):
            raise ClawnchError(
                "bad_request",
                f"unknown chain {chain!r} — expected 'base' or 'robinhood'",
            )
        return {
            "chain": "base",
            "chain_id": 8453,
            "token_address": os.environ.get(
                "CLAWNCH_TOKEN_ADDRESS",
                "0xa1F72459dfA10BAD200Ac160eCd78C6b77a747be",
            ),
            "burn_address": os.environ.get(
                "CLAWNCH_BURN_ADDRESS",
                "0x000000000000000000000000000000000000dEaD",
            ),
            "min_burn_tokens": int(os.environ.get("CLAWNCH_MIN_BURN_TOKENS", "1000000")),
            "burn_required": True,
        }

    def rh_token_info(self) -> dict[str, Any]:
        """$CLAWNCH on Robinhood Chain: address + trade / explorer links.

        The RHC deployment (``0x6a50F139F3eD4C9c7bDa0D067c5Ed09De1EEBbeA``)
        is distinct from the Base token; use this to render per-chain
        token cards without guessing which deployment is live where.
        """
        token = os.environ.get("CLAWNCH_RH_TOKEN_ADDRESS", RH_CLAWNCH_TOKEN_DEFAULT)
        return {
            "chain": "robinhood",
            "chain_id": RH_CHAIN_ID,
            "symbol": "CLAWNCH",
            "token_address": token,
            "trade_url": rh_trade_url(token),
            "explorer_url": rh_explorer_token_url(token),
        }

    # ── Robinhood Chain: launch ticket (non-custodial) ──────────────

    #: Chain ids/short names accepted where a caller names an RHC chain.
    _RH_CHAIN_KEYS = frozenset({"robinhood", "rh", "4663", "robinhood-chain"})

    @classmethod
    def _is_rh_chain(cls, chain: str | None) -> bool:
        if not chain:
            return False
        return chain.strip().lower() in cls._RH_CHAIN_KEYS

    @staticmethod
    def _require_address(value: str, field: str) -> str:
        if not value or not is_hex_address(value):
            raise ClawnchError("bad_request", f"{field} must be a 0x… address")
        return value

    @classmethod
    def _validate_rh_token_fields(cls, name: str, symbol: str) -> None:
        if not name:
            raise ClawnchError("bad_request", "name is required")
        if not symbol:
            raise ClawnchError("bad_request", "symbol is required")
        if len(name) > _RH_NAME_MAX:
            raise ClawnchError("bad_request", f"name too long (max {_RH_NAME_MAX} chars)")
        if len(symbol) > _RH_SYMBOL_MAX:
            raise ClawnchError("bad_request", f"symbol too long (max {_RH_SYMBOL_MAX} chars)")

    def rh_ticket(
        self,
        *,
        agent_wallet: str,
        name: str,
        symbol: str,
        description: str | None = None,
        image: str | None = None,
        fee_recipient: str | None = None,
    ) -> dict[str, Any]:
        """Issue a Robinhood Chain launch ticket.

        POST ``/api/robinhood/ticket`` (Bearer auth required — the
        ticket is bound to the registered agent wallet, which must also
        be ``agent_wallet``). Returns the upstream envelope::

            {
                "ok": True,
                "data": {"to": "0x…", "data": "0x…", "value": "0x…", "chainId": 4663},
                "ticket": {"agent", "feeRecipient", "paramsHash", "nonce",
                           "deadline", "signature"},
                "meta": {"backend": "bags", "chain": "robinhood",
                         "router", "depositAddress", "creationFeeWei", ...},
            }

        The agent signs and sends ``data`` to ``to`` with ``value`` from
        its own wallet (the Bags creation fee + gas), then records the
        launch with :meth:`rh_confirm_launch`.

        Refuses to return a ticket whose ``chainId`` is not 4663 — a
        wrong-chain ticket would otherwise be signed and broadcast on
        the wrong network.
        """
        self._require_key()
        wallet = self._require_address(agent_wallet, "agent_wallet")
        self._validate_rh_token_fields(name, symbol)
        if fee_recipient:
            self._require_address(fee_recipient, "fee_recipient")
        body: dict[str, Any] = {"agentWallet": wallet, "name": name, "symbol": symbol}
        if description:
            body["description"] = description
        if image:
            body["image"] = image
        if fee_recipient:
            body["feeRecipient"] = fee_recipient
        resp = self._rh_post("/api/robinhood/ticket", body)
        self._assert_rh_chain(resp)
        return resp

    def rh_confirm_launch(self, *, tx_hash: str) -> dict[str, Any]:
        """Record a ticket-path launch after the agent broadcast it.

        POST ``/api/robinhood/launch`` with ``mode="confirm"`` (Bearer
        auth required). The server verifies the tx receipt on Robinhood
        Chain, parses the router's ``AgenticLaunch`` + ``TokenCreated``
        events, and stores the launch. Returns ``{ok, launch}`` (or
        ``{ok, alreadyRecorded: True, launch}`` on a replay).

        ``tx_hash`` is the hash of the transaction the agent sent from
        its own wallet — not the deposit path (see
        :meth:`rh_deposit_launch`).
        """
        self._require_key()
        if not is_tx_hash(tx_hash):
            raise ClawnchError("bad_request", "tx_hash must be a 0x + 64 hex tx hash")
        resp = self._rh_post("/api/robinhood/launch", {"mode": "confirm", "txHash": tx_hash})
        return resp

    def rh_deposit_launch(
        self,
        *,
        deposit_tx_hash: str,
        agent_wallet: str,
        name: str,
        symbol: str,
        description: str | None = None,
        image: str | None = None,
    ) -> dict[str, Any]:
        """Launch via the deposit path: the deposit is already on-chain.

        POST ``/api/robinhood/launch`` with ``mode="deposit"`` (Bearer
        auth required). The agent first sends a plain ETH transfer to
        the router's deposit address (returned as ``meta.depositAddress``
        by :meth:`rh_ticket`, minimum ``RH_DEPOSIT_MIN_WEI`` or the live
        Bags creation fee, whichever is larger); Clawnch then deploys
        through Bags with the agent as sole fee claimer and registers
        provenance.

        ``deposit_tx_hash`` must be the plain transfer from
        ``agent_wallet``; the server verifies sender, recipient, value,
        age, and single-use before deploying. Returns ``{ok, launch}``.
        """
        self._require_key()
        wallet = self._require_address(agent_wallet, "agent_wallet")
        self._validate_rh_token_fields(name, symbol)
        if not is_tx_hash(deposit_tx_hash):
            raise ClawnchError("bad_request", "deposit_tx_hash must be a 0x + 64 hex tx hash")
        body: dict[str, Any] = {
            "mode": "deposit",
            "depositTxHash": deposit_tx_hash,
            "agentWallet": wallet,
            "name": name,
            "symbol": symbol,
        }
        if description:
            body["description"] = description
        if image:
            body["image"] = image
        # The deposit sender must also be the fee recipient (anti-hijack
        # rule server-side) — the agent wallet plays both roles here.
        resp = self._rh_post("/api/robinhood/launch", body)
        return resp

    # ── Robinhood Chain: fee claims ─────────────────────────────────

    def rh_claimable(self, *, token: str, address: str) -> dict[str, Any]:
        """Read a wallet's claimable RHC fees for a token (public).

        GET ``/api/robinhood/claim?token=…&address=…``. Returns the
        token's ``BagsFeeShare`` address, whether ``address`` is one of
        its claimers (with the claimer ``bps``), the claimable amount in
        wei, and — when the address is a claimer — an unsigned
        ``BagsFeeShare.claim(true)`` tx under ``claim``.
        """
        token_addr = self._require_address(token, "token")
        wallet = self._require_address(address, "address")
        return self._rh_get("/api/robinhood/claim", params={"token": token_addr, "address": wallet})

    def rh_claim(self, *, token: str) -> dict[str, Any]:
        """Unsigned claim tx for the agent's own accrued RHC fees.

        POST ``/api/robinhood/claim`` (Bearer auth required). Fees
        accrue in WETH inside the token's ``BagsFeeShare`` and are
        claimed by the claimer itself; Clawnch never claims for the
        agent. Returns ``{ok, ready, claimableWei, claim: {to, data,
        value, chainId}, meta}`` — the agent signs and sends ``claim``
        from its registered wallet to receive native ETH.

        ``ready`` is ``False`` with ``claim: None`` when there is
        nothing claimable yet (not an error). Raises ``not_claimer``
        when the registered wallet isn't a claimer for the token.
        """
        self._require_key()
        token_addr = self._require_address(token, "token")
        resp = self._rh_post("/api/robinhood/claim", {"token": token_addr})
        claim = resp.get("claim")
        if isinstance(claim, dict) and claim.get("chainId") not in (None, RH_CHAIN_ID):
            raise ClawnchError(
                "api_error",
                f"claim tx targets chain {claim.get('chainId')}, expected {RH_CHAIN_ID} "
                "(Robinhood Chain) — refusing to hand back a wrong-chain tx",
            )
        return resp

    # ── Robinhood Chain: launch feed ────────────────────────────────

    def rh_launches(
        self,
        *,
        agent: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Read the Robinhood Chain launch feed (public, newest first).

        GET ``/api/robinhood/launches``. Optionally filtered to one
        agent wallet. Covers both ticket and deposit-path launches;
        every row is decorated with ``trade_url`` (bags.fm) and
        ``explorer_url`` / ``tx_url`` / ``router_tx_url`` (Blockscout)
        so callers don't rebuild links per surface.
        """
        if agent is not None and agent != "":
            self._require_address(agent, "agent")
        try:
            limit_n = int(limit)
            offset_n = int(offset)
        except (TypeError, ValueError) as exc:
            raise ClawnchError("bad_request", "limit and offset must be integers") from exc
        if limit_n < 1:
            raise ClawnchError("bad_request", "limit must be >= 1")
        if offset_n < 0:
            raise ClawnchError("bad_request", "offset must be >= 0")
        params: dict[str, str] = {
            "limit": str(min(limit_n, 200)),
            "offset": str(offset_n),
        }
        if agent:
            params["agent"] = agent
        resp = self._rh_get("/api/robinhood/launches", params=params)
        launches = resp.get("launches")
        if isinstance(launches, list):
            resp["launches"] = [
                self._decorate_rh_launch(row) for row in launches if isinstance(row, dict)
            ]
        return resp

    @staticmethod
    def _decorate_rh_launch(row: dict[str, Any]) -> dict[str, Any]:
        """Add canonical RHC links to a launch-feed row (in place).

        Upstream already emits ``tradeUrl`` / ``explorerUrl`` / ``txUrl``
        camelCase variants; we add snake_case duplicates only when
        absent so every clawmes surface reads one spelling. Values that
        can't be built (malformed token / hash) are skipped rather than
        emitted as ``null``.
        """
        token = row.get("token")
        if token and not row.get("trade_url"):
            url = rh_trade_url(token)
            if url:
                row["trade_url"] = url
        if token and not row.get("explorer_url"):
            url = rh_explorer_token_url(token)
            if url:
                row["explorer_url"] = url
        tx_hash = row.get("txHash")
        if tx_hash and not row.get("tx_url"):
            url = rh_explorer_tx_url(tx_hash)
            if url:
                row["tx_url"] = url
        router_tx = row.get("routerTxHash")
        if router_tx and not row.get("router_tx_url"):
            url = rh_explorer_tx_url(router_tx)
            if url:
                row["router_tx_url"] = url
        row.setdefault("chain", "robinhood")
        row.setdefault("chain_id", RH_CHAIN_ID)
        return row

    # ── internals: Robinhood request helpers ────────────────────────

    def _rh_post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        """Authed POST against the RHC API; raise on ``ok: false``."""
        return self._ensure_rh_ok(self._post(path, body, auth=True))

    def _rh_get(self, path: str, *, params: dict[str, str]) -> dict[str, Any]:
        """GET against the RHC API; raise on ``ok: false``."""
        return self._ensure_rh_ok(self._get(path, params=params))

    @staticmethod
    def _ensure_rh_ok(resp: Any) -> dict[str, Any]:
        if not isinstance(resp, dict):
            raise ClawnchError(
                "api_error",
                f"Robinhood API returned non-dict body: {type(resp).__name__}",
            )
        if resp.get("ok") is False:
            raise ClawnchError._from_rh_body(resp)
        return resp

    @staticmethod
    def _assert_rh_chain(resp: dict[str, Any]) -> None:
        """Refuse a response that isn't bound to Robinhood Chain (4663)."""
        raw_data = resp.get("data")
        data: dict[str, Any] = raw_data if isinstance(raw_data, dict) else {}
        chain_id = data.get("chainId") or resp.get("chainId")
        if chain_id is not None and int(chain_id) != RH_CHAIN_ID:
            raise ClawnchError(
                "api_error",
                f"ticket targets chain {chain_id}, expected {RH_CHAIN_ID} "
                "(Robinhood Chain) — refusing to return a wrong-chain launch tx",
            )
        raw_meta = resp.get("meta")
        meta: dict[str, Any] = raw_meta if isinstance(raw_meta, dict) else {}
        chain = meta.get("chain")
        if chain is not None and str(chain).lower() != "robinhood":
            raise ClawnchError(
                "api_error",
                f"ticket meta.chain={chain!r}, expected 'robinhood' — refusing to "
                "return a wrong-chain launch tx",
            )

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

    def _get(
        self,
        path: str,
        *,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        url = self._base_url + path
        headers: dict[str, str] = {}
        with self._lock:
            key = self._api_key
        if key:
            headers["Authorization"] = f"Bearer {key}"
        try:
            return http_get(url, params=params, headers=headers, timeout=15.0)
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
        if code_hint in ("burn_required", "BURN_PAYMENT_REQUIRED") or status == 402:
            # Mandatory 1M $CLAWNCH burn missing. Carry the upstream
            # ``meta`` block (minBurnTokens, burnAddress) so the UX can
            # render exact burn instructions.
            meta = body.get("meta")
            raise ClawnchError(
                "burn_required",
                message,
                meta=meta if isinstance(meta, dict) else {},
            )
        # Robinhood-chain code hints win over the generic HTTP-status
        # mapping: e.g. a 403 ``not_claimer`` must not be misreported as
        # ``no_credentials`` (the key IS valid — the wallet just isn't in
        # the token's claimer set).
        if isinstance(code_hint, str) and code_hint in ClawnchError._RH_CODE_MAP:
            raise ClawnchError._from_rh_body(body)
        if isinstance(code_hint, str) and code_hint in _RH_PASSTHROUGH_CODES:
            raise ClawnchError._from_rh_body(body)
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
