<script setup lang="ts">
import { ArrowRight, Eye, EyeOff, ShieldCheck, Sparkles } from "@lucide/vue";
import { computed, ref } from "vue";

import { uiText } from "@/i18n";
import QuickPreferences from "@/components/QuickPreferences.vue";
import { errorMessage } from "@/stores/chat";
import { useAuthStore } from "@/stores/auth";
import { useUiStore } from "@/stores/ui";

const props = withDefaults(defineProps<{ setup?: boolean; flow?: "default" | "qq-binding" }>(), { setup: false, flow: "default" });
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
const registering = computed(() => mode.value === "register" && !props.setup);
const usernameValid = computed(() => /^[A-Za-z0-9_.-]{3,64}$/.test(username.value));
const passwordValid = computed(() => password.value.length >= 8 && password.value.length <= 1024);
const confirmationValid = computed(() => confirmation.value.length > 0 && confirmation.value === password.value);
const registrationValid = computed(() => usernameValid.value && passwordValid.value && confirmationValid.value);
const panelTitle = computed(() => {
  if (props.setup) return tr("初始化系统所有者", "Initialize system owner");
  if (props.flow === "qq-binding") return mode.value === "login" ? tr("登录并绑定 QQ", "Sign in and connect QQ") : tr("创建账号并绑定 QQ", "Create account and connect QQ");
  return mode.value === "login" ? tr("欢迎回来", "Welcome back") : tr("创建账号", "Create account");
});

async function submit() {
  failure.value = "";
  if (registering.value && !usernameValid.value) {
    failure.value = tr("请按提示修改账号", "Please revise the account as indicated");
    return;
  }
  if (registering.value && !passwordValid.value) {
    failure.value = tr("密码必须为 8–1024 个字符", "Password must contain 8–1024 characters");
    return;
  }
  if (registering.value && !confirmationValid.value) {
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
  <main class="auth-page" :class="{ 'binding-auth-page': flow === 'qq-binding' }">
    <QuickPreferences class="auth-preferences-outside" />
    <section class="auth-slider" :class="{ 'is-register': mode === 'register' && !setup, 'is-setup': setup, 'is-channel-binding': flow === 'qq-binding' }">
      <div class="auth-brand-panel">
        <QuickPreferences class="auth-preferences-inside" />
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
          <div><h2>{{ panelTitle }}</h2><p>{{ setup ? tr('Owner 账号固定为 owner', 'The Owner account is fixed as owner') : flow === 'qq-binding' ? tr('完成后会自动绑定，无需再进入设置。', 'QQ will connect automatically after authentication.') : mode === 'login' ? tr('登录你的 ZhiCe-Agent 账号', 'Sign in to your ZhiCe-Agent account') : tr('新账号默认拥有普通用户角色', 'New accounts receive the standard user role') }}</p></div>
        </div>
        <label v-if="!setup" class="validated-field">
          <span>{{ tr('账号', 'Account') }}<em v-if="registering && username" :class="usernameValid ? 'is-valid' : 'is-invalid'">{{ usernameValid ? tr('可用', 'Valid') : tr('需调整', 'Revise') }}</em></span>
          <input v-model="username" autocomplete="username" required :minlength="registering ? 3 : undefined" :maxlength="registering ? 64 : undefined" :pattern="registering ? '[A-Za-z0-9._-]{3,64}' : undefined" :class="{ 'is-valid': registering && usernameValid, 'is-invalid': registering && username && !usernameValid }" :aria-invalid="registering && username ? !usernameValid : undefined" />
          <small v-if="registering" class="validation-bubble" :class="username && !usernameValid ? 'is-invalid' : ''">{{ tr('3–64 位，仅支持字母、数字、点、下划线和连字符', '3–64 characters: letters, numbers, dots, underscores, and hyphens') }}</small>
        </label>
        <label v-else><span>{{ tr('账号', 'Account') }}</span><input value="owner" disabled /></label>
        <label class="validated-field">
          <span>{{ tr('密码', 'Password') }}<em v-if="registering && password" :class="passwordValid ? 'is-valid' : 'is-invalid'">{{ passwordValid ? tr('符合要求', 'Valid') : tr('不足', 'Too short') }}</em></span>
          <span class="password-field"><input v-model="password" :type="passwordVisible ? 'text' : 'password'" :autocomplete="mode === 'login' && !setup ? 'current-password' : 'new-password'" minlength="8" maxlength="1024" required :class="{ 'is-valid': registering && passwordValid, 'is-invalid': registering && password && !passwordValid }" :aria-invalid="registering && password ? !passwordValid : undefined" /><button type="button" tabindex="-1" :aria-label="passwordVisible ? tr('隐藏密码', 'Hide password') : tr('显示密码', 'Show password')" @click="passwordVisible = !passwordVisible"><EyeOff v-if="passwordVisible" :size="18" /><Eye v-else :size="18" /></button></span>
          <small v-if="registering" class="validation-bubble" :class="password && !passwordValid ? 'is-invalid' : ''">{{ tr('至少 8 个字符', 'At least 8 characters') }}</small>
        </label>
        <label v-if="registering" class="validated-field">
          <span>{{ tr('确认密码', 'Confirm password') }}<em v-if="confirmation" :class="confirmationValid ? 'is-valid' : 'is-invalid'">{{ confirmationValid ? tr('一致', 'Matches') : tr('不一致', 'Mismatch') }}</em></span>
          <span class="password-field"><input v-model="confirmation" :type="confirmationVisible ? 'text' : 'password'" autocomplete="new-password" minlength="8" maxlength="1024" required :class="{ 'is-valid': confirmationValid, 'is-invalid': confirmation && !confirmationValid }" :aria-invalid="confirmation ? !confirmationValid : undefined" /><button type="button" tabindex="-1" :aria-label="confirmationVisible ? tr('隐藏确认密码', 'Hide confirmation password') : tr('显示确认密码', 'Show confirmation password')" @click="confirmationVisible = !confirmationVisible"><EyeOff v-if="confirmationVisible" :size="18" /><Eye v-else :size="18" /></button></span>
          <small class="validation-bubble" :class="confirmation && !confirmationValid ? 'is-invalid' : ''">{{ confirmation ? (confirmationValid ? tr('两次密码一致', 'Passwords match') : tr('与新密码不一致', 'Does not match the new password')) : tr('请再次输入新密码', 'Enter the new password again') }}</small>
        </label>
        <label v-if="setup"><span>{{ tr('初始化凭据', 'Setup credential') }}</span><span class="password-field"><input v-model="setupToken" :type="setupTokenVisible ? 'text' : 'password'" autocomplete="off" required /><button type="button" tabindex="-1" :aria-label="setupTokenVisible ? tr('隐藏初始化凭据', 'Hide setup credential') : tr('显示初始化凭据', 'Show setup credential')" @click="setupTokenVisible = !setupTokenVisible"><EyeOff v-if="setupTokenVisible" :size="18" /><Eye v-else :size="18" /></button></span></label>
        <p v-if="failure" class="form-error" role="alert">{{ failure }}</p>
        <button class="primary-button auth-submit" :disabled="busy || (registering && !registrationValid)" type="submit">{{ busy ? tr('处理中…', 'Working…') : setup ? tr('创建 Owner 并登录', 'Create Owner and sign in') : flow === 'qq-binding' ? mode === 'login' ? tr('登录并继续', 'Sign in and continue') : tr('创建账号并继续', 'Create account and continue') : mode === 'login' ? tr('登录', 'Sign in') : tr('创建并登录', 'Create and sign in') }}</button>
        <button v-if="!setup" class="mobile-mode-switch" type="button" @click="switchMode(mode === 'login' ? 'register' : 'login')"><span>{{ mode === 'login' ? tr('没有账号？', 'No account?') : tr('已有账号？', 'Already have an account?') }}</span><strong>{{ mode === 'login' ? tr('立即创建', 'Create one') : tr('返回登录', 'Sign in') }}</strong></button>
        <a v-if="setup" class="mobile-mode-switch" href="/">{{ tr('返回登录', 'Back to sign in') }}</a>
      </form>
    </section>
  </main>
</template>
