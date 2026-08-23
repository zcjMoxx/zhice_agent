import { defineStore } from "pinia";

import { ApiError, api } from "@/api/client";
import type { ChannelBinding, WeixinBindingAttempt, WeixinChannelStatus } from "@/api/types";

const WEIXIN_TERMINAL_STATUSES = new Set([
  "connected",
  "expired",
  "cancelled",
  "account_conflict",
  "already_bound",
  "verification_failed",
  "upstream_unavailable",
  "persist_failed",
]);

export function isWeixinAttemptTerminal(attempt: WeixinBindingAttempt): boolean {
  return WEIXIN_TERMINAL_STATUSES.has(attempt.status) || Boolean(attempt.error_code);
}

function requestFailure(error: unknown, fallback: string): { code: string; message: string } {
  if (error instanceof ApiError) return { code: error.code, message: error.message };
  if (error instanceof Error) return { code: "REQUEST_FAILED", message: error.message };
  return { code: "REQUEST_FAILED", message: fallback };
}

export const useChannelStore = defineStore("channels", {
  state: () => ({
    bindings: [] as ChannelBinding[],
    weixin: { status: "unknown", linked_at: "" } as WeixinChannelStatus,
    weixinAttempt: null as WeixinBindingAttempt | null,
    weixinError: null as { code: string; message: string } | null,
    qqCode: "",
    qqCommand: "",
    pendingQqToken: "",
    qqAuthorizationError: "",
    busy: false,
    weixinBusy: false,
    error: "",
    pollTimer: 0,
  }),
  actions: {
    async refresh() {
      this.busy = true;
      this.error = "";
      try {
        const [bindings, weixin] = await Promise.allSettled([api.bindings(), api.weixinStatus()]);
        if (bindings.status === "fulfilled") this.bindings = bindings.value.bindings;
        else this.error = bindings.reason instanceof Error ? bindings.reason.message : "渠道绑定读取失败";
        if (weixin.status === "fulfilled") this.weixin = weixin.value;
        else {
          this.weixin = { status: "unavailable", linked_at: "" };
          if (!(weixin.reason instanceof ApiError && weixin.reason.code === "CHANNEL_WEIXIN_UNAVAILABLE")) {
            const message = weixin.reason instanceof Error ? weixin.reason.message : "微信状态读取失败";
            this.error = [this.error, message].filter(Boolean).join("；");
          }
        }
      } finally {
        this.busy = false;
      }
    },
    async generateQqCode() {
      this.busy = true;
      this.error = "";
      try {
        const result = await api.qqCode();
        this.qqCode = result.code;
        this.qqCommand = result.command;
      } catch (error) {
        this.error = requestFailure(error, "QQ 绑定码生成失败").message;
      } finally {
        this.busy = false;
      }
    },
    async authorizeQq(token?: string) {
      const authorizationToken = token ?? this.pendingQqToken;
      this.busy = true;
      this.error = "";
      this.qqAuthorizationError = "";
      try {
        await api.qqAuthorize(authorizationToken);
        this.pendingQqToken = "";
        await this.refresh();
      } catch (error) {
        this.qqAuthorizationError = requestFailure(error, "QQ 绑定失败").message;
        throw error;
      } finally {
        this.busy = false;
      }
    },
    async unlink(id: string) {
      this.busy = true;
      this.error = "";
      try { await api.unlinkBinding(id); await this.refresh(); }
      catch (error) { this.error = requestFailure(error, "QQ 解绑失败").message; }
      finally { this.busy = false; }
    },
    async startWeixin() {
      window.clearTimeout(this.pollTimer);
      this.weixinBusy = true;
      this.weixinError = null;
      try {
        this.weixinAttempt = await api.startWeixin();
        await this.afterWeixinAttempt();
      } catch (error) {
        this.weixinError = requestFailure(error, "微信扫码启动失败");
      } finally {
        this.weixinBusy = false;
      }
    },
    schedulePoll() {
      window.clearTimeout(this.pollTimer);
      if (!this.weixinAttempt || isWeixinAttemptTerminal(this.weixinAttempt)) return;
      this.pollTimer = window.setTimeout(() => { void this.pollWeixinNow(); }, 1500);
    },
    async pollWeixinNow() {
      const attemptId = this.weixinAttempt?.attempt_id;
      if (!attemptId) return;
      this.weixinBusy = true;
      this.weixinError = null;
      try {
        const attempt = await api.pollWeixin(attemptId);
        if (this.weixinAttempt?.attempt_id !== attemptId) return;
        this.weixinAttempt = attempt;
        await this.afterWeixinAttempt();
      } catch (error) {
        this.weixinError = requestFailure(error, "微信扫码状态读取失败");
      } finally {
        this.weixinBusy = false;
      }
    },
    async afterWeixinAttempt() {
      if (!this.weixinAttempt) return;
      if (this.weixinAttempt.status === "connected") {
        window.clearTimeout(this.pollTimer);
        await this.refresh();
        return;
      }
      if (!isWeixinAttemptTerminal(this.weixinAttempt)) this.schedulePoll();
      else window.clearTimeout(this.pollTimer);
    },
    async retryWeixin() {
      this.weixinError = null;
      if (this.weixinAttempt && !isWeixinAttemptTerminal(this.weixinAttempt)) {
        await this.pollWeixinNow();
        return;
      }
      this.weixinAttempt = null;
      await this.startWeixin();
    },
    async cancelWeixin() {
      const attemptId = this.weixinAttempt?.attempt_id;
      if (!attemptId) return;
      window.clearTimeout(this.pollTimer);
      this.weixinBusy = true;
      this.weixinError = null;
      try {
        this.weixinAttempt = await api.cancelWeixin(attemptId);
      } catch (error) {
        this.weixinError = requestFailure(error, "取消微信扫码失败");
      } finally {
        this.weixinBusy = false;
      }
    },
    async unlinkWeixin() {
      this.weixinBusy = true;
      this.weixinError = null;
      try { await api.unlinkWeixin(); await this.refresh(); }
      catch (error) { this.weixinError = requestFailure(error, "微信解绑失败"); }
      finally { this.weixinBusy = false; }
    },
    async reconnectWeixin() {
      this.weixinBusy = true;
      this.weixinError = null;
      try { await api.reconnectWeixin(); await this.refresh(); }
      catch (error) { this.weixinError = requestFailure(error, "微信重连失败"); }
      finally { this.weixinBusy = false; }
    },
  },
});
