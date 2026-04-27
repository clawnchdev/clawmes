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
 * Methods (PRD §21.2):
 *   health, pair, session_status, disconnect, request_signature, switch_chain
 */

import * as readline from "node:readline";
import {
  MethodError,
  disconnect,
  pair,
  request_signature,
  session_status,
  switch_chain,
} from "./methods.js";

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


type Handler = (params: unknown) => Promise<unknown>;

const HANDLERS: Record<string, Handler> = {
  health: async () => ({
    version: VERSION,
    node_version: process.versions.node,
    uptime_s: Math.floor((Date.now() - startTime) / 1000),
  }),
  pair: pair as Handler,
  session_status: session_status as Handler,
  disconnect: disconnect as Handler,
  request_signature: request_signature as Handler,
  switch_chain: switch_chain as Handler,
};


async function dispatch(method: string, params: unknown): Promise<unknown> {
  const handler = HANDLERS[method];
  if (!handler) {
    throw new MethodError(
      "method_not_implemented",
      `method ${method} not implemented`,
    );
  }
  return handler(params);
}


async function handleLine(raw: string): Promise<void> {
  let req: RpcRequest;
  try {
    req = JSON.parse(raw) as RpcRequest;
  } catch (err) {
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
    if (err instanceof MethodError) {
      send({
        id: req.id,
        error: { code: err.code, message: err.message, data: err.data },
      });
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

  process.on("uncaughtException", (err) => {
    process.stderr.write(`[clawmes-wc-bridge] uncaughtException: ${err}\n`);
    process.exit(1);
  });
}


main();
