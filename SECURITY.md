# Security

clawmes signs blockchain transactions on your behalf. The security model below
explains what it protects against, what it doesn't, and how to recover if
something goes wrong.

## Reporting a Vulnerability

Email **security@clawn.ch**. Do not file public GitHub issues for security
problems.

We acknowledge reports within 48 hours and aim to ship fixes within 14 days
for critical issues, 30 days for high-severity, 90 days for everything else.
Disclosure happens after a fix ships and users have a reasonable upgrade
window (typically 30 days post-fix).

There is no bug bounty program at this stage. We may offer recognition in
release notes if the reporter wants it.

## Threat Model

clawmes runs as a Hermes Agent plugin in the user's terminal. The trust
boundaries:

| Component | Trusted? | Why |
|---|---|---|
| The user | Yes | They control the host, the keystore password, and the wallet. |
| Hermes Agent | Yes | clawmes is a plugin; if Hermes is compromised, clawmes can't defend against it. |
| The LLM | **No** | The LLM may be tricked by prompt injection in tool results, web content, or user input. Every write tool goes through the policy gate. |
| Tool args from the LLM | **No** | Validated, simulated (`eth_call`), and gated by user policies before broadcast. |
| RPC providers | Partially | They see all queries but cannot forge signed transactions. We send to default public nodes by default; users running real volume should configure private endpoints. |
| Block explorers | Partially | They see all addresses and tx hashes we look up. Read-only; cannot affect on-chain state. |
| WalletConnect relay | Partially | Used for pairing in WC mode. Sees tx requests in transit but the user signs on their own device. |
| Bankr custodial API | Yes (in Bankr mode) | The user has chosen to delegate custody. clawmes never sees a private key in this mode. |
| Local-key keystore | Yes | Encrypted at rest with the user's password. Plaintext only exists during the signing window. |

### What clawmes Defends Against

- **LLM hallucinating transactions.** Every write tool invokes `eth_estimateGas`
  before signing. If the simulation reverts (insufficient balance, paused
  contract, transfer to zero address), the tool returns `simulation_reverted`
  and never broadcasts. The wallet mode is not called.
- **Wrong-decimals fund loss.** The transfer tool uses `TokenDecimalsService.get_strict()`,
  which raises if the on-chain `decimals()` call fails. A silent fallback
  to 18 — for a 6-decimal token like USDC — would multiply the user's
  amount by 10^12.
- **Policy bypass.** The `@write_tool` gate runs before every signing path.
  Default policies confirm transfers above 0.05 ETH and rate-limit swaps
  above 20/hour. Users can add custom policies via `/policy "<NL rule>"`.
- **Readonly mode.** `/safemode` blocks all write tools without touching the
  wallet. Doesn't survive process restart by design — a compromise that
  flipped the flag wouldn't persist.
- **Keystore tampering.** AES-256-GCM authenticates the ciphertext, salt,
  and nonce. Any tampering produces a "wrong password" error, not a silently
  decrypted-but-wrong mnemonic.
- **Brute force.** scrypt with N = 2^17, r = 8, p = 1 — ~100ms per attempt
  on a 2024 CPU, which puts a $1M/password floor on cloud-rented brute force.

### What clawmes Does Not Defend Against

- **Compromised host.** If an attacker has code execution on the user's
  machine, they can read the keystore file, snoop on memory during signing,
  or replace the entire plugin. clawmes assumes the host is trustworthy.
- **Compromised Hermes Agent.** A malicious Hermes update could load a
  malicious clawmes. Verify Hermes installs from the canonical source.
- **Phishing through the LLM.** The LLM may relay a malicious link or
  address from a tool result that included attacker-controlled content
  (e.g. an explorer page with injected instructions). Users should treat
  any address the LLM proposes as untrusted and verify against an
  independent source for large transfers.
- **MEV / sandwich attacks.** clawmes does not currently route through
  private mempools (Flashbots Protect, etc.). A high-value swap submitted
  to a public mempool is exposed to standard MEV extraction.
- **Reorgs.** `wait_for_receipt` returns the first receipt seen. A reorg
  after that point can flip a confirmed tx to dropped. For high-value
  transactions, wait for additional confirmations manually before treating
  the tx as final.
- **Replacement transactions.** If the user replaces a clawmes-broadcast
  tx via their wallet, `wait_for_receipt` will time out rather than
  reporting on the replacement.
- **Quantum attacks.** secp256k1 signatures are not post-quantum secure.
  This is a property of every EVM wallet today, not a clawmes-specific gap.

## Local-Key Wallet Mode

The most security-sensitive mode. The user's mnemonic is encrypted at rest
using:

- **scrypt** for password-based key derivation (N=2^17, r=8, p=1, dkLen=32 — OWASP 2024 recommendation).
- **AES-256-GCM** for authenticated encryption with a random 12-byte nonce
  per encryption.
- **macOS Keychain** as a defense-in-depth layer when available, on top
  of the encrypted file.

The encrypted blob lives at `${HERMES_HOME}/clawmes/wallet/keystore.bin`
with file mode 0600. The mnemonic is the only secret stored at rest;
private keys are derived in memory at signing time.

`tests/wallet/test_keystore.py` cross-validates the scrypt KDF against
Python's stdlib `hashlib.scrypt`, asserts OWASP parameter compliance, and
verifies tamper detection across ciphertext, tag, nonce, and salt.

### Password Loss

There is no password recovery. If the user forgets their password:

1. They can re-import using the mnemonic shown once at wallet creation
   (`/connect_local mnemonic="..." password=<new>`). This produces a
   fresh keystore under the new password; the old one is overwritten.
2. If both password and mnemonic are lost, the funds in that wallet are
   inaccessible. Move to a new wallet via `/connect` (WalletConnect) and
   transfer any recoverable balance from a separate device that still
   has access.

### Password Rotation

`rotate_password()` in `clawmes.wallet.keystore` re-encrypts the keystore
under a new password with a fresh salt and nonce. The CLI doesn't expose
this yet — open an issue if you need it.

## WalletConnect Mode

Pairing happens via the WalletConnect v2 relay. clawmes generates a
pairing URI; the user scans it on their phone wallet to approve. After
approval, every signature request is forwarded to the phone for the
user to confirm.

clawmes never holds a private key in this mode. The relay sees the
encrypted payloads in transit but cannot decrypt them — WC v2 uses
end-to-end encrypted sessions with keys generated during pairing.

A `WALLETCONNECT_PROJECT_ID` is required (free at
https://cloud.walletconnect.com). Without it, `/connect` returns a
clear error.

## Bankr Mode

Bankr is a custodial provider. The user delegates custody by setting
`BANKR_API_KEY`. clawmes uses the API key to authenticate signing
requests; Bankr signs and broadcasts on the user's behalf.

Trust assumptions:

- The user trusts Bankr with their keys (this is the explicit mode
  contract — there is no non-custodial Bankr).
- The API key has the same effective authority as a private key. Treat
  it as a secret. Set it in `~/.hermes/.env` (not committed) or via
  the OS keychain.

## Network Allowlist

`clawmes.lib.http.http_post` enforces an allowlist of permitted hosts.
Any HTTP call to a host not on the list raises `NetworkAllowlistError`
before any bytes are sent. This bounds the surface that a compromised
LLM or tool can exfiltrate to.

The allowlist covers RPC providers (Alchemy, public nodes), DEX
aggregators (0x, 1inch, LiFi), price feeds (CoinGecko, DefiLlama),
explorers (Etherscan family), and the Bankr API. Users can extend it
via `clawmes.network_allowlist.extra_hosts` in `config.yaml`.

## Logs

Logs at `${HERMES_HOME}/clawmes/logs/clawmes.log` capture every tool
call, every RPC, and every command. They include addresses, chain IDs,
and tx hashes — all public information. The credential redactor
(`clawmes/services/credential_redactor.py`) strips known secret
patterns (API keys, mnemonics, private keys) from log messages
before write.

If you find a log that contains a secret that wasn't redacted, treat
that as a vulnerability and report it via security@clawn.ch.

## Recovery Checklist

If you suspect the keystore has been stolen:

1. **Move funds immediately.** Sign a transfer to a fresh wallet from a
   different device.
2. **Delete the keystore.** Remove `${HERMES_HOME}/clawmes/wallet/keystore.bin`
   and the matching macOS Keychain entry (Keychain Access → search
   "clawmes" → delete).
3. **Rotate API keys.** If you used Bankr, rotate `BANKR_API_KEY` from
   the Bankr dashboard. The old key was effective custody.
4. **Audit recent transactions** via the block explorer for any signed
   activity you didn't authorize. Tally any losses for an incident
   report.
5. **Report the incident.** Email security@clawn.ch with the affected
   address, suspected compromise vector, and recovery actions taken so
   we can update guidance for other users.

## Audit Status

clawmes has not received a third-party security audit. The keystore
construction is verified against Python stdlib's reference scrypt and
includes tamper-detection tests. The transfer path is covered by 50+
tests including simulation-revert refusal, decimals fail-loud, and
policy gate enforcement.

A formal audit is on the roadmap before exposing real funds at scale.
Public beta is positioned as a "kick the tires" stage — use small
amounts you can afford to lose while we collect feedback and field
reports.
