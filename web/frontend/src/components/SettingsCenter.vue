<script setup lang="ts">
import { KeyRound, Link2, Palette, Settings2, UserRound, X } from "@lucide/vue";
import { computed, onMounted, ref } from "vue";

import { useAuthStore } from "@/stores/auth";
import { useChannelStore } from "@/stores/channels";
import { errorMessage } from "@/stores/chat";
import { useUiStore, type ThemePreference } from "@/stores/ui";
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

const sections = [
  { key: "general", label: "常规", icon: Settings2 },
  { key: "personalization", label: "个性化", icon: Palette },
  { key: "profile", label: "个人资料", icon: UserRound },
  { key: "security", label: "账号与安全", icon: KeyRound },
  { key: "channels", label: "渠道连接", icon: Link2 },
];
const title = computed(() => sections.find((item) => item.key === ui.settingsSection)?.label || "设置");

onMounted(() => { if (ui.settingsSection === "channels") void channels.refresh(); });

async function saveProfile() {
  try { await auth.updateProfile(displayName.value); status.value = "个人资料已保存"; }
  catch (error) { failure.value = errorMessage(error); }
}
async function changePassword() {
  if (newPassword.value !== confirmPassword.value) { failure.value = "两次输入的新密码不一致"; return; }
  try { await auth.changePassword(currentPassword.value, newPassword.value); ui.settingsOpen = false; }
  catch (error) { failure.value = errorMessage(error); }
}
async function authorizeQq() {
  try { await channels.authorizeQq(qqToken.value); history.replaceState({}, "", window.location.pathname); qqToken.value = ""; status.value = "QQ 已绑定"; }
  catch (error) { failure.value = errorMessage(error); }
}
function selectSection(key: string) { ui.settingsSection = key; if (key === "channels") void channels.refresh(); }
function chooseTheme(theme: ThemePreference) { ui.setTheme(theme, auth.user?.id || "pre-auth"); }
</script>

<template>
  <div class="modal-backdrop settings-backdrop" @click.self="ui.settingsOpen = false">
    <section class="settings-center" role="dialog" aria-modal="true" :aria-label="title">
      <aside class="settings-nav">
        <header><strong>设置</strong><button class="icon-button mobile-close" @click="ui.settingsOpen = false"><X :size="18" /></button></header>
        <button v-for="section in sections" :key="section.key" :class="{ active: ui.settingsSection === section.key }" @click="selectSection(section.key)"><component :is="section.icon" :size="18" />{{ section.label }}</button>
      </aside>
      <main class="settings-detail">
        <header><div><span class="eyebrow">ZhiCe-Agent</span><h2>{{ title }}</h2></div><button class="icon-button" aria-label="关闭设置" @click="ui.settingsOpen = false"><X :size="20" /></button></header>
        <p v-if="failure" class="form-error">{{ failure }}</p><p v-if="status" class="form-success">{{ status }}</p>
        <section v-if="ui.settingsSection === 'general'" class="setting-section">
          <div class="setting-row"><span><strong>语言</strong><small>当前产品界面语言</small></span><select><option>简体中文</option></select></div>
          <div class="setting-row"><span><strong>界面密度</strong><small>调整列表和表单的间距</small></span><select v-model="ui.density" @change="ui.persist(auth.user?.id || 'pre-auth')"><option value="comfortable">舒适</option><option value="compact">紧凑</option></select></div>
          <div class="setting-row"><span><strong>启动页面</strong><small>登录后首先打开的位置</small></span><select v-model="ui.startPage" @change="ui.persist(auth.user?.id || 'pre-auth')"><option value="chat">聊天</option><option value="last">上次 Session</option></select></div>
        </section>
        <section v-else-if="ui.settingsSection === 'personalization'" class="setting-section">
          <div><h3>曜石主题</h3><p>毛玻璃用于结构面，消息正文始终保持清晰。</p><div class="theme-grid"><button v-for="theme in (['system','light','dark'] as ThemePreference[])" :key="theme" :class="{ active: ui.theme === theme }" @click="chooseTheme(theme)"><span :class="`theme-preview ${theme}`"></span><strong>{{ { system: '跟随系统', light: '浅色曜石', dark: '暗色曜石' }[theme] }}</strong></button></div></div>
          <div class="setting-row"><span><strong>聊天内容宽度</strong><small>只影响阅读区，不改变 Session 侧栏</small></span><select v-model="ui.contentWidth" @change="ui.persist(auth.user?.id || 'pre-auth')"><option value="standard">标准</option><option value="wide">宽</option></select></div>
        </section>
        <form v-else-if="ui.settingsSection === 'profile'" class="setting-section" @submit.prevent="saveProfile">
          <div class="profile-preview"><UserAvatar :name="displayName || auth.user?.username || ''" /><div><strong>{{ displayName || auth.user?.username }}</strong><small>@{{ auth.user?.username }}</small></div></div>
          <label><span>用户名</span><input :value="auth.user?.username" disabled /></label><label><span>显示名称</span><input v-model="displayName" maxlength="120" required /></label><button class="primary-button">保存资料</button>
        </form>
        <form v-else-if="ui.settingsSection === 'security'" class="setting-section" @submit.prevent="changePassword">
          <div class="security-note"><KeyRound :size="21" /><span><strong>修改密码后将退出当前账号</strong><small>所有现有登录 Session 会被撤销，需要重新登录。</small></span></div>
          <label><span>当前密码</span><input v-model="currentPassword" type="password" autocomplete="current-password" required /></label><label><span>新密码</span><input v-model="newPassword" type="password" autocomplete="new-password" minlength="8" required /></label><label><span>确认新密码</span><input v-model="confirmPassword" type="password" autocomplete="new-password" minlength="8" required /></label><button class="primary-button">修改密码</button>
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
