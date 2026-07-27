import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  officialDriver,
  safePollCode,
  shouldDegradePoll,
} from "../src/official-driver.js";

test("official driver classifies poll failures without exposing raw details", () => {
  assert.equal(
    safePollCode(Object.assign(new TypeError("fetch failed secret"), {
      cause: { code: "ENOTFOUND" },
    })),
    "WEIXIN_POLL_DNS_FAILED",
  );
  assert.equal(safePollCode(new SyntaxError("private upstream body")), "WEIXIN_POLL_INVALID_RESPONSE");
  assert.equal(
    safePollCode(Object.assign(new Error("business failure"), { code: "WEIXIN_GET_UPDATES_FAILED" })),
    "WEIXIN_GET_UPDATES_FAILED",
  );
  assert.equal(shouldDegradePoll(1), false);
  assert.equal(shouldDegradePoll(2), false);
  assert.equal(shouldDegradePoll(3), true);
  assert.equal(shouldDegradePoll(4), false);
});

test("official driver keeps one transient poll failure out of account status", async () => {
  const originalFetch = globalThis.fetch;
  const originalState = process.env.ZHICE_WEIXIN_STATE_DIR;
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "zhice-weixin-poll-retry-"));
  process.env.ZHICE_WEIXIN_STATE_DIR = stateRoot;
  const events = [];
  let polls = 0;
  globalThis.fetch = async (url, options = {}) => {
    const text = String(url);
    if (text.includes("notifystart") || text.includes("notifystop")) return jsonResponse({ ret: 0 });
    if (text.includes("getupdates")) {
      polls += 1;
      if (polls === 1) {
        throw Object.assign(new TypeError("fetch failed"), { cause: { code: "ECONNRESET" } });
      }
      if (polls === 2) return jsonResponse({ ret: 0, msgs: [] });
      return abortableResponse(options.signal);
    }
    throw new Error("unexpected fetch");
  };
  let account;
  try {
    account = await officialDriver.startAccount({
      accountKey: "opaque-poll-retry",
      credential: accountCredential(),
      emit: (frame) => events.push(frame),
    });
    await waitFor(() => polls >= 2, 3500);
    const retry = events.find((frame) => frame.type === "account.poll_retry");
    assert.equal(retry.code, "WEIXIN_POLL_CONNECTION_RESET");
    assert.equal(retry.consecutive_failures, 1);
    assert.equal(events.some((frame) => frame.type === "account.status"), false);
    assert.equal(account.status, "active");
  } finally {
    await account?.stop();
    globalThis.fetch = originalFetch;
    if (originalState === undefined) delete process.env.ZHICE_WEIXIN_STATE_DIR;
    else process.env.ZHICE_WEIXIN_STATE_DIR = originalState;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("official driver completes QR binding with allowlisted credential fields", async () => {
  const originalFetch = globalThis.fetch;
  const events = [];
  globalThis.fetch = async (url) => {
    if (String(url).includes("get_bot_qrcode")) {
      return jsonResponse({ qrcode: "qr-secret", qrcode_img_content: "https://example.invalid/qr" });
    }
    if (String(url).includes("get_qrcode_status")) {
      return jsonResponse({
        status: "confirmed",
        bot_token: "bot-token-secret",
        ilink_bot_id: "bot@im.bot",
        ilink_user_id: "user@im.wechat",
        baseurl: "https://ilinkai.weixin.qq.com",
      });
    }
    throw new Error("unexpected fetch");
  };
  try {
    const binding = await officialDriver.startBinding({
      attemptId: "attempt-1",
      emit: (frame) => events.push(frame),
    });
    assert.match(binding.qrData, /^data:image\/png;base64,/);
    assert.equal(binding.qrData.includes("https://example.invalid/qr"), false);
    await waitFor(() => events.some((frame) => frame.type === "binding.connected"));
    const connected = events.find((frame) => frame.type === "binding.connected");
    assert.deepEqual(Object.keys(connected.credential).sort(), [
      "base_url",
      "bot_token",
      "external_account_id",
      "external_user_id",
    ]);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("official driver cancels an in-flight QR poll promptly", async () => {
  const originalFetch = globalThis.fetch;
  let pollStarted = false;
  globalThis.fetch = async (url, options = {}) => {
    if (String(url).includes("get_bot_qrcode")) {
      return jsonResponse({ qrcode: "qr-secret", qrcode_img_content: "https://example.invalid/qr" });
    }
    if (String(url).includes("get_qrcode_status")) {
      pollStarted = true;
      return abortableResponse(options.signal);
    }
    throw new Error("unexpected fetch");
  };
  try {
    const binding = await officialDriver.startBinding({ attemptId: "attempt-cancel", emit: () => {} });
    await waitFor(() => pollStarted);
    const startedAt = Date.now();
    await binding.cancel();
    assert.ok(Date.now() - startedAt < 250, "cancel should acknowledge without awaiting the long poll");
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("official driver suppresses a late confirmed event after cancellation", async () => {
  const originalFetch = globalThis.fetch;
  const events = [];
  let releasePoll;
  globalThis.fetch = async (url) => {
    if (String(url).includes("get_bot_qrcode")) {
      return jsonResponse({ qrcode: "qr-secret", qrcode_img_content: "https://example.invalid/qr" });
    }
    if (String(url).includes("get_qrcode_status")) {
      return new Promise((resolve) => {
        releasePoll = () => resolve(jsonResponse({
          status: "confirmed",
          bot_token: "late-token",
          ilink_bot_id: "late-bot",
          ilink_user_id: "late-user",
        }));
      });
    }
    throw new Error("unexpected fetch");
  };
  try {
    const binding = await officialDriver.startBinding({
      attemptId: "attempt-late",
      emit: (frame) => events.push(frame),
    });
    await waitFor(() => typeof releasePoll === "function");
    await binding.cancel();
    releasePoll();
    await new Promise((resolve) => setTimeout(resolve, 25));
    assert.deepEqual(events, []);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("official driver ACKs before cursor commit and sends with saved context", async () => {
  const originalFetch = globalThis.fetch;
  const originalState = process.env.ZHICE_WEIXIN_STATE_DIR;
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "zhice-weixin-driver-"));
  process.env.ZHICE_WEIXIN_STATE_DIR = stateRoot;
  const events = [];
  const sentBodies = [];
  let delivered = false;
  globalThis.fetch = async (url, options = {}) => {
    const text = String(url);
    if (text.includes("notifystart") || text.includes("notifystop")) return jsonResponse({ ret: 0 });
    if (text.includes("getupdates")) {
      if (!delivered) {
        delivered = true;
        return jsonResponse({
          ret: 0,
          get_updates_buf: "cursor-after-ack",
          msgs: [{
            message_id: 42,
            from_user_id: "user@im.wechat",
            create_time_ms: Date.now(),
            context_token: "context-secret",
            item_list: [{ type: 1, text_item: { text: "hello" } }],
          }],
        });
      }
      return abortableResponse(options.signal);
    }
    if (text.includes("sendmessage")) {
      sentBodies.push(JSON.parse(options.body));
      return jsonResponse({ ret: 0 });
    }
    throw new Error("unexpected fetch");
  };
  let account;
  try {
    account = await officialDriver.startAccount({
      accountKey: "opaque-a",
      credential: {
        bot_token: "bot-token-secret",
        base_url: "https://ilinkai.weixin.qq.com",
        external_account_id: "bot@im.bot",
        external_user_id: "user@im.wechat",
      },
      emit: (frame) => events.push(frame),
    });
    await waitFor(() => events.some((frame) => frame.type === "message.received"));
    const inbound = events.find((frame) => frame.type === "message.received");
    const cursorPath = path.join(stateRoot, "opaque-a", "sync.json");
    assert.equal(fs.existsSync(cursorPath), false);
    await account.ack({ event_id: inbound.event_id, disposition: "accepted" });
    await waitFor(() => fs.existsSync(cursorPath));
    assert.equal(JSON.parse(fs.readFileSync(cursorPath, "utf8")).get_updates_buf, "cursor-after-ack");
    await account.send({
      peer: "user@im.wechat",
      context_token_ref: inbound.context_token_ref,
      client_id: "zhice-weixin-0123456789abcdef0123456789abcdef",
      text: "reply",
    });
    assert.equal(sentBodies[0].msg.context_token, "context-secret");
    assert.equal(sentBodies[0].msg.to_user_id, "user@im.wechat");
    assert.equal(sentBodies[0].msg.client_id, "zhice-weixin-0123456789abcdef0123456789abcdef");
  } finally {
    await account?.stop();
    globalThis.fetch = originalFetch;
    if (originalState === undefined) delete process.env.ZHICE_WEIXIN_STATE_DIR;
    else process.env.ZHICE_WEIXIN_STATE_DIR = originalState;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("official driver rejects an explicitly stale token during account start", async () => {
  const originalFetch = globalThis.fetch;
  const originalState = process.env.ZHICE_WEIXIN_STATE_DIR;
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "zhice-weixin-stale-"));
  process.env.ZHICE_WEIXIN_STATE_DIR = stateRoot;
  globalThis.fetch = async (url) => {
    if (String(url).includes("notifystart")) return jsonResponse({ ret: -14 });
    throw new Error("unexpected fetch");
  };
  try {
    await assert.rejects(
      officialDriver.startAccount({
        accountKey: "opaque-stale",
        credential: accountCredential(),
        emit: () => {},
      }),
      (error) => error?.code === "WEIXIN_TOKEN_STALE",
    );
  } finally {
    globalThis.fetch = originalFetch;
    if (originalState === undefined) delete process.env.ZHICE_WEIXIN_STATE_DIR;
    else process.env.ZHICE_WEIXIN_STATE_DIR = originalState;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("official driver keeps a transient start failure degraded until polling recovers", async () => {
  const originalFetch = globalThis.fetch;
  const originalState = process.env.ZHICE_WEIXIN_STATE_DIR;
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "zhice-weixin-recover-"));
  process.env.ZHICE_WEIXIN_STATE_DIR = stateRoot;
  const events = [];
  let releasePoll;
  let pollCount = 0;
  globalThis.fetch = async (url, options = {}) => {
    const text = String(url);
    if (text.includes("notifystart")) throw new Error("temporary network failure");
    if (text.includes("getupdates")) {
      pollCount += 1;
      if (pollCount > 1) return abortableResponse(options.signal);
      return new Promise((resolve) => {
        releasePoll = () => resolve(jsonResponse({ ret: 0, msgs: [] }));
      });
    }
    if (text.includes("notifystop")) return jsonResponse({ ret: 0 });
    throw new Error("unexpected fetch");
  };
  let account;
  try {
    account = await officialDriver.startAccount({
      accountKey: "opaque-recover",
      credential: accountCredential(),
      emit: (frame) => events.push(frame),
    });
    assert.equal(account.status, "degraded");
    await waitFor(() => typeof releasePoll === "function");
    releasePoll();
    await waitFor(() => events.some((frame) => frame.status === "active"));
    assert.equal(account.status, "active");
  } finally {
    await account?.stop();
    globalThis.fetch = originalFetch;
    if (originalState === undefined) delete process.env.ZHICE_WEIXIN_STATE_DIR;
    else process.env.ZHICE_WEIXIN_STATE_DIR = originalState;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

test("official driver stop releases an inbound message waiting for ACK", async () => {
  const originalFetch = globalThis.fetch;
  const originalState = process.env.ZHICE_WEIXIN_STATE_DIR;
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), "zhice-weixin-stop-"));
  process.env.ZHICE_WEIXIN_STATE_DIR = stateRoot;
  const events = [];
  let delivered = false;
  globalThis.fetch = async (url, options = {}) => {
    const text = String(url);
    if (text.includes("notifystart") || text.includes("notifystop")) {
      return jsonResponse({ ret: 0 });
    }
    if (text.includes("getupdates")) {
      if (!delivered) {
        delivered = true;
        return jsonResponse({
          ret: 0,
          msgs: [{
            message_id: 99,
            from_user_id: "user@im.wechat",
            context_token: "context-secret",
            item_list: [{ type: 1, text_item: { text: "hello" } }],
          }],
        });
      }
      return abortableResponse(options.signal);
    }
    throw new Error("unexpected fetch");
  };
  let account;
  try {
    account = await officialDriver.startAccount({
      accountKey: "opaque-stop",
      credential: accountCredential(),
      emit: (frame) => events.push(frame),
    });
    await waitFor(() => events.some((frame) => frame.type === "message.received"));
    const startedAt = Date.now();
    await account.stop();
    assert.ok(Date.now() - startedAt < 250, "stop should release pending ACK waits");
    account = null;
  } finally {
    await account?.stop();
    globalThis.fetch = originalFetch;
    if (originalState === undefined) delete process.env.ZHICE_WEIXIN_STATE_DIR;
    else process.env.ZHICE_WEIXIN_STATE_DIR = originalState;
    fs.rmSync(stateRoot, { recursive: true, force: true });
  }
});

function accountCredential() {
  return {
    bot_token: "bot-token-secret",
    base_url: "https://ilinkai.weixin.qq.com",
    external_account_id: "bot@im.bot",
    external_user_id: "user@im.wechat",
  };
}

function jsonResponse(value) {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

function abortableResponse(signal) {
  return new Promise((resolve, reject) => {
    const abort = () => reject(new DOMException("aborted", "AbortError"));
    if (signal?.aborted) abort();
    else signal?.addEventListener("abort", abort, { once: true });
  });
}

async function waitFor(predicate, timeoutMs = 2000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (predicate()) return;
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  assert.fail("condition was not met before timeout");
}
