import { defineStore } from "pinia";

import { api } from "@/api/client";

export const useChannelStore = defineStore("channels", {
  state: () => ({
    bindings: [] as Array<{ binding_id: string; channel: string; display_name: string; linked_at: string }>,
    weixin: { status: "unknown", linked_at: "" },
    weixinAttempt: null as null | { attempt_id: string; status: string; expires_at: string; qr_data: string; error_code: string },
    qqCode: "",
    qqCommand: "",
    busy: false,
    error: "",
    pollTimer: 0,
  }),
  actions: {
    async refresh() {
      this.busy = true;
      this.error = "";
      const [bindings, weixin] = await Promise.allSettled([api.bindings(), api.weixinStatus()]);
      if (bindings.status === "fulfilled") this.bindings = bindings.value.bindings;
      else this.error = bindings.reason instanceof Error ? bindings.reason.message : "渠道绑定读取失败";
      if (weixin.status === "fulfilled") this.weixin = weixin.value;
      else {
        this.weixin = { status: "unavailable", linked_at: "" };
        const message = weixin.reason instanceof Error ? weixin.reason.message : "微信状态读取失败";
        this.error = [this.error, message].filter(Boolean).join("；");
      }
      this.busy = false;
    },
    async generateQqCode() {
      const result = await api.qqCode();
      this.qqCode = result.code;
      this.qqCommand = result.command;
    },
    async authorizeQq(token: string) { await api.qqAuthorize(token); await this.refresh(); },
    async unlink(id: string) { await api.unlinkBinding(id); await this.refresh(); },
    async startWeixin() {
      this.weixinAttempt = await api.startWeixin();
      this.schedulePoll();
    },
    schedulePoll() {
      window.clearTimeout(this.pollTimer);
      if (!this.weixinAttempt || !["pending", "scanning"].includes(this.weixinAttempt.status)) return;
      this.pollTimer = window.setTimeout(async () => {
        if (!this.weixinAttempt) return;
        this.weixinAttempt = await api.pollWeixin(this.weixinAttempt.attempt_id);
        if (this.weixinAttempt.status === "bound") { this.weixinAttempt = null; await this.refresh(); }
        else this.schedulePoll();
      }, 1500);
    },
    async cancelWeixin() {
      if (!this.weixinAttempt) return;
      await api.cancelWeixin(this.weixinAttempt.attempt_id);
      this.weixinAttempt = null;
      window.clearTimeout(this.pollTimer);
    },
    async unlinkWeixin() { await api.unlinkWeixin(); await this.refresh(); },
    async reconnectWeixin() { await api.reconnectWeixin(); await this.refresh(); },
  },
});
