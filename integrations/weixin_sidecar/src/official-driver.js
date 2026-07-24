import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import QRCode from "qrcode";

import {
  apiGetFetch,
  apiPostFetch,
  getConfig,
  getUpdates,
  notifyStart,
  notifyStop,
  sendMessage,
  sendTyping,
} from "../vendor/openclaw-weixin-2.4.6/api/api.js";

const FIXED_BASE_URL = "https://ilinkai.weixin.qq.com";
const QR_POLL_TIMEOUT_MS = 35_000;
const ACK_TIMEOUT_MS = 120_000;
const STALE_TOKEN_CODE = -14;

export const officialDriver = {
  available: true,

  async startBinding({ attemptId, emit }) {
    const controller = new AbortController();
    const raw = await apiPostFetch({
      baseUrl: FIXED_BASE_URL,
      endpoint: "ilink/bot/get_bot_qrcode?bot_type=3",
      body: JSON.stringify({ local_token_list: [] }),
      timeoutMs: 15_000,
      label: "bindingStart",
      abortSignal: controller.signal,
    });
    const qr = JSON.parse(raw);
    if (!qr.qrcode || !qr.qrcode_img_content) throw transportError("BINDING_QR_INVALID");
    const qrData = await QRCode.toDataURL(qr.qrcode_img_content, {
      errorCorrectionLevel: "M",
      margin: 2,
      width: 320,
    });
    const state = {
      attemptId,
      qrcode: qr.qrcode,
      qrData,
      controller,
      currentBaseUrl: FIXED_BASE_URL,
      cancelled: false,
    };
    state.task = pollBinding(state, emit);
    return {
      qrData: state.qrData,
      async cancel() {
        state.cancelled = true;
        controller.abort();
        void state.task.catch(() => {});
      },
    };
  },

  async startAccount({ accountKey, credential, emit }) {
    validateCredential(credential);
    const account = new OfficialAccount({ accountKey, credential, emit });
    await account.start();
    return account;
  },
};

async function pollBinding(state, emit) {
  while (!state.controller.signal.aborted) {
    try {
      let endpoint = `ilink/bot/get_qrcode_status?qrcode=${encodeURIComponent(state.qrcode)}`;
      const raw = await apiGetFetch({
        baseUrl: state.currentBaseUrl,
        endpoint,
        timeoutMs: QR_POLL_TIMEOUT_MS,
        label: "bindingStatus",
        abortSignal: state.controller.signal,
      });
      if (state.cancelled || state.controller.signal.aborted) return;
      const result = JSON.parse(raw);
      if (result.status === "wait") continue;
      if (result.status === "scaned") {
        emit({ type: "binding.status", attempt_id: state.attemptId, status: "scanned_pending_confirm" });
        continue;
      }
      if (result.status === "scaned_but_redirect" && result.redirect_host) {
        if (!/^[A-Za-z0-9.-]+$/.test(result.redirect_host)) throw transportError("BINDING_REDIRECT_INVALID");
        state.currentBaseUrl = `https://${result.redirect_host}`;
        continue;
      }
      if (result.status === "need_verifycode" || result.status === "verify_code_blocked") {
        emit({
          type: "binding.failed",
          attempt_id: state.attemptId,
          status: "verification_failed",
          code: "WEIXIN_VERIFY_CODE_REQUIRED",
        });
        return;
      }
      if (result.status === "expired") {
        emit({ type: "binding.failed", attempt_id: state.attemptId, status: "expired", code: "WEIXIN_QR_EXPIRED" });
        return;
      }
      if (result.status === "binded_redirect") {
        emit({ type: "binding.failed", attempt_id: state.attemptId, status: "already_bound", code: "WEIXIN_ALREADY_CONNECTED" });
        return;
      }
      if (result.status === "confirmed") {
        if (!result.bot_token || !result.ilink_bot_id || !result.ilink_user_id) {
          throw transportError("BINDING_CREDENTIAL_INVALID");
        }
        emit({
          type: "binding.connected",
          attempt_id: state.attemptId,
          external_account_id: result.ilink_bot_id,
          external_user_id: result.ilink_user_id,
          credential: {
            bot_token: result.bot_token,
            base_url: result.baseurl || state.currentBaseUrl,
            external_account_id: result.ilink_bot_id,
            external_user_id: result.ilink_user_id,
          },
        });
        return;
      }
    } catch (error) {
      if (state.controller.signal.aborted) return;
      emit({
        type: "binding.failed",
        attempt_id: state.attemptId,
        status: "upstream_unavailable",
        code: safeCode(error, "WEIXIN_BINDING_UPSTREAM_FAILED"),
      });
      return;
    }
  }
}

class OfficialAccount {
  constructor({ accountKey, credential, emit }) {
    this.accountKey = accountKey;
    this.credential = credential;
    this.emit = emit;
    this.controller = new AbortController();
    this.pendingAcks = new Map();
    this.contexts = new Map();
    this.typingTickets = new Map();
    this.stateDir = resolveAccountStateDir(accountKey);
    this.cursorPath = path.join(this.stateDir, "sync.json");
    this.contextPath = path.join(this.stateDir, "context.json");
    this.cursor = readJson(this.cursorPath)?.get_updates_buf || "";
    const persisted = readJson(this.contextPath) || {};
    for (const [reference, value] of Object.entries(persisted)) {
      if (value && typeof value.peer === "string" && typeof value.token === "string") {
        this.contexts.set(reference, value);
      }
    }
  }

  async start() {
    fs.mkdirSync(this.stateDir, { recursive: true });
    try {
      await notifyStart(this.apiOptions(10_000));
    } catch {
      // Upstream start notification is advisory; long-poll remains authoritative.
    }
    this.task = this.pollLoop();
  }

  async stop() {
    this.controller.abort();
    await this.task?.catch(() => {});
    try {
      await notifyStop(this.apiOptions(10_000));
    } catch {
      // Shutdown remains bounded even when the upstream notification times out.
    }
  }

  async ack(frame) {
    const pending = this.pendingAcks.get(String(frame.event_id || ""));
    if (pending) pending(String(frame.disposition || "rejected"));
  }

  async send(frame) {
    const context = this.contextFor(frame);
    await sendMessage({
      ...this.apiOptions(15_000),
      body: {
        msg: {
          from_user_id: "",
          to_user_id: context.peer,
          client_id: `zhice-weixin-${crypto.randomUUID()}`,
          message_type: 2,
          message_state: 2,
          item_list: [{ type: 1, text_item: { text: String(frame.text || "") } }],
          context_token: context.token,
        },
      },
    });
  }

  async typing(frame) {
    const context = this.contextFor(frame);
    let ticket = this.typingTickets.get(context.peer);
    if (!ticket) {
      const config = await getConfig({
        ...this.apiOptions(10_000),
        ilinkUserId: context.peer,
        contextToken: context.token,
      });
      ticket = config.typing_ticket;
      if (ticket) this.typingTickets.set(context.peer, ticket);
    }
    if (!ticket) return;
    await sendTyping({
      ...this.apiOptions(10_000),
      body: {
        ilink_user_id: context.peer,
        typing_ticket: ticket,
        status: frame.active ? 1 : 2,
      },
    });
  }

  contextFor(frame) {
    const reference = String(frame.context_token_ref || "");
    const context = this.contexts.get(reference);
    if (!context || context.peer !== String(frame.peer || "")) {
      throw transportError("CONTEXT_TOKEN_REFERENCE_INVALID");
    }
    return context;
  }

  async pollLoop() {
    let failures = 0;
    while (!this.controller.signal.aborted) {
      try {
        const response = await getUpdates({
          ...this.apiOptions(35_000),
          get_updates_buf: this.cursor,
          abortSignal: this.controller.signal,
        });
        if (this.controller.signal.aborted) return;
        const code = response.errcode ?? response.ret ?? 0;
        if (code === STALE_TOKEN_CODE) {
          this.emit({ type: "account.status", account_key: this.accountKey, status: "reconnect_required", code: "WEIXIN_TOKEN_STALE" });
          return;
        }
        if (code !== 0) throw transportError("WEIXIN_GET_UPDATES_FAILED");
        failures = 0;
        const messages = (response.msgs || []).filter((message) => this.isAllowedText(message));
        const dispositions = await Promise.all(messages.map((message) => this.deliver(message)));
        if (dispositions.every((value) => ["accepted", "duplicate", "rejected"].includes(value))) {
          if (typeof response.get_updates_buf === "string" && response.get_updates_buf) {
            this.cursor = response.get_updates_buf;
            atomicWrite(this.cursorPath, { get_updates_buf: this.cursor });
          }
        }
      } catch (error) {
        if (this.controller.signal.aborted) return;
        failures += 1;
        const delay = Math.min(30_000, 1000 * 2 ** Math.min(failures, 5));
        this.emit({ type: "account.status", account_key: this.accountKey, status: "degraded", code: safeCode(error, "WEIXIN_POLL_FAILED") });
        await sleep(delay, this.controller.signal);
      }
    }
  }

  isAllowedText(message) {
    return message
      && message.from_user_id === this.credential.external_user_id
      && Array.isArray(message.item_list)
      && message.item_list.some((item) => item?.type === 1 && item.text_item?.text);
  }

  async deliver(message) {
    const peer = String(message.from_user_id || "");
    const token = String(message.context_token || "");
    const reference = this.storeContext(peer, token);
    const eventId = stableEventId(message);
    const text = message.item_list
      .filter((item) => item?.type === 1 && item.text_item?.text)
      .map((item) => String(item.text_item.text))
      .join("\n");
    const disposition = new Promise((resolve) => {
      const timer = setTimeout(() => {
        this.pendingAcks.delete(eventId);
        resolve("rejected");
      }, ACK_TIMEOUT_MS);
      this.pendingAcks.set(eventId, (value) => {
        clearTimeout(timer);
        this.pendingAcks.delete(eventId);
        resolve(value);
      });
    });
    this.emit({
      type: "message.received",
      account_key: this.accountKey,
      event_id: eventId,
      message_id: String(message.message_id ?? eventId),
      conversation_type: "c2c",
      external_user_id: peer,
      text,
      occurred_at: new Date(Number(message.create_time_ms || Date.now())).toISOString(),
      context_token_ref: reference,
    });
    return disposition;
  }

  storeContext(peer, token) {
    if (!token) throw transportError("CONTEXT_TOKEN_MISSING");
    const reference = `ctx_${crypto.createHash("sha256").update(`${this.accountKey}\0${peer}\0${token}`).digest("hex").slice(0, 32)}`;
    this.contexts.set(reference, { peer, token });
    atomicWrite(this.contextPath, Object.fromEntries(this.contexts));
    return reference;
  }

  apiOptions(timeoutMs) {
    return {
      baseUrl: String(this.credential.base_url || FIXED_BASE_URL),
      token: String(this.credential.bot_token || ""),
      timeoutMs,
    };
  }
}

function validateCredential(value) {
  if (!value || !value.bot_token || !value.external_account_id || !value.external_user_id) {
    throw transportError("ACCOUNT_CREDENTIAL_INVALID");
  }
}

function resolveAccountStateDir(accountKey) {
  const root = process.env.ZHICE_WEIXIN_STATE_DIR;
  if (!root) throw transportError("WEIXIN_STATE_DIR_MISSING");
  if (!/^[A-Za-z0-9_-]{1,80}$/.test(accountKey)) throw transportError("ACCOUNT_KEY_INVALID");
  return path.join(root, accountKey);
}

function stableEventId(message) {
  if (message.message_id !== undefined && message.message_id !== null) return String(message.message_id);
  return crypto.createHash("sha256").update(JSON.stringify(message)).digest("hex");
}

function readJson(file) {
  try { return JSON.parse(fs.readFileSync(file, "utf8")); } catch { return null; }
}

function atomicWrite(file, value) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const temp = `${file}.${process.pid}.${crypto.randomUUID()}.tmp`;
  fs.writeFileSync(temp, JSON.stringify(value), { encoding: "utf8", mode: 0o600 });
  fs.renameSync(temp, file);
}

function sleep(milliseconds, signal) {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, milliseconds);
    signal?.addEventListener("abort", () => { clearTimeout(timer); resolve(); }, { once: true });
  });
}

function transportError(code) {
  return Object.assign(new Error(code), { code });
}

function safeCode(error, fallback) {
  const code = String(error?.code || fallback);
  return /^[A-Z0-9_]{1,64}$/.test(code) ? code : fallback;
}
