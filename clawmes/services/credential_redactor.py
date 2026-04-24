"""Credential redactor — scans tool output and CLI display strings for
secrets and replaces them with sanitized markers before they reach the
LLM, the user's terminal, or any messaging channel.

Patterns detected:

  * **Hex private keys** — 64 hex chars after ``0x``, when the
    surrounding context suggests a key (``private`` / ``priv`` / ``key``
    / ``mnemonic`` / ``seed`` keyword nearby). Tx hashes are also 64
    hex chars; we use the keyword heuristic to tell them apart and err
    on the side of redacting when ambiguous.
  * **BIP-39 mnemonics** — 12 / 15 / 18 / 24 consecutive lowercase
    words drawn from the BIP-39 English wordlist. The wordlist itself
    is bundled as ``clawmes/data/bip39_wordlist.txt``; if it's absent
    we skip the check.
  * **API key prefixes** — ``sk-ant-…``, ``sk-or-…``, ``sk-…``,
    ``xoxb-…``, ``ghp_…``, ``ghs_…``, ``gho_…`` (followed by ≥ 16
    URL-safe chars).
  * **WalletConnect v2 pairing URIs** — ``wc:<uuid>@2…``.
  * **Bankr session tokens** — ``bankr_sess_…`` (≥ 32 chars).

Every redaction is logged to
``${HERMES_HOME}/clawmes/logs/redaction.log`` with the **SHA-256 of
the redacted content** (not the content itself) so post-incident review
can confirm a leak was caught without re-leaking the secret.
"""

from __future__ import annotations

import hashlib
import re
import threading
from dataclasses import dataclass
from pathlib import Path

from clawmes.lib.logger import logger_for
from clawmes.lib.paths import logs_dir
from clawmes.services._base import Service

_log = logger_for("services.credential_redactor")

# --- Pattern definitions --------------------------------------------------

# 64 hex chars (a private key OR a tx hash). We disambiguate via context.
_HEX64_RE = re.compile(r"\b0x[0-9a-fA-F]{64}\b")

# Words near a 64-hex match that flag it as a private key (case-insensitive).
_PRIVATE_KEY_HINTS = (
    "private key",
    "private_key",
    "privatekey",
    "secret key",
    "secret_key",
    "secretkey",
    "priv key",
    "priv_key",
    "raw key",
    "signing key",
    "mnemonic",
    "seed phrase",
    "seedphrase",
    "wallet seed",
)

# Common LLM/cloud API key prefixes. The trailing run accepts URL-safe
# chars that show up in real keys.
_API_KEY_RE = re.compile(
    r"\b("
    r"sk-ant-[A-Za-z0-9_\-]{20,}"
    r"|sk-or-[A-Za-z0-9_\-]{20,}"
    r"|sk-[A-Za-z0-9_\-]{32,}"
    r"|xoxb-[A-Za-z0-9_\-]{20,}"
    r"|gh[pso]_[A-Za-z0-9_]{16,}"
    r")\b"
)

# WalletConnect v2 pairing URI. The relay-protocol parameters can carry
# the symKey, so the entire URI must be redacted.
_WC_URI_RE = re.compile(
    r"\bwc:[A-Za-z0-9]{32,}@2(?:\?[A-Za-z0-9_\-=&%.:/]+)?"
)

# Bankr session token (placeholder format — Bankr's actual format may
# evolve; redactor errs on the side of capturing).
_BANKR_TOKEN_RE = re.compile(r"\bbankr_sess_[A-Za-z0-9_\-]{32,}\b")


@dataclass(frozen=True)
class Redaction:
    """Record of a single redaction event."""

    pattern: str
    redacted_sha256: str
    context: str


# --- Service --------------------------------------------------------------


class CredentialRedactor(Service):
    """Stateless redaction service.

    Stateless except for the audit log. Safe to call from any thread.
    """

    id = "clawmes.credential_redactor"

    def __init__(self) -> None:
        self._log_lock = threading.Lock()
        self._wordlist: frozenset[str] | None = None
        self._wordlist_loaded = False

    def start(self) -> None:
        # Lazy-load the BIP-39 wordlist on first use so an absent file
        # doesn't break plugin startup.
        pass

    def stop(self) -> None:
        pass

    def scan_and_redact(self, text: str, *, context: str = "") -> str:
        """Return ``text`` with credentials replaced by sanitized markers.

        ``context`` is a free-form string ("tool_result", "cli_output", etc.)
        recorded in the audit log so we can correlate redactions to their
        source path.
        """
        if not text:
            return text

        out = text
        out = self._redact_api_keys(out, context=context)
        out = self._redact_wc_uris(out, context=context)
        out = self._redact_bankr_tokens(out, context=context)
        out = self._redact_private_keys(out, context=context)
        out = self._redact_mnemonics(out, context=context)
        return out

    # --- pattern handlers ---

    def _redact_api_keys(self, text: str, *, context: str) -> str:
        return _API_KEY_RE.sub(
            lambda m: self._record_and_marker(m.group(0), "api_key", context),
            text,
        )

    def _redact_wc_uris(self, text: str, *, context: str) -> str:
        return _WC_URI_RE.sub(
            lambda m: self._record_and_marker(m.group(0), "walletconnect_uri", context),
            text,
        )

    def _redact_bankr_tokens(self, text: str, *, context: str) -> str:
        return _BANKR_TOKEN_RE.sub(
            lambda m: self._record_and_marker(m.group(0), "bankr_token", context),
            text,
        )

    def _redact_private_keys(self, text: str, *, context: str) -> str:
        """Redact 64-hex strings that look like keys vs. tx hashes."""
        text_lower = text.lower()

        def replace(match: re.Match[str]) -> str:
            value = match.group(0)
            if not _looks_like_private_key(text_lower, match.start(), match.end()):
                return value  # likely a tx hash — leave it alone
            return self._record_and_marker(value, "private_key", context)

        return _HEX64_RE.sub(replace, text)

    def _redact_mnemonics(self, text: str, *, context: str) -> str:
        wordlist = self._load_wordlist()
        if wordlist is None:
            return text  # wordlist unavailable — skip mnemonic detection

        # Find any run of 12/15/18/24 consecutive lowercase words from the wordlist.
        pattern = re.compile(r"\b[a-z]{3,8}(?:\s+[a-z]{3,8}){11,23}\b")

        def replace(match: re.Match[str]) -> str:
            words = match.group(0).split()
            if len(words) not in (12, 15, 18, 24):
                return match.group(0)
            if not all(w in wordlist for w in words):
                return match.group(0)
            return self._record_and_marker(match.group(0), "mnemonic", context)

        return pattern.sub(replace, text)

    # --- audit log + marker ---

    def _record_and_marker(self, value: str, pattern: str, context: str) -> str:
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
        try:
            self._append_audit(Redaction(pattern=pattern, redacted_sha256=digest, context=context))
        except Exception:
            # Audit log failure must never block redaction.
            _log.exception("credential_redactor: audit log append failed")
        return f"[REDACTED:{pattern}:{digest[:8]}]"

    def _append_audit(self, redaction: Redaction) -> None:
        path = self._audit_log_path()
        line = (
            f"pattern={redaction.pattern} "
            f"sha256={redaction.redacted_sha256} "
            f"context={redaction.context}\n"
        )
        with self._log_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line)

    @staticmethod
    def _audit_log_path() -> Path:
        return logs_dir() / "redaction.log"

    # --- wordlist loading ---

    def _load_wordlist(self) -> frozenset[str] | None:
        if self._wordlist_loaded:
            return self._wordlist
        self._wordlist_loaded = True
        path = Path(__file__).parent.parent / "data" / "bip39_wordlist.txt"
        if not path.exists():
            _log.debug("BIP-39 wordlist absent at %s; skipping mnemonic detection", path)
            self._wordlist = None
            return None
        try:
            words = {
                line.strip().lower()
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            }
        except OSError:
            _log.exception("BIP-39 wordlist read failed")
            self._wordlist = None
            return None
        if len(words) < 2000:
            _log.warning("BIP-39 wordlist suspicious length (%d); ignoring", len(words))
            self._wordlist = None
            return None
        self._wordlist = frozenset(words)
        return self._wordlist


# --- module-level accessor + helper ---------------------------------------


_instance: CredentialRedactor | None = None


def get_credential_redactor() -> CredentialRedactor:
    global _instance
    if _instance is None:
        _instance = CredentialRedactor()
    return _instance


def scan_and_redact(text: str, *, context: str = "") -> str:
    """Module-level convenience wrapper.

    Delegates to the singleton ``CredentialRedactor`` so the hook layer
    can call ``credential_redactor.scan_and_redact(text)`` without
    holding its own reference.
    """
    return get_credential_redactor().scan_and_redact(text, context=context)


# --- private helpers ------------------------------------------------------


def _looks_like_private_key(
    text_lower: str,
    match_start: int,
    match_end: int,
    window: int = 60,
) -> bool:
    """Return True iff a 64-hex match has 'private key' / 'mnemonic' / etc.
    in its surrounding context."""
    head = max(0, match_start - window)
    tail = min(len(text_lower), match_end + window)
    surrounding = text_lower[head:tail]
    return any(hint in surrounding for hint in _PRIVATE_KEY_HINTS)
