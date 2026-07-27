<script setup lang="ts">
import { ArrowRight, Eye, EyeOff, ShieldCheck, Sparkles } from "@lucide/vue";
import { computed, ref } from "vue";

import { errorMessage } from "@/stores/chat";
import { useAuthStore } from "@/stores/auth";

const props = withDefaults(defineProps<{ setup?: boolean }>(), { setup: false });
const emit = defineEmits<{ authenticated: [] }>();
const auth = useAuthStore();
const mode = ref<"login" | "register">(props.setup ? "login" : "login");
const username = ref("");
const password = ref("");
const confirmation = ref("");
const setupToken = ref("");
const visible = ref(false);
const busy = ref(false);
const failure = ref("");
const panelTitle = computed(() => props.setup ? "初始化系统所有者" : mode.value === "login" ? "欢迎回来" : "创建本地账号");

async function submit() {
  failure.value = "";
  if (!props.setup && mode.value === "register" && password.value !== confirmation.value) {
    failure.value = "两次输入的密码不一致";
    return;
  }
  busy.value = true;
  try {
    if (props.setup) await auth.bootstrap(setupToken.value, password.value);
    else if (mode.value === "login") await auth.login(username.value, password.value);
    else await auth.register(username.value, password.value);
    emit("authenticated");
  } catch (error) { failure.value = errorMessage(error); }
  finally { busy.value = false; }
}

function switchMode(next: "login" | "register") {
  mode.value = next;
  password.value = "";
  confirmation.value = "";
  failure.value = "";
}
</script>

<template>
  <main class="auth-page">
    <section class="auth-slider" :class="{ 'is-register': mode === 'register' && !setup, 'is-setup': setup }">
      <div class="auth-brand-panel">
        <div class="brand-lockup"><img :src="'/static/zhice-logo-a.png'" alt="" /><strong>ZhiCe-Agent</strong></div>
        <div class="auth-brand-copy">
          <span class="eyebrow"><Sparkles :size="16" /> 明亮曜石</span>
          <h1>{{ setup ? '建立你的本地智能工作台' : mode === 'login' ? '继续与智能同行' : '从一个清晰的对话开始' }}</h1>
          <p>{{ setup ? '此入口只在 Owner 尚未创建且配置了初始化凭据时可用。' : '会话、Memory、Tool 与渠道连接都留在清晰可控的本地边界中。' }}</p>
        </div>
        <button v-if="!setup" class="ghost-inverse" type="button" @click="switchMode(mode === 'login' ? 'register' : 'login')">
          {{ mode === 'login' ? '创建账号' : '已有账号' }} <ArrowRight :size="17" />
        </button>
      </div>

      <form class="auth-form-panel" @submit.prevent="submit">
        <div class="auth-form-heading">
          <span class="form-icon"><ShieldCheck :size="21" /></span>
          <div><h2>{{ panelTitle }}</h2><p>{{ setup ? 'Owner 用户名固定为 owner' : mode === 'login' ? '使用本地账号登录' : '新账号默认拥有普通用户角色' }}</p></div>
        </div>
        <label v-if="!setup"><span>用户名</span><input v-model="username" autocomplete="username" required placeholder="例如 zhangsan" /></label>
        <label v-else><span>用户名</span><input value="owner" disabled /></label>
        <label>
          <span>{{ setup || mode === 'register' ? '新密码' : '密码' }}</span>
          <span class="password-field"><input v-model="password" :type="visible ? 'text' : 'password'" :autocomplete="mode === 'login' && !setup ? 'current-password' : 'new-password'" minlength="8" required /><button type="button" :aria-label="visible ? '隐藏密码' : '显示密码'" @click="visible = !visible"><EyeOff v-if="visible" :size="18" /><Eye v-else :size="18" /></button></span>
        </label>
        <label v-if="mode === 'register' && !setup"><span>确认密码</span><input v-model="confirmation" :type="visible ? 'text' : 'password'" autocomplete="new-password" minlength="8" required /></label>
        <label v-if="setup"><span>初始化凭据</span><input v-model="setupToken" :type="visible ? 'text' : 'password'" autocomplete="off" required /></label>
        <p v-if="failure" class="form-error" role="alert">{{ failure }}</p>
        <button class="primary-button auth-submit" :disabled="busy" type="submit">{{ busy ? '处理中…' : setup ? '创建 Owner 并登录' : mode === 'login' ? '登录' : '创建并登录' }}</button>
        <button v-if="!setup" class="mobile-mode-switch" type="button" @click="switchMode(mode === 'login' ? 'register' : 'login')">{{ mode === 'login' ? '没有账号？立即创建' : '已有账号？返回登录' }}</button>
        <a v-if="setup" class="mobile-mode-switch" href="/">返回登录</a>
      </form>
    </section>
  </main>
</template>
