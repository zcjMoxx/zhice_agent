<script setup lang="ts">
import { KeyRound, Link2, Monitor, Moon, Palette, Settings2, Sun, UserRound, X } from "@lucide/vue";
import { computed, onMounted, ref } from "vue";

import { uiText, type UiLanguage } from "@/i18n";
import { useAuthStore } from "@/stores/auth";
import { useChannelStore } from "@/stores/channels";
import { errorMessage } from "@/stores/chat";
import { useUiStore, type ColorModePreference, type ThemeFamily } from "@/stores/ui";
import UserAvatar from "./UserAvatar.vue";

const auth = useAuthStore();
const channels = useChannelStore();
const ui = useUiStore();
const displayName = ref(auth.user?.display_name || "");
const currentPassword = ref("");
const newPassword = ref("");
const confirmPassword = ref("");
const status = ref("");
const failure = ref("");
const qqToken = ref(new URLSearchParams(window.location.search).get("channel_bind") || "");

function tr(chinese: string, english: string): string { return uiText(ui.language, chinese, english); }
function userId(): string { return auth.user?.id || "pre-auth"; }
const sections = computed(() => [
  { key: "general", label: tr("常规", "General"), icon: Settings2 },
  { key: "personalization", label: tr("个性化", "Personalization"), icon: Palette },
  { key: "profile", label: tr("个人资料", "Profile"), icon: UserRound },
  { key: "security", label: tr("账号与安全", "Account & security"), icon: KeyRound },
  { key: "channels", label: tr("渠道连接", "Channel connections"), icon: Link2 },
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

onMounted(() => { if (ui.settingsSection === "channels") void channels.refresh(); });

async function saveProfile() {
  try { await auth.updateProfile(displayName.value); status.value = tr("个人资料已保存", "Profile saved"); }
  catch (error) { failure.value = errorMessage(error); }
}
async function changePassword() {
  if (newPassword.value !== confirmPassword.value) { failure.value = tr("两次输入的新密码不一致", "The new passwords do not match"); return; }
  try { await auth.changePassword(currentPassword.value, newPassword.value); ui.settingsOpen = false; }
  catch (error) { failure.value = errorMessage(error); }
}
async function authorizeQq() {
  try { await channels.authorizeQq(qqToken.value); history.replaceState({}, "", window.location.pathname); qqToken.value = ""; status.value = tr("QQ 已绑定", "QQ connected"); }
  catch (error) { failure.value = errorMessage(error); }
}
function selectSection(key: string) { ui.settingsSection = key; if (key === "channels") void channels.refresh(); }
function chooseColorMode(colorMode: ColorModePreference) { ui.setColorMode(colorMode, userId()); }
function chooseThemeFamily(themeFamily: ThemeFamily) { ui.setThemeFamily(themeFamily, userId()); }
function chooseLanguage(language: UiLanguage) { ui.setLanguage(language, userId()); }
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
        <p v-if="failure" class="form-error">{{ failure }}</p><p v-if="status" class="form-success">{{ status }}</p>
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
          <label><span>{{ tr('用户名', 'Username') }}</span><input :value="auth.user?.username" disabled /></label><label><span>{{ tr('显示名称', 'Display name') }}</span><input v-model="displayName" maxlength="120" required /></label><button class="primary-button">{{ tr('保存资料', 'Save profile') }}</button>
        </form>
        <form v-else-if="ui.settingsSection === 'security'" class="setting-section" @submit.prevent="changePassword">
          <div class="security-note"><KeyRound :size="21" /><span><strong>{{ tr('修改密码后将退出当前账号', 'Changing your password signs you out') }}</strong><small>{{ tr('所有现有登录 Session 会被撤销，需要重新登录。', 'All active login sessions will be revoked.') }}</small></span></div>
          <label><span>{{ tr('当前密码', 'Current password') }}</span><input v-model="currentPassword" type="password" autocomplete="current-password" required /></label><label><span>{{ tr('新密码', 'New password') }}</span><input v-model="newPassword" type="password" autocomplete="new-password" minlength="8" required /></label><label><span>{{ tr('确认新密码', 'Confirm new password') }}</span><input v-model="confirmPassword" type="password" autocomplete="new-password" minlength="8" required /></label><button class="primary-button">{{ tr('修改密码', 'Change password') }}</button>
        </form>
        <section v-else class="setting-section channel-settings">
          <p v-if="channels.error" class="form-error">{{ channels.error }}</p>
          <div class="channel-card"><div><span class="channel-icon qq">QQ</span><span><strong>QQ 机器人</strong><small>{{ channels.bindings.filter((item) => item.channel === 'qq').length ? '已连接' : '未连接' }}</small></span></div><button @click="channels.generateQqCode">生成绑定码</button><pre v-if="channels.qqCommand">{{ channels.qqCommand }}</pre><div v-if="qqToken" class="inline-bind"><input v-model="qqToken" /><button class="primary-button" @click="authorizeQq">完成授权</button></div><div v-for="binding in channels.bindings.filter((item) => item.channel === 'qq')" :key="binding.binding_id" class="binding-row"><span>{{ binding.display_name || 'QQ 身份' }}</span><button class="danger-text" @click="channels.unlink(binding.binding_id)">解绑</button></div></div>
          <div class="channel-card"><div><span class="channel-icon weixin">微</span><span><strong>微信</strong><small>{{ channels.weixin.status }}</small></span></div><img v-if="channels.weixinAttempt?.qr_data" class="weixin-qr" :src="channels.weixinAttempt.qr_data" alt="微信绑定二维码" /><p v-if="['unavailable','disabled'].includes(channels.weixin.status)" class="muted">当前 Gateway 未启用微信连接。</p><div class="channel-actions"><button v-if="!['active','connected','bound','unavailable','disabled'].includes(channels.weixin.status) && !channels.weixinAttempt" @click="channels.startWeixin">扫码连接</button><button v-if="channels.weixinAttempt" @click="channels.cancelWeixin">取消扫码</button><button v-if="channels.weixin.status === 'reconnect_required'" @click="channels.reconnectWeixin">重新连接</button><button v-if="['active','connected','bound','reconnect_required'].includes(channels.weixin.status)" class="danger-text" @click="channels.unlinkWeixin">解绑</button></div></div>
        </section>
      </main>
    </section>
  </div>
</template>
