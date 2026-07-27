<script setup lang="ts">
import { computed, ref, watch } from "vue";

import { useChatStore } from "@/stores/chat";

const chat = useChatStore();
const elicitationJson = ref("{}");
const parseError = ref("");
const elicitationSchema = computed(() => JSON.stringify(chat.elicitation?.requested_schema || {}, null, 2));
watch(() => chat.elicitation?.interaction_id, () => { elicitationJson.value = "{}"; parseError.value = ""; });

async function acceptElicitation() {
  try {
    const value = JSON.parse(elicitationJson.value) as unknown;
    if (!value || Array.isArray(value) || typeof value !== "object") throw new Error("响应必须是 JSON 对象");
    await chat.respondToElicitation("accept", value as Record<string, unknown>);
  } catch (error) { parseError.value = error instanceof Error ? error.message : "JSON 无效"; }
}
</script>

<template>
  <div v-if="chat.confirmation" class="modal-backdrop">
    <section class="dialog-card"><span class="risk-pill">{{ chat.confirmation.risk_level || 'high' }}</span><h2>确认 Tool 操作</h2><dl><dt>Tool</dt><dd>{{ chat.confirmation.tool_name }}</dd><dt v-if="chat.confirmation.command_preview">命令预览</dt><dd v-if="chat.confirmation.command_preview"><code>{{ chat.confirmation.command_preview }}</code></dd></dl><p>仅批准本次已校验参数；修改后的参数仍需重新经过安全检查。</p><div class="dialog-actions"><button @click="chat.decideConfirmation(false)">拒绝</button><button class="danger-button" @click="chat.decideConfirmation(true)">仅批准一次</button></div></section>
  </div>
  <div v-if="chat.elicitation" class="modal-backdrop">
    <section class="dialog-card"><h2>{{ chat.elicitation.server_id ? `MCP · ${chat.elicitation.server_id}` : 'MCP 请求输入' }}</h2><p>{{ chat.elicitation.message || '外部 MCP Server 请求结构化输入。' }}</p><a v-if="chat.elicitation.url" :href="chat.elicitation.url" target="_blank" rel="noreferrer">打开关联页面</a><details v-if="elicitationSchema !== '{}'" class="technical-details"><summary>请求 Schema</summary><pre>{{ elicitationSchema }}</pre></details><label><span>JSON 响应</span><textarea v-model="elicitationJson" rows="7" /></label><p v-if="parseError" class="form-error">{{ parseError }}</p><div class="dialog-actions"><button @click="chat.respondToElicitation('cancel', null)">取消</button><button class="primary-button" @click="acceptElicitation">提交</button></div></section>
  </div>
</template>
