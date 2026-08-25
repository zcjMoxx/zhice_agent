<script setup lang="ts">
import { KeyRound, Link2, Mail, Monitor, Moon, Palette, Settings2, Sun, UserRound, X } from "@lucide/vue";
import { computed, onBeforeUnmount, onMounted, ref } from "vue";

import { ApiError, api } from "@/api/client";
import type { NotificationEmail, WorkflowCapabilities, WorkflowEmailConnection } from "@/api/types";
import { uiText, type UiLanguage } from "@/i18n";
import { useAuthStore } from "@/stores/auth";
import { isWeixinAttemptTerminal, useChannelStore } from "@/stores/channels";
import { errorMessage } from "@/stores/chat";
import { useUiStore, type ColorModePreference, type ThemeFamily } from "@/stores/ui";
import UserAvatar from "./UserAvatar.vue";

type MailboxProvider = "qq" | "163" | "126" | "other";
type SmtpDraft = {
  provider: MailboxProvider;
  host: string;
  port: number;
  security: "tls" | "starttls";
  username: string;
  app_password: string;
};

const mailboxPresets: Record<Exclude<MailboxProvider, "other">, { host: string; port: number; security: "tls" | "starttls" }> = {
  qq: { host: "smtp.qq.com", port: 465, security: "tls" },
  "163": { host: "smtp.163.com", port: 465, security: "tls" },
  "126": { host: "smtp.126.com", port: 465, security: "tls" },
};

function initialSmtpDraft(): SmtpDraft {
  return { provider: "qq", ...mailboxPresets.qq, username: "", app_password: "" };
}

const auth = useAuthStore();
const channels = useChannelStore();
const ui = useUiStore();
const displayName = ref(auth.user?.display_name || "");
const currentPassword = ref("");
const newPassword = ref("");
const confirmPassword = ref("");
const status = ref("");
const failure = ref("");
const settingsAction = ref<"" | "profile" | "password">("");
const emailConnections = ref<WorkflowEmailConnection[]>([]);
const emailCapabilities = ref<WorkflowCapabilities>({});
const emailAction = ref("");
const emailFeedback = ref("");
const notificationEmail = ref<NotificationEmail>({ address: "", status: "missing", verified: false });
const notificationAddress = ref("");
const notificationCode = ref("");
const notificationVerificationPending = ref(false);
const notificationResendSeconds = ref(0);
const showSmtpForm = ref(false);
const smtp = ref<SmtpDraft>(initialSmtpDraft());
const testRecipients = ref<Record<string, string>>({});
const qqToken = computed({
  get: () => channels.pendingQqToken,
  set: (value: string) => { channels.pendingQqToken = value; },
});

function tr(chinese: string, english: string): string { return uiText(ui.language, chinese, english); }
function userId(): string { return auth.user?.id || "pre-auth"; }
const sections = computed(() => [
  { key: "general", label: tr("常规", "General"), icon: Settings2 },
  { key: "personalization", label: tr("个性化", "Personalization"), icon: Palette },
  { key: "profile", label: tr("个人资料", "Profile"), icon: UserRound },
  { key: "security", label: tr("账号与安全", "Account & security"), icon: KeyRound },
  { key: "connections", label: tr("连接与账号", "Connections & accounts"), icon: Link2 },
]);
const title = computed(() => sections.value.find((item) => item.key === ui.settingsSection)?.label || tr("设置", "Settings"));
const colorModes = computed(() => [
  { key: "system" as ColorModePreference, label: tr("跟随系统", "System"), icon: Monitor },
  { key: "light" as ColorModePreference, label: tr("浅色", "Light"), icon: Sun },
  { key: "dark" as ColorModePreference, label: tr("暗色", "Dark"), icon: Moon },
]);
const themeFamilies = computed(() => [
  { key: "classic" as ThemeFamily, label: tr("经典黑白", "Classic Mono") },
  { key: "obsidian" as ThemeFamily, label: tr("象牙曜石", "Ivory Obsidian") },
  { key: "ocean" as ThemeFamily, label: tr("深海蓝灰", "Ocean Blue") },
  { key: "sage" as ThemeFamily, label: tr("森雾浅绿", "Forest Mist") },
  { key: "aurora" as ThemeFamily, label: tr("雾紫极光", "Aurora Violet") },
  { key: "amber" as ThemeFamily, label: tr("琥珀暖砂", "Amber Sand") },
]);
const qqBindings = computed(() => channels.bindings.filter((item) => item.channel === "qq"));
const customMailbox = computed(() => smtp.value.provider === "other");
const authorizationHint = computed(() => {
  if (smtp.value.provider === "qq") return tr("在 QQ 邮箱设置中开启 SMTP 服务后生成；不是 QQ 登录密码。", "Generate it after enabling SMTP in QQ Mail settings; it is not your QQ password.");
  if (smtp.value.provider === "163" || smtp.value.provider === "126") return tr("在网易邮箱设置中开启 SMTP 服务后生成；不是邮箱登录密码。", "Generate it after enabling SMTP in NetEase Mail settings; it is not your mailbox password.");
  return tr("请填写邮箱服务商或企业管理员提供的 SMTP 授权码，不要填写网页登录密码。", "Use the SMTP app password from your provider or administrator, not your web password.");
});
const weixinQrSource = computed(() => {
  const value = channels.weixinAttempt?.qr_data || "";
  return /^data:image\/(?:png|jpeg|webp|gif);base64,/i.test(value) ? value : "";
});
const weixinAttemptTerminal = computed(() => (
  channels.weixinAttempt ? isWeixinAttemptTerminal(channels.weixinAttempt) : false
));
const weixinAttemptLabel = computed(() => {
  const labels: Record<string, [string, string]> = {
    creating_qr: ["正在生成二维码", "Creating QR code"],
    waiting_scan: ["等待微信扫码", "Waiting for Weixin scan"],
    scanned_pending_confirm: ["已扫码，请在微信中确认", "Scanned; confirm in Weixin"],
    connected: ["连接成功", "Connected"],
    expired: ["二维码已过期", "QR code expired"],
    cancelled: ["扫码已取消", "Scan cancelled"],
    account_conflict: ["微信账号冲突", "Weixin account conflict"],
    already_bound: ["该微信账号已被绑定", "This Weixin account is already connected"],
    verification_failed: ["微信验证失败", "Weixin verification failed"],
    upstream_unavailable: ["微信服务暂不可用", "Weixin service unavailable"],
    persist_failed: ["绑定信息保存失败", "Failed to save binding"],
  };
  const statusValue = channels.weixinAttempt?.status || "";
  const label = labels[statusValue];
  return label ? tr(label[0], label[1]) : statusValue;
});
const notificationRequestLabel = computed(() => {
  if (emailAction.value === "notification-request") return tr("发送中…", "Sending…");
  if (notificationResendSeconds.value > 0) {
    return tr(
      `${notificationResendSeconds.value} 秒后可重发`,
      `Resend in ${notificationResendSeconds.value}s`,
    );
  }
  return tr("发送验证码", "Send verification code");
});

let notificationResendTimer: ReturnType<typeof setInterval> | undefined;

function startNotificationResendCountdown(seconds: unknown): void {
  const normalized = Math.max(0, Math.ceil(Number(seconds) || 0));
  if (notificationResendTimer) clearInterval(notificationResendTimer);
  notificationResendSeconds.value = normalized;
  if (!normalized) {
    notificationResendTimer = undefined;
    return;
  }
  notificationResendTimer = setInterval(() => {
    if (notificationResendSeconds.value <= 1) {
      notificationResendSeconds.value = 0;
      if (notificationResendTimer) clearInterval(notificationResendTimer);
      notificationResendTimer = undefined;
      return;
    }
    notificationResendSeconds.value -= 1;
  }, 1000);
}

onMounted(() => {
  if (ui.settingsSection === "channels") ui.settingsSection = "connections";
  if (ui.settingsSection === "connections") void refreshConnections();
});

onBeforeUnmount(() => {
  if (notificationResendTimer) clearInterval(notificationResendTimer);
});

async function saveProfile() {
  if (settingsAction.value) return;
  settingsAction.value = "profile";
  failure.value = ""; status.value = "";
  try { await auth.updateProfile(displayName.value); status.value = tr("个人资料已保存", "Profile saved"); }
  catch (error) { failure.value = errorMessage(error); }
  finally { settingsAction.value = ""; }
}
async function changePassword() {
  if (newPassword.value !== confirmPassword.value) { failure.value = tr("两次输入的新密码不一致", "The new passwords do not match"); return; }
  if (settingsAction.value) return;
  settingsAction.value = "password";
  failure.value = ""; status.value = "";
  try { await auth.changePassword(currentPassword.value, newPassword.value); ui.settingsOpen = false; }
  catch (error) { failure.value = errorMessage(error); }
  finally { settingsAction.value = ""; }
}
async function authorizeQq() {
  try {
    await channels.authorizeQq(qqToken.value);
    const url = new URL(window.location.href);
    url.searchParams.delete("channel_bind");
    url.searchParams.delete("token");
    history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
    status.value = tr("QQ 已绑定", "QQ connected");
  }
  catch (error) { failure.value = errorMessage(error); }
}
function selectSection(key: string) { ui.settingsSection = key; if (key === "connections") void refreshConnections(); }
function chooseColorMode(colorMode: ColorModePreference) { ui.setColorMode(colorMode, userId()); }
function chooseThemeFamily(themeFamily: ThemeFamily) { ui.setThemeFamily(themeFamily, userId()); }
function chooseLanguage(language: UiLanguage) { ui.setLanguage(language, userId()); }

function applyMailboxProvider(): void {
  if (smtp.value.provider === "other") {
    smtp.value.host = "";
    smtp.value.port = 465;
    smtp.value.security = "tls";
    return;
  }
  Object.assign(smtp.value, mailboxPresets[smtp.value.provider]);
}

function detectMailboxProvider(): void {
  const domain = smtp.value.username.trim().split("@").pop()?.toLowerCase();
  const detected: MailboxProvider | undefined = domain === "qq.com" ? "qq" : domain === "163.com" ? "163" : domain === "126.com" ? "126" : domain?.includes(".") ? "other" : undefined;
  if (!detected || detected === smtp.value.provider) return;
  smtp.value.provider = detected;
  applyMailboxProvider();
}

function connectionProviderLabel(provider: string): string {
  return provider === "smtp_personal"
    ? tr("SMTP 代发", "SMTP sender")
    : tr("不再支持的邮箱连接", "Unsupported email connection");
}

function connectionStatusLabel(value: string): string {
  if (value === "active") return tr("连接正常", "Connected");
  if (value === "reauthorization_required") return tr("需要重新连接", "Reconnect required");
  return tr("暂不可用", "Unavailable");
}

function weixinStatusLabel(value: string): string {
  if (["active", "connected", "bound"].includes(value)) return tr("已连接", "Connected");
  if (value === "unbound") return tr("未连接", "Not connected");
  if (value === "reconnect_required") return tr("需要重新连接", "Reconnect required");
  if (value === "reconnecting") return tr("正在恢复连接", "Reconnecting");
  if (value === "degraded") return tr("连接异常", "Connection issue");
  if (value === "unknown") return tr("正在读取状态", "Loading status");
  return tr("暂不可用", "Unavailable");
}

function emailError(error: unknown): string {
  if (!(error instanceof ApiError)) return errorMessage(error);
  const labels: Record<string, string> = {
    CONNECTION_PROVIDER_UNSUPPORTED: tr("系统还没有完成邮件连接配置。", "Email connections are not configured on this system."),
    CONNECTION_CREDENTIAL_KEY_MISSING: tr("系统还没有配置连接加密密钥。", "The connection encryption key is not configured."),
    CONNECTION_SMTP_INSECURE: tr("请使用 465/TLS 或 587/STARTTLS。", "Use 465/TLS or 587/STARTTLS."),
    EMAIL_REJECTED: tr("邮箱服务器拒绝了连接或邮件，请检查账号和授权码。", "The email server rejected the connection or message."),
    EMAIL_OUTCOME_UNKNOWN: tr("邮件结果暂时无法确认，请先检查收件箱，不要立即重复发送。", "The email outcome is unknown. Check the inbox before retrying."),
    EMAIL_RECIPIENT_INVALID: tr("请输入正确的测试收件邮箱。", "Enter a valid test recipient."),
    OFFICIAL_EMAIL_NOT_CONFIGURED: tr("系统还没有配置官方发信邮箱。", "Official email is not configured."),
    NOTIFICATION_EMAIL_UNAVAILABLE: tr("系统暂时无法保存我的邮箱。", "My email storage is unavailable."),
    NOTIFICATION_EMAIL_NOT_VERIFIED: tr("请先验证我的邮箱。", "Verify my email first."),
    NOTIFICATION_EMAIL_CODE_INVALID: tr("验证码错误或已经过期，请重新获取。", "The code is invalid or expired. Request a new one."),
    NOTIFICATION_EMAIL_VERIFICATION_RATE_LIMITED: tr("验证码发送过于频繁，请等待倒计时结束。", "Verification requests are too frequent. Wait for the countdown."),
  };
  return labels[error.code] || tr("邮件连接操作失败，请检查配置后重试。", "The email connection action failed. Check the configuration and retry.");
}

async function refreshConnections() {
  failure.value = "";
  emailFeedback.value = "";
  try { emailCapabilities.value = await api.workflowCapabilities(); }
  catch { emailCapabilities.value = {}; }
  if (emailCapabilities.value.personal_email?.available === false) {
    emailConnections.value = [];
  } else {
    try { emailConnections.value = (await api.workflowEmailConnections()).connections || []; }
    catch (error) { failure.value = emailError(error); emailConnections.value = []; }
  }
  try {
    notificationEmail.value = (await api.notificationEmail()).email;
    if (notificationEmail.value.address) notificationAddress.value = notificationEmail.value.address;
  } catch (error) {
    failure.value = emailError(error);
    notificationEmail.value = { address: "", status: "missing", verified: false };
  }
  await channels.refresh();
}

async function requestNotificationVerification() {
  emailAction.value = "notification-request";
  failure.value = ""; emailFeedback.value = "";
  try {
    const result = await api.requestNotificationEmailVerification(notificationAddress.value.trim());
    startNotificationResendCountdown(result.retry_after_seconds);
    notificationVerificationPending.value = true;
    notificationCode.value = "";
    emailFeedback.value = tr("验证码已发送，请在 10 分钟内完成验证。", "Verification code sent. Complete verification within 10 minutes.");
  } catch (error) {
    if (error instanceof ApiError && error.code === "NOTIFICATION_EMAIL_VERIFICATION_RATE_LIMITED") {
      startNotificationResendCountdown(error.details.retry_after_seconds);
    }
    failure.value = emailError(error);
  }
  finally { emailAction.value = ""; }
}

async function verifyNotificationAddress() {
  emailAction.value = "notification-verify";
  failure.value = ""; emailFeedback.value = "";
  try {
    notificationEmail.value = (await api.verifyNotificationEmail(
      notificationAddress.value.trim(), notificationCode.value.trim(),
    )).email;
    notificationVerificationPending.value = false;
    notificationCode.value = "";
    emailFeedback.value = tr("我的邮箱已验证，可以接收官方通知。", "My email is verified and can receive official notifications.");
  } catch (error) { failure.value = emailError(error); }
  finally { emailAction.value = ""; }
}

async function testNotificationAddress() {
  emailAction.value = "notification-test";
  failure.value = ""; emailFeedback.value = "";
  try {
    await api.testNotificationEmail();
    emailFeedback.value = tr("官方测试通知已被服务商接收，请到收件箱确认。", "The official test notification was accepted. Confirm it in your inbox.");
  } catch (error) { failure.value = emailError(error); }
  finally { emailAction.value = ""; }
}

async function saveSmtpConnection() {
  emailAction.value = "smtp";
  failure.value = ""; emailFeedback.value = "";
  try {
    await api.createSmtpEmailConnection({
      host: smtp.value.host.trim(),
      port: Number(smtp.value.port),
      security: smtp.value.security,
      username: smtp.value.username.trim(),
      app_password: smtp.value.app_password,
    });
    smtp.value = initialSmtpDraft();
    showSmtpForm.value = false;
    emailFeedback.value = tr("邮箱连接成功。建议立即发送一封测试邮件。", "Email connected. Send a test message now.");
    await refreshConnections();
  } catch (error) { failure.value = emailError(error); }
  finally { emailAction.value = ""; }
}

async function testEmail(connection: WorkflowEmailConnection) {
  emailAction.value = `test:${connection.id}`;
  failure.value = ""; emailFeedback.value = "";
  try {
    await api.testEmailConnection(connection.id, testRecipients.value[connection.id] || connection.account_display);
    emailFeedback.value = tr("测试邮件已被服务商接收，请到收件箱确认。", "The provider accepted the test email. Confirm it in the inbox.");
  } catch (error) { failure.value = emailError(error); }
  finally { emailAction.value = ""; }
}

async function deleteEmail(connection: WorkflowEmailConnection) {
  if (!window.confirm(tr(`确认删除 ${connection.account_display} 的邮件连接？`, `Delete the email connection for ${connection.account_display}?`))) return;
  emailAction.value = `delete:${connection.id}`;
  failure.value = "";
  try { await api.deleteEmailConnection(connection.id); await refreshConnections(); }
  catch (error) { failure.value = emailError(error); }
  finally { emailAction.value = ""; }
}
</script>

<template>
  <div class="modal-backdrop settings-backdrop" @click.self="ui.settingsOpen = false">
    <section class="settings-center" role="dialog" aria-modal="true" :aria-label="title">
      <aside class="settings-nav">
        <header><strong>{{ tr('设置', 'Settings') }}</strong><button class="icon-button mobile-close" @click="ui.settingsOpen = false"><X :size="18" /></button></header>
        <button v-for="section in sections" :key="section.key" :class="{ active: ui.settingsSection === section.key }" @click="selectSection(section.key)"><component :is="section.icon" :size="18" />{{ section.label }}</button>
      </aside>
      <main class="settings-detail">
        <header><div><span class="eyebrow">ZhiCe-Agent</span><h2>{{ title }}</h2></div><button class="icon-button" :aria-label="tr('关闭设置', 'Close settings')" @click="ui.settingsOpen = false"><X :size="20" /></button></header>
        <p v-if="failure" class="form-error" role="alert" aria-live="assertive">{{ failure }}</p><p v-if="status" class="form-success" role="status" aria-live="polite">{{ status }}</p>
        <section v-if="ui.settingsSection === 'general'" class="setting-section">
          <div class="setting-row"><span><strong>{{ tr('语言', 'Language') }}</strong><small>{{ tr('当前产品界面语言', 'Interface language') }}</small></span><select :value="ui.language" @change="chooseLanguage(($event.target as HTMLSelectElement).value as UiLanguage)"><option value="zh-CN">简体中文</option><option value="en">English</option></select></div>
          <div class="setting-row"><span><strong>{{ tr('界面密度', 'Interface density') }}</strong><small>{{ tr('调整列表和表单的间距', 'Adjust spacing in lists and forms') }}</small></span><select v-model="ui.density" @change="ui.persist(userId())"><option value="comfortable">{{ tr('舒适', 'Comfortable') }}</option><option value="compact">{{ tr('紧凑', 'Compact') }}</option></select></div>
          <div class="setting-row"><span><strong>{{ tr('启动页面', 'Start page') }}</strong><small>{{ tr('登录后首先打开的位置', 'The first page after sign-in') }}</small></span><select v-model="ui.startPage" @change="ui.persist(userId())"><option value="chat">{{ tr('聊天', 'Chat') }}</option><option value="new">{{ tr('新会话', 'New Session') }}</option></select></div>
        </section>
        <section v-else-if="ui.settingsSection === 'personalization'" class="setting-section">
          <div class="appearance-section">
            <h3>{{ tr('外观模式', 'Appearance mode') }}</h3>
            <p>{{ tr('跟随系统，或固定使用当前主题的浅色与暗色版本。', 'Follow the system, or keep the selected theme in light or dark mode.') }}</p>
            <div class="appearance-mode-grid">
              <button v-for="mode in colorModes" :key="mode.key" type="button" :class="{ active: ui.colorMode === mode.key }" :aria-pressed="ui.colorMode === mode.key" @click="chooseColorMode(mode.key)">
                <component :is="mode.icon" :size="17" />
                <span>{{ mode.label }}</span>
              </button>
            </div>
          </div>
          <div class="theme-family-section">
            <h3>{{ tr('主题风格', 'Theme style') }}</h3>
            <p>{{ tr('选择配色家族，浅色与暗色切换会保留当前主题。', 'Choose a color family. Light and dark mode keep the current theme.') }}</p>
            <div class="theme-grid">
              <button v-for="theme in themeFamilies" :key="theme.key" type="button" :class="{ active: ui.themeFamily === theme.key }" :aria-pressed="ui.themeFamily === theme.key" @click="chooseThemeFamily(theme.key)">
                <span :class="['theme-preview', `theme-${theme.key}`, ui.resolvedTheme]"><i></i></span>
                <strong>{{ theme.label }}</strong>
              </button>
            </div>
          </div>
        </section>
        <form v-else-if="ui.settingsSection === 'profile'" class="setting-section" @submit.prevent="saveProfile">
          <div class="profile-preview"><UserAvatar :name="displayName || auth.user?.username || ''" /><div><strong>{{ displayName || auth.user?.username }}</strong><small>@{{ auth.user?.username }}</small></div></div>
          <label><span>{{ tr('账号', 'Account') }}</span><input :value="auth.user?.username" disabled /></label><label><span>{{ tr('昵称', 'Nickname') }}</span><input v-model="displayName" maxlength="120" required /></label><button class="primary-button" :disabled="Boolean(settingsAction)">{{ settingsAction === 'profile' ? tr('保存中…', 'Saving…') : tr('保存资料', 'Save profile') }}</button>
        </form>
        <form v-else-if="ui.settingsSection === 'security'" class="setting-section" @submit.prevent="changePassword">
          <div class="security-note"><KeyRound :size="21" /><span><strong>{{ tr('修改密码后将退出当前账号', 'Changing your password signs you out') }}</strong><small>{{ tr('所有现有登录 Session 会被撤销，需要重新登录。', 'All active login sessions will be revoked.') }}</small></span></div>
          <label><span>{{ tr('当前密码', 'Current password') }}</span><input v-model="currentPassword" type="password" autocomplete="current-password" required /></label><label><span>{{ tr('新密码', 'New password') }}</span><input v-model="newPassword" type="password" autocomplete="new-password" minlength="8" required /></label><label><span>{{ tr('确认新密码', 'Confirm new password') }}</span><input v-model="confirmPassword" type="password" autocomplete="new-password" minlength="8" required /></label><button class="primary-button" :disabled="Boolean(settingsAction)">{{ settingsAction === 'password' ? tr('修改中…', 'Changing…') : tr('修改密码', 'Change password') }}</button>
        </form>
        <section v-else class="setting-section channel-settings connection-settings">
          <div class="connection-intro">
            <span><Mail :size="20" /></span>
            <div><strong>{{ tr('我的邮箱', 'My email') }}</strong><p>{{ tr('这个邮箱用于接收智策官方通知；如需使用自己的邮箱向其他人发信，可以额外配置 SMTP 代发。', 'This address receives official ZhiCe notifications. Configure SMTP separately only when you want to send from your own mailbox.') }}</p></div>
          </div>
          <section class="connected-email-list notification-email-card">
            <article>
              <div><span class="email-provider-mark">@</span><span><strong>{{ notificationEmail.verified ? notificationEmail.address : tr('尚未设置我的邮箱', 'My email is not set') }}</strong><small>{{ notificationEmail.verified ? tr('已验证 · 可接收官方通知', 'Verified · receives official notifications') : tr('验证后即可接收官方通知', 'Verify it to receive official notifications') }}</small></span></div>
              <span class="connection-row-actions" v-if="notificationEmail.verified"><button :disabled="Boolean(emailAction)" @click="testNotificationAddress">{{ emailAction === 'notification-test' ? tr('发送中…', 'Sending…') : tr('发送测试通知', 'Send test notification') }}</button></span>
            </article>
            <form class="smtp-connection-form notification-email-form" @submit.prevent="requestNotificationVerification">
              <label>{{ notificationEmail.verified ? tr('更换我的邮箱', 'Change my email') : tr('邮箱地址', 'Email address') }}<input v-model="notificationAddress" type="email" autocomplete="email" placeholder="name@example.com" required /></label>
              <button class="primary-button" :disabled="Boolean(emailAction) || notificationResendSeconds > 0 || emailCapabilities.official_notification?.code === 'OFFICIAL_EMAIL_NOT_CONFIGURED'">{{ notificationRequestLabel }}</button>
            </form>
            <form v-if="notificationVerificationPending" class="smtp-connection-form notification-email-form" @submit.prevent="verifyNotificationAddress">
              <label>{{ tr('邮箱验证码', 'Verification code') }}<input v-model="notificationCode" inputmode="numeric" maxlength="12" autocomplete="one-time-code" required /></label>
              <button class="primary-button" :disabled="Boolean(emailAction)">{{ emailAction === 'notification-verify' ? tr('验证中…', 'Verifying…') : tr('完成验证', 'Verify email') }}</button>
            </form>
          </section>
          <div class="connection-divider"><span>{{ tr('SMTP 代发（可选）', 'SMTP sender (optional)') }}</span></div>
          <p v-if="emailCapabilities.personal_email?.available === false" class="connection-unavailable">{{ tr('当前系统还没有启用安全的邮件连接存储，因此暂时不能配置 SMTP 代发；这不影响接收官方通知。', 'Secure connection storage is unavailable, so SMTP sending cannot be configured. Official notifications are unaffected.') }}</p>
          <template v-else>
            <div class="email-provider-grid">
              <article class="email-provider-card" data-enabled="true"><span class="email-provider-mark smtp">@</span><div><strong>{{ tr('使用我的邮箱代发', 'Send from my mailbox') }}</strong><small>{{ tr('配置 SMTP 授权码后，工作流可以向其他收件人发信', 'Add an SMTP app password so workflows can email other recipients') }}</small></div><button :disabled="Boolean(emailAction)" @click="showSmtpForm = !showSmtpForm">{{ showSmtpForm ? tr('收起', 'Close') : tr('配置 SMTP', 'Configure SMTP') }}</button></article>
            </div>
            <form v-if="showSmtpForm" class="smtp-connection-form" @submit.prevent="saveSmtpConnection">
              <header><div><strong>{{ tr('配置 SMTP 代发', 'Configure SMTP sender') }}</strong><small>{{ tr('选择邮箱类型，再填写邮箱地址和授权码；凭据只会加密保存在你的账号下。', 'Choose a mailbox type, address, and app password. Credentials remain encrypted under your account.') }}</small></div></header>
              <label>{{ tr('邮箱类型', 'Mailbox type') }}<select v-model="smtp.provider" class="mailbox-provider-select" @change="applyMailboxProvider"><option value="qq">QQ 邮箱</option><option value="163">网易 163 邮箱</option><option value="126">网易 126 邮箱</option><option value="other">{{ tr('其他或企业邮箱', 'Other or business mailbox') }}</option></select></label>
              <label>{{ tr('发件邮箱', 'Sender address') }}<input v-model="smtp.username" type="email" autocomplete="username" placeholder="name@example.com" required @blur="detectMailboxProvider" /><small>{{ tr('只用于 SMTP 代发；我的邮箱仍用于接收官方通知。', 'Used only for SMTP sending; My email still receives official notifications.') }}</small></label>
              <label>{{ tr('邮箱授权码', 'App password') }}<input v-model="smtp.app_password" type="password" autocomplete="new-password" required /><small>{{ authorizationHint }}</small></label>
              <section v-if="customMailbox" class="custom-smtp-settings">
                <header><strong>{{ tr('其他邮箱服务器设置', 'Other mailbox server settings') }}</strong><small>{{ tr('以下三项请向邮箱服务商或企业管理员索取。', 'Ask your email provider or administrator for these three values.') }}</small></header>
                <label>{{ tr('发信服务器', 'Outgoing mail server') }}<input v-model="smtp.host" placeholder="smtp.example.com" required /></label>
                <div class="workflow-inline-fields"><label>{{ tr('连接方式', 'Connection security') }}<select v-model="smtp.security" @change="smtp.port = smtp.security === 'tls' ? 465 : 587"><option value="tls">TLS（465）</option><option value="starttls">STARTTLS（587）</option></select></label><label>{{ tr('端口', 'Port') }}<input v-model.number="smtp.port" type="number" :min="smtp.security === 'tls' ? 465 : 587" :max="smtp.security === 'tls' ? 465 : 587" required /></label></div>
              </section>
              <button class="primary-button" :disabled="Boolean(emailAction)">{{ emailAction === 'smtp' ? tr('正在验证…', 'Verifying…') : tr('验证并连接', 'Verify and connect') }}</button>
            </form>
            <section v-if="emailConnections.length" class="connected-email-list">
              <h3>{{ tr('已配置的 SMTP 代发账号', 'Configured SMTP senders') }}</h3>
              <article v-for="connection in emailConnections" :key="connection.id">
                <div><span class="email-provider-mark">{{ connectionProviderLabel(connection.provider).slice(0, 1) }}</span><span><strong>{{ connection.account_display }}</strong><small>{{ connectionProviderLabel(connection.provider) }} · {{ connectionStatusLabel(connection.status) }}</small></span></div>
                <label>{{ tr('测试收件邮箱', 'Test recipient') }}<input v-model="testRecipients[connection.id]" type="email" :placeholder="connection.account_display" /></label>
                <span class="connection-row-actions"><button :disabled="Boolean(emailAction) || connection.status !== 'active'" @click="testEmail(connection)">{{ emailAction === `test:${connection.id}` ? tr('发送中…', 'Sending…') : tr('发送测试', 'Send test') }}</button><button class="danger-text" :disabled="Boolean(emailAction)" @click="deleteEmail(connection)">{{ tr('删除', 'Delete') }}</button></span>
              </article>
            </section>
            <p v-else class="muted">{{ tr('未配置 SMTP 代发。你仍然可以正常接收官方通知。', 'SMTP sending is not configured. You can still receive official notifications.') }}</p>
          </template>
          <p v-if="emailFeedback" class="form-success">{{ emailFeedback }}</p>
          <div class="connection-divider"><span>{{ tr('消息渠道', 'Messaging channels') }}</span></div>
          <p v-if="channels.error" class="form-error">{{ channels.error }}</p>
          <p v-if="channels.qqAuthorizationError" class="form-error">{{ channels.qqAuthorizationError }}</p>
          <div class="channel-card">
            <div><span class="channel-icon qq">QQ</span><span><strong>{{ tr('QQ 机器人', 'QQ bot') }}</strong><small>{{ qqBindings.length ? tr('已连接', 'Connected') : tr('未连接', 'Not connected') }}</small></span></div>
            <p class="muted">{{ tr('群聊：先 @机器人，再发送生成的 /bind 命令。私聊：直接发送该命令。', 'Group chat: @mention the bot first, then send the generated /bind command. Direct chat: send the command directly.') }}</p>
            <button :disabled="channels.busy" @click="channels.generateQqCode">{{ channels.busy ? tr('处理中…', 'Working…') : tr('生成绑定码', 'Generate binding code') }}</button>
            <template v-if="channels.qqCommand">
              <pre>{{ channels.qqCommand }}</pre>
              <small>{{ tr('绑定码为一次性短期凭据，请勿转发给他人。', 'The binding code is a short-lived one-time credential. Do not share it.') }}</small>
            </template>
            <div v-if="qqToken" class="inline-bind"><input v-model="qqToken" /><button class="primary-button channel-bind-action" :disabled="channels.busy" @click="authorizeQq">{{ channels.busy ? tr('绑定中…', 'Connecting…') : tr('完成绑定', 'Complete binding') }}</button></div>
            <div v-for="binding in qqBindings" :key="binding.binding_id" class="binding-row"><span>{{ binding.display_name || tr('QQ 身份', 'QQ identity') }}</span><button class="danger-text" :disabled="channels.busy" @click="channels.unlink(binding.binding_id)">{{ channels.busy ? tr('处理中…', 'Working…') : tr('解绑', 'Unlink') }}</button></div>
          </div>
          <div class="channel-card">
            <div><span class="channel-icon weixin">微</span><span><strong>{{ tr('微信', 'Weixin') }}</strong><small>{{ weixinStatusLabel(channels.weixin.status) }}</small></span></div>
            <img v-if="weixinQrSource" class="weixin-qr" :src="weixinQrSource" :alt="tr('微信绑定二维码', 'Weixin binding QR code')" />
            <p v-if="channels.weixinAttempt" :class="{ 'form-error': weixinAttemptTerminal && channels.weixinAttempt.status !== 'connected' && channels.weixinAttempt.status !== 'cancelled' }">{{ weixinAttemptLabel }}</p>
            <p v-if="channels.weixinAttempt?.error_code" class="form-error"><code>{{ channels.weixinAttempt.error_code }}</code></p>
            <p v-if="channels.weixinError" class="form-error"><code>{{ channels.weixinError.code }}</code> · {{ channels.weixinError.message }}</p>
            <p v-if="['unavailable','disabled'].includes(channels.weixin.status)" class="muted">{{ tr('当前 Gateway 未启用微信连接。', 'Weixin is not enabled on this Gateway.') }}</p>
            <div class="channel-actions">
              <button v-if="!['active','connected','bound','unavailable','disabled'].includes(channels.weixin.status) && !channels.weixinAttempt" :disabled="channels.weixinBusy" @click="channels.startWeixin">{{ tr('扫码连接', 'Connect by QR code') }}</button>
              <button v-if="channels.weixinAttempt && !weixinAttemptTerminal" :disabled="channels.weixinBusy" @click="channels.cancelWeixin">{{ tr('取消扫码', 'Cancel scan') }}</button>
              <button v-if="channels.weixinError || (weixinAttemptTerminal && channels.weixinAttempt?.status !== 'connected')" :disabled="channels.weixinBusy" @click="channels.retryWeixin">{{ tr('重试扫码', 'Retry scan') }}</button>
              <button v-if="channels.weixin.status === 'reconnect_required'" :disabled="channels.weixinBusy" @click="channels.reconnectWeixin">{{ tr('重新连接', 'Reconnect') }}</button>
              <button v-if="['active','connected','bound','reconnect_required'].includes(channels.weixin.status)" class="danger-text" :disabled="channels.weixinBusy" @click="channels.unlinkWeixin">{{ tr('解绑', 'Unlink') }}</button>
            </div>
          </div>
        </section>
      </main>
    </section>
  </div>
</template>
