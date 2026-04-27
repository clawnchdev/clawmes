/**
 * clawmes-wc-bridge — WalletConnect v2 stdio bridge.
 *
 * Reads JSON-Line RPC requests from stdin, writes responses to stdout,
 * and emits server-pushed notifications also via stdout. The Python
 * client at clawmes/bridges/wc_client.py drives this process.
 *
 * Wire format (one record per line, terminated with \n):
 *
 *   Request:      {"id": "<uuid>", "method": "<name>", "params": {...}}
 *   Response OK:  {"id": "<uuid>", "result": <any>}
 *   Response err: {"id": "<uuid>", "error": {"code": "<str>", "message": "<str>"}}
 *   Notification: {"method": "<event>", "params": {...}}   (no id)
 *
 * v0.1 milestone exposes only `health`. WalletConnect-specific methods
 * (pair, session_status, request_signature, ...) land in subsequent
 * commits as the @walletconnect/sign-client integration is built out.
 */

import * as readline from "node:readline";

interface RpcRequest {
  id?: string;
  method: string;
  params?: unknown;
}

interface RpcResponse {
  id?: string;
  result?: unknown;
  error?: { code: string; message: string; data?: unknown };
}

const VERSION = "0.1.0";
const startTime = Date.now();

function send(record: RpcResponse): void {
  process.stdout.write(JSON.stringify(record) + "\n");
}

async function dispatch(method: string, _params: unknown): Promise<unknown> {
  switch (method) {
    case "health":
      return {
        version: VERSION,
        node_version: process.versions.node,
        uptime_s: Math.floor((Date.now() - startTime) / 1000),
      };
    default:
      throw new RpcError("method_not_implemented", `method ${method} not implemented`);
  }
}

class RpcError extends Error {
  constructor(public code: string, message: string, public data?: unknown) {
    super(message);
  }
}

async function handleLine(raw: string): Promise<void> {
  let req: RpcRequest;
  try {
    req = JSON.parse(raw) as RpcRequest;
  } catch (err) {
    // Can't even parse the request — emit a generic error with no id
    send({ error: { code: "parse_error", message: String(err) } });
    return;
  }

  if (!req.method || typeof req.method !== "string") {
    send({
      id: req.id,
      error: { code: "bad_request", message: "missing 'method'" },
    });
    return;
  }

  try {
    const result = await dispatch(req.method, req.params ?? {});
    send({ id: req.id, result });
  } catch (err) {
    if (err instanceof RpcError) {
      send({ id: req.id, error: { code: err.code, message: err.message, data: err.data } });
    } else {
      send({
        id: req.id,
        error: { code: "internal_error", message: String(err) },
      });
    }
  }
}

function main(): void {
  const rl = readline.createInterface({
    input: process.stdin,
    crlfDelay: Infinity,
  });

  rl.on("line", (line) => {
    const trimmed = line.trim();
    if (trimmed) {
      void handleLine(trimmed);
    }
  });

  rl.on("close", () => {
    process.exit(0);
  });

  // Surface uncaught errors via stderr so the Python parent's bridge
  // log captures them instead of dying silently.
  process.on("uncaughtException", (err) => {
    process.stderr.write(`[clawmes-wc-bridge] uncaughtException: ${err}\n`);
    process.exit(1);
  });
}

main();
