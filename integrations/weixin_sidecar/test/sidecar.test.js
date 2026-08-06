import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import test from "node:test";
import { PassThrough } from "node:stream";
import { fileURLToPath, pathToFileURL } from "node:url";

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

test("direct process entry serves hello, health, and QR binding on Linux-compatible URLs", async () => {
  const entry = fileURLToPath(new URL("../src/main.js", import.meta.url));
  const fixture = pathToFileURL(
    fileURLToPath(new URL("./process-fetch-fixture.js", import.meta.url)),
  ).href;
  const child = spawn(process.execPath, [entry], {
    env: { ...process.env, NODE_OPTIONS: `--import=${fixture}` },
    stdio: ["pipe", "pipe", "pipe"],
  });
  const frames = [];
  let stdoutBuffer = "";
  let stderr = "";
  child.stdout.setEncoding("utf8");
  child.stderr.setEncoding("utf8");
  child.stdout.on("data", (chunk) => {
    stdoutBuffer += chunk;
    const lines = stdoutBuffer.split("\n");
    stdoutBuffer = lines.pop() || "";
    for (const line of lines) if (line) frames.push(JSON.parse(line));
  });
  child.stderr.on("data", (chunk) => { stderr += chunk; });
  const exited = new Promise((resolve) => child.once("exit", (code) => resolve(code)));
  const send = (frame) => child.stdin.write(
    `${JSON.stringify({ protocol_version: PROTOCOL_VERSION, ...frame })}\n`,
  );

  try {
    send({ type: "hello", request_id: "process-hello" });
    send({ type: "health.get", request_id: "process-health" });
    send({ type: "binding.start", request_id: "process-binding", attempt_id: "process-attempt" });
    await waitForFrames(frames, ["hello.ok", "health.status", "binding.qr", "binding.connected"]);

    assert.equal(frames.find((frame) => frame.type === "health.status")?.status, "available");
    assert.match(frames.find((frame) => frame.type === "binding.qr")?.qr_data || "", /^data:image\/png;base64,/);
    send({ type: "shutdown", request_id: "process-shutdown" });
    await waitForFrames(frames, ["shutdown.ok"]);
    assert.equal(await exited, 0, stderr);
  } finally {
    if (child.exitCode === null) child.kill();
  }
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

async function waitForFrames(frames, requiredTypes, timeoutMs = 3000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const types = new Set(frames.map((frame) => frame.type));
    if (requiredTypes.every((type) => types.has(type))) return;
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  assert.fail(`timed out waiting for frames: ${requiredTypes.join(", ")}`);
}
