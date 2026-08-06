<script setup lang="ts">
import { ArrowRight, Eye, EyeOff, ShieldCheck, Sparkles } from "@lucide/vue";
import { computed, ref } from "vue";

import { uiText } from "@/i18n";
import QuickPreferences from "@/components/QuickPreferences.vue";
import { errorMessage } from "@/stores/chat";
import { useAuthStore } from "@/stores/auth";
import { useUiStore } from "@/stores/ui";

const props = withDefaults(defineProps<{ setup?: boolean }>(), { setup: false });
const emit = defineEmits<{ authenticated: [] }>();
const auth = useAuthStore();
const ui = useUiStore();
const mode = ref<"login" | "register">(props.setup ? "login" : "login");
const username = ref("");
const password = ref("");
const confirmation = ref("");
const setupToken = ref("");
const passwordVisible = ref(false);
const confirmationVisible = ref(false);
const setupTokenVisible = ref(false);
const busy = ref(false);
const failure = ref("");
function tr(chinese: string, english: string): string { return uiText(ui.language, chinese, english); }
const panelTitle = computed(() => props.setup ? tr("初始化系统所有者", "Initialize system owner") : mode.value === "login" ? tr("欢迎回来", "Welcome back") : tr("创建本地账号", "Create a local account"));

async function submit() {
  failure.value = "";
  if (!props.setup && mode.value === "register" && password.value !== confirmation.value) {
    failure.value = tr("两次输入的密码不一致", "The passwords do not match");
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
  passwordVisible.value = false;
  confirmationVisible.value = false;
  setupTokenVisible.value = false;
  failure.value = "";
}
</script>

<template>
  <main class="auth-page">
    <QuickPreferences />
    <section class="auth-slider" :class="{ 'is-register': mode === 'register' && !setup, 'is-setup': setup }">
      <div class="auth-brand-panel">
        <div class="brand-lockup"><img :src="'/static/zhice-logo-a.png'" alt="" /><strong>ZhiCe-Agent</strong></div>
        <div class="auth-brand-copy">
          <span class="eyebrow"><Sparkles :size="16" /> {{ tr('松雾晨光', 'Pine Mist Dawn') }}</span>
          <h1>{{ setup ? tr('建立你的本地智能工作台', 'Build your local intelligent workspace') : mode === 'login' ? tr('继续与智能同行', 'Continue with intelligence') : tr('从一个清晰的对话开始', 'Start with a clear conversation') }}</h1>
          <p>{{ setup ? tr('此入口只在 Owner 尚未创建且配置了初始化凭据时可用。', 'This entry is available only before the Owner exists and with setup credentials configured.') : tr('让每一次对话，都离完成更近一步。', 'Let every conversation bring you one step closer to completion.') }}</p>
        </div>
        <button v-if="!setup" class="ghost-inverse" type="button" @click="switchMode(mode === 'login' ? 'register' : 'login')">
          {{ mode === 'login' ? tr('创建账号', 'Create account') : tr('已有账号', 'I have an account') }} <ArrowRight :size="17" />
        </button>
      </div>

      <form class="auth-form-panel" @submit.prevent="submit">
        <div class="auth-form-heading">
          <span class="form-icon"><ShieldCheck :size="21" /></span>
          <div><h2>{{ panelTitle }}</h2><p>{{ setup ? tr('Owner 用户名固定为 owner', 'The Owner username is fixed as owner') : mode === 'login' ? tr('登录你的 ZhiCe-Agent 账号', 'Sign in to your ZhiCe-Agent account') : tr('新账号默认拥有普通用户角色', 'New accounts receive the standard user role') }}</p></div>
        </div>
        <label v-if="!setup"><span>{{ tr('用户名', 'Username') }}</span><input v-model="username" autocomplete="username" required :placeholder="tr('例如 zhangsan', 'e.g. alex')" /></label>
        <label v-else><span>{{ tr('用户名', 'Username') }}</span><input value="owner" disabled /></label>
        <label>
          <span>{{ setup || mode === 'register' ? tr('新密码', 'New password') : tr('密码', 'Password') }}</span>
          <span class="password-field"><input v-model="password" :type="passwordVisible ? 'text' : 'password'" :autocomplete="mode === 'login' && !setup ? 'current-password' : 'new-password'" minlength="8" required /><button type="button" tabindex="-1" :aria-label="passwordVisible ? tr('隐藏密码', 'Hide password') : tr('显示密码', 'Show password')" @click="passwordVisible = !passwordVisible"><EyeOff v-if="passwordVisible" :size="18" /><Eye v-else :size="18" /></button></span>
        </label>
        <label v-if="mode === 'register' && !setup"><span>{{ tr('确认密码', 'Confirm password') }}</span><span class="password-field"><input v-model="confirmation" :type="confirmationVisible ? 'text' : 'password'" autocomplete="new-password" minlength="8" required /><button type="button" tabindex="-1" :aria-label="confirmationVisible ? tr('隐藏确认密码', 'Hide confirmation password') : tr('显示确认密码', 'Show confirmation password')" @click="confirmationVisible = !confirmationVisible"><EyeOff v-if="confirmationVisible" :size="18" /><Eye v-else :size="18" /></button></span></label>
        <label v-if="setup"><span>{{ tr('初始化凭据', 'Setup credential') }}</span><span class="password-field"><input v-model="setupToken" :type="setupTokenVisible ? 'text' : 'password'" autocomplete="off" required /><button type="button" tabindex="-1" :aria-label="setupTokenVisible ? tr('隐藏初始化凭据', 'Hide setup credential') : tr('显示初始化凭据', 'Show setup credential')" @click="setupTokenVisible = !setupTokenVisible"><EyeOff v-if="setupTokenVisible" :size="18" /><Eye v-else :size="18" /></button></span></label>
        <p v-if="failure" class="form-error" role="alert">{{ failure }}</p>
        <button class="primary-button auth-submit" :disabled="busy" type="submit">{{ busy ? tr('处理中…', 'Working…') : setup ? tr('创建 Owner 并登录', 'Create Owner and sign in') : mode === 'login' ? tr('登录', 'Sign in') : tr('创建并登录', 'Create and sign in') }}</button>
        <button v-if="!setup" class="mobile-mode-switch" type="button" @click="switchMode(mode === 'login' ? 'register' : 'login')">{{ mode === 'login' ? tr('没有账号？立即创建', 'No account? Create one') : tr('已有账号？返回登录', 'Already have an account? Sign in') }}</button>
        <a v-if="setup" class="mobile-mode-switch" href="/">{{ tr('返回登录', 'Back to sign in') }}</a>
      </form>
    </section>
  </main>
</template>
