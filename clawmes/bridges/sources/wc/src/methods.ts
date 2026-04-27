/**
 * WC bridge RPC methods.
 *
 * Each method is an async function ``(params) => result``. Errors are
 * thrown as :class:`RpcError` (defined in index.ts) and the dispatcher
 * converts them to error envelopes on stdout.
 *
 * Methods exposed at this milestone (PRD §21.2):
 *
 *   pair                  — generate a pairing URI for the user's phone
 *   session_status        — current connected session(s)
 *   disconnect            — drop a session by topic (or all)
 *   request_signature     — eth_sendTransaction / eth_signTypedData_v4 / personal_sign
 *   switch_chain          — request the wallet switch to a different chain
 *   health                — bridge process health (in index.ts)
 */

import { getClient, WcConfigError } from "./wc-client.js";

export class MethodError extends Error {
  constructor(public code: string, message: string, public data?: unknown) {
    super(message);
  }
}

const DEFAULT_NAMESPACES = {
  eip155: {
    methods: [
      "eth_sendTransaction",
      "eth_signTransaction",
      "eth_sign",
      "personal_sign",
      "eth_signTypedData",
      "eth_signTypedData_v4",
    ],
    chains: ["eip155:1", "eip155:8453", "eip155:42161", "eip155:10", "eip155:137"],
    events: ["accountsChanged", "chainChanged"],
  },
};


// --- pair ----------------------------------------------------------------


export async function pair(_params: unknown): Promise<{ uri: string; topic: string }> {
  const client = await safeGetClient("pair");
  const { uri, approval } = await client.connect({
    requiredNamespaces: DEFAULT_NAMESPACES,
  });

  if (!uri) {
    throw new MethodError("no_uri", "WC connect returned no pairing URI");
  }

  // Don't await `approval` here — it resolves only when the user scans the
  // QR. We let the caller poll via `session_status` and emit a
  // `pairing_approved` notification when it finishes (handled by the
  // caller).
  void approval()
    .then((session) => {
      // Notification side-channel — emitted by index.ts via the
      // notification queue.
      process.stdout.write(
        JSON.stringify({
          method: "pairing_approved",
          params: { topic: session.topic, peer: session.peer.metadata },
        }) + "\n",
      );
    })
    .catch((err: unknown) => {
      process.stdout.write(
        JSON.stringify({
          method: "pairing_rejected",
          params: { reason: String(err) },
        }) + "\n",
      );
    });

  // Best-effort topic extraction — the URI is `wc:<topic>@2?...`
  const topic = uri.split(":")[1]?.split("@")[0] ?? "";
  return { uri, topic };
}


// --- session_status ------------------------------------------------------


export interface SessionStatus {
  connected: boolean;
  sessions: Array<{
    topic: string;
    peer: string;
    chains: string[];
    accounts: string[];
  }>;
}


export async function session_status(_params: unknown): Promise<SessionStatus> {
  const client = await safeGetClient("session_status");
  const sessions = client.session.values;
  return {
    connected: sessions.length > 0,
    sessions: sessions.map((s) => ({
      topic: s.topic,
      peer: s.peer.metadata.name,
      chains: Object.keys(s.namespaces).flatMap(
        (k) => s.namespaces[k]?.chains ?? [],
      ),
      accounts: Object.keys(s.namespaces).flatMap(
        (k) => s.namespaces[k]?.accounts ?? [],
      ),
    })),
  };
}


// --- disconnect ----------------------------------------------------------


export async function disconnect(params: { topic?: string }): Promise<{ disconnected: number }> {
  const client = await safeGetClient("disconnect");
  const sessions = client.session.values;
  const targets = params.topic
    ? sessions.filter((s) => s.topic === params.topic)
    : sessions;

  let count = 0;
  for (const s of targets) {
    try {
      await client.disconnect({
        topic: s.topic,
        reason: { code: 6000, message: "User disconnected" },
      });
      count += 1;
    } catch {
      // ignore — session might already be dead on the wallet side
    }
  }
  return { disconnected: count };
}


// --- request_signature ---------------------------------------------------


export interface RequestSignatureParams {
  method: string;
  params: unknown[];
  chain_id?: number;
  topic?: string;
  metadata?: Record<string, unknown>;
}


export async function request_signature(
  params: RequestSignatureParams,
): Promise<{ signature_or_hash: string }> {
  const client = await safeGetClient("request_signature");
  if (!params.method || !Array.isArray(params.params)) {
    throw new MethodError(
      "bad_request",
      "request_signature requires method (string) + params (array)",
    );
  }

  const sessions = client.session.values;
  const session = params.topic
    ? sessions.find((s) => s.topic === params.topic)
    : sessions[sessions.length - 1];
  if (!session) {
    throw new MethodError(
      "no_session",
      "no active WalletConnect session — pair first",
    );
  }

  const chainId = params.chain_id ?? 8453;
  const result = (await client.request({
    topic: session.topic,
    chainId: `eip155:${chainId}`,
    request: { method: params.method, params: params.params },
  })) as string;

  return { signature_or_hash: result };
}


// --- switch_chain --------------------------------------------------------


export async function switch_chain(params: {
  chain_id: number;
  topic?: string;
}): Promise<{ ok: boolean }> {
  const client = await safeGetClient("switch_chain");
  if (typeof params.chain_id !== "number") {
    throw new MethodError("bad_request", "switch_chain requires chain_id (number)");
  }
  const sessions = client.session.values;
  const session = params.topic
    ? sessions.find((s) => s.topic === params.topic)
    : sessions[sessions.length - 1];
  if (!session) {
    throw new MethodError("no_session", "no active session");
  }

  try {
    await client.request({
      topic: session.topic,
      chainId: `eip155:${params.chain_id}`,
      request: {
        method: "wallet_switchEthereumChain",
        params: [{ chainId: `0x${params.chain_id.toString(16)}` }],
      },
    });
    return { ok: true };
  } catch {
    return { ok: false };
  }
}


// --- helpers -------------------------------------------------------------


async function safeGetClient(methodName: string) {
  try {
    return await getClient();
  } catch (err) {
    if (err instanceof WcConfigError) {
      throw new MethodError("config_error", err.message);
    }
    throw new MethodError(
      "init_failed",
      `WC client init failed in ${methodName}: ${String(err)}`,
    );
  }
}
