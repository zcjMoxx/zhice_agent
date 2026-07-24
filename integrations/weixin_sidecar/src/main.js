import readline from "node:readline";

import { officialDriver } from "./official-driver.js";

export const PROTOCOL_VERSION = "1";
const MAX_FRAME_BYTES = 256 * 1024;

export function createSidecar({ input = process.stdin, output = process.stdout, driver }) {
  const accounts = new Map();
  const attempts = new Map();

  function emit(frame) {
    output.write(`${JSON.stringify({ protocol_version: PROTOCOL_VERSION, ...frame })}\n`);
  }

  async function handle(frame) {
    const requestId = String(frame.request_id || "");
    if (frame.protocol_version !== PROTOCOL_VERSION || !requestId) {
      emit({ type: "protocol.error", request_id: requestId, code: "PROTOCOL_INVALID" });
      return;
    }
    const reply = (value) => emit({ request_id: requestId, ...value });
    switch (frame.type) {
      case "hello":
        reply({ type: "hello.ok", sidecar_version: "0.1.0" });
        return;
      case "health.get":
        reply({
          type: "health.status",
          status: driver.available ? "available" : "degraded",
          code: driver.available ? "OK" : "WEIXIN_TRANSPORT_POC_REQUIRED",
        });
        return;
      case "binding.start": {
        const attemptId = String(frame.attempt_id || "");
        if (!driver.available) {
          reply({
            type: "binding.failed",
            attempt_id: attemptId,
            status: "upstream_unavailable",
            code: "WEIXIN_TRANSPORT_POC_REQUIRED",
          });
          return;
        }
        const attempt = await driver.startBinding({ attemptId, emit });
        attempts.set(attemptId, attempt);
        reply({ type: "binding.qr", attempt_id: attemptId, qr_data: attempt.qrData });
        return;
      }
      case "binding.cancel": {
        const attemptId = String(frame.attempt_id || "");
        await attempts.get(attemptId)?.cancel?.();
        attempts.delete(attemptId);
        reply({ type: "binding.status", attempt_id: attemptId, status: "cancelled" });
        return;
      }
      case "account.start": {
        const accountKey = String(frame.account_key || "");
        if (!driver.available) {
          reply({ type: "account.status", account_key: accountKey, status: "reconnect_required" });
          return;
        }
        const account = await driver.startAccount({
          accountKey,
          credential: frame.credential,
          emit,
        });
        accounts.set(accountKey, account);
        reply({ type: "account.status", account_key: accountKey, status: "active" });
        return;
      }
      case "account.stop": {
        const accountKey = String(frame.account_key || "");
        await accounts.get(accountKey)?.stop?.();
        accounts.delete(accountKey);
        reply({ type: "account.status", account_key: accountKey, status: "disabled" });
        return;
      }
      case "message.ack":
        await accounts.get(String(frame.account_key || ""))?.ack?.(frame);
        reply({ type: "message.ack_result", disposition: frame.disposition });
        return;
      case "message.send":
        await requireAccount(accounts, frame).send(frame);
        reply({ type: "message.send_result", status: "sent" });
        return;
      case "typing.set":
        await requireAccount(accounts, frame).typing?.(frame);
        reply({ type: "typing.result", status: "ok" });
        return;
      case "shutdown":
        for (const account of accounts.values()) await account.stop?.();
        accounts.clear();
        reply({ type: "shutdown.ok" });
        setImmediate(() => process.exit(0));
        return;
      default:
        reply({ type: "protocol.error", code: "PROTOCOL_TYPE_UNSUPPORTED" });
    }
  }

  const lines = readline.createInterface({ input, crlfDelay: Infinity });
  lines.on("line", (line) => {
    if (Buffer.byteLength(line, "utf8") > MAX_FRAME_BYTES) {
      emit({ type: "protocol.error", request_id: "", code: "PROTOCOL_FRAME_TOO_LARGE" });
      return;
    }
    let frame;
    try {
      frame = JSON.parse(line);
    } catch {
      emit({ type: "protocol.error", request_id: "", code: "PROTOCOL_JSON_INVALID" });
      return;
    }
    void handle(frame).catch((error) => {
      emit({
        type: "protocol.error",
        request_id: String(frame.request_id || ""),
        code: safeErrorCode(error),
      });
    });
  });
  return { handle, emit, accounts, attempts };
}

function requireAccount(accounts, frame) {
  const account = accounts.get(String(frame.account_key || ""));
  if (!account) throw Object.assign(new Error("account unavailable"), { code: "ACCOUNT_UNAVAILABLE" });
  return account;
}

function safeErrorCode(error) {
  const code = String(error?.code || "WEIXIN_TRANSPORT_ERROR");
  return /^[A-Z0-9_]{1,64}$/.test(code) ? code : "WEIXIN_TRANSPORT_ERROR";
}

export const blockedDriver = Object.freeze({ available: false });

if (process.argv[1] && import.meta.url === new URL(`file:///${process.argv[1].replaceAll("\\", "/")}`).href) {
  createSidecar({ driver: officialDriver });
}
