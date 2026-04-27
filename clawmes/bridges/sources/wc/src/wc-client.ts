/**
 * Lazy WalletConnect SignClient wrapper.
 *
 * The SignClient init is async, hits the WC relay, and requires a project
 * ID. We defer init until the first method that actually needs it so the
 * bridge can boot and answer `health` even without WALLETCONNECT_PROJECT_ID
 * set.
 *
 * The SignClient is a singleton per process — WC v2 doesn't support
 * multiple instances cleanly.
 */

import { SignClient } from "@walletconnect/sign-client";

let _client: InstanceType<typeof SignClient> | null = null;
let _initPromise: Promise<InstanceType<typeof SignClient>> | null = null;

const METADATA = {
  name: "Clawmes",
  description: "Clawmes — Hermes Agent for crypto",
  url: "https://clawnch.dev",
  icons: ["https://clawnch.dev/icon.png"],
};

export class WcConfigError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "WcConfigError";
  }
}

export async function getClient(): Promise<InstanceType<typeof SignClient>> {
  if (_client !== null) return _client;
  if (_initPromise !== null) return _initPromise;

  const projectId = process.env.WALLETCONNECT_PROJECT_ID;
  if (!projectId) {
    throw new WcConfigError(
      "WALLETCONNECT_PROJECT_ID not set. Get one at https://cloud.walletconnect.com",
    );
  }

  _initPromise = SignClient.init({
    projectId,
    metadata: METADATA,
  })
    .then((c) => {
      _client = c;
      _initPromise = null;
      return c;
    })
    .catch((err) => {
      _initPromise = null;
      throw err;
    });

  return _initPromise;
}

export function _resetForTesting(): void {
  _client = null;
  _initPromise = null;
}
