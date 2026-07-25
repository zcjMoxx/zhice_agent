import assert from "node:assert/strict";
import test from "node:test";
import { PassThrough } from "node:stream";

import { blockedDriver, createSidecar, PROTOCOL_VERSION } from "../src/main.js";

function harness(driver = blockedDriver) {
  const input = new PassThrough();
  const output = new PassThrough();
  const frames = [];
  output.on("data", (chunk) => {
    for (const line of chunk.toString().trim().split("\n")) if (line) frames.push(JSON.parse(line));
  });
  createSidecar({ input, output, driver });
  const send = (frame) => input.write(`${JSON.stringify({ protocol_version: PROTOCOL_VERSION, ...frame })}\n`);
  return { frames, send };
}

test("handshake and health use stdout NDJSON", async () => {
  const h = harness();
  h.send({ type: "hello", request_id: "1" });
  h.send({ type: "health.get", request_id: "2" });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(h.frames[0].type, "hello.ok");
  assert.equal(h.frames[1].code, "WEIXIN_TRANSPORT_POC_REQUIRED");
});

test("binding stops at the explicit POC boundary", async () => {
  const h = harness();
  h.send({ type: "binding.start", request_id: "1", attempt_id: "attempt-safe" });
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(
    { type: h.frames[0].type, status: h.frames[0].status, code: h.frames[0].code },
    {
      type: "binding.failed",
      status: "upstream_unavailable",
      code: "WEIXIN_TRANSPORT_POC_REQUIRED",
    },
  );
});

test("injected driver keeps accounts isolated", async () => {
  const sent = [];
  const driver = {
    available: true,
    async startAccount({ accountKey }) {
      return { async send(frame) { sent.push([accountKey, frame.text]); } };
    },
  };
  const h = harness(driver);
  h.send({ type: "account.start", request_id: "1", account_key: "a", credential: {} });
  h.send({ type: "account.start", request_id: "2", account_key: "b", credential: {} });
  await new Promise((resolve) => setImmediate(resolve));
  h.send({ type: "message.send", request_id: "3", account_key: "b", text: "hello" });
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(sent, [["b", "hello"]]);
});

test("restarting one account stops the previous poller before replacement", async () => {
  const stopped = [];
  const sent = [];
  let nextId = 0;
  const driver = {
    available: true,
    async startAccount() {
      const id = ++nextId;
      return {
        async stop() { stopped.push(id); },
        async send() { sent.push(id); },
      };
    },
  };
  const h = harness(driver);
  h.send({ type: "account.start", request_id: "1", account_key: "a", credential: {} });
  h.send({ type: "account.start", request_id: "2", account_key: "a", credential: {} });
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));
  h.send({ type: "message.send", request_id: "3", account_key: "a", text: "hello" });
  await new Promise((resolve) => setImmediate(resolve));

  assert.deepEqual(stopped, [1]);
  assert.deepEqual(sent, [2]);
});
