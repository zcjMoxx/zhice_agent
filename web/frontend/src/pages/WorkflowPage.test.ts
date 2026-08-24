import { createPinia, setActivePinia } from "pinia";
import { flushPromises, mount } from "@vue/test-utils";
import { createMemoryHistory, createRouter } from "vue-router";
import { MarkerType } from "@vue-flow/core";
import { defineComponent, h, type PropType } from "vue";
import { describe, expect, it, vi } from "vitest";

import { api } from "@/api/client";
import type { WorkflowCapabilities, WorkflowDetail } from "@/api/types";
import { useWorkflowStore } from "@/stores/workflows";
import WorkflowPage from "./WorkflowPage.vue";

interface StubNode { id: string; type: string; label: string; data: Record<string, unknown> }
interface StubEdge { id: string; source: string; target: string; sourceHandle?: string }

const HandleStub = defineComponent({
  name: "WorkflowHandleStub",
  inheritAttrs: false,
  props: {
    id: { type: String, default: "" },
    type: { type: String, required: true },
  },
  setup(props, { attrs }) {
    return () => h("button", {
      ...attrs,
      class: ["vue-flow__handle", attrs.class],
      type: "button",
      "data-handle-id": props.id,
      "data-handle-type": props.type,
    });
  },
});

const VueFlowStub = defineComponent({
  name: "VueFlow",
  props: {
    nodes: { type: Array as PropType<StubNode[]>, default: () => [] },
    edges: { type: Array as PropType<StubEdge[]>, default: () => [] },
    defaultEdgeOptions: { type: Object, default: () => ({}) },
  },
  emits: ["pane-click", "node-click", "connect", "connect-start", "connect-end", "click-connect-start", "click-connect-end", "edge-click", "move", "node-drag", "node-drag-start", "node-drag-stop", "update:nodes", "update:edges"],
  setup(props, { slots, emit }) {
    return () => h("div", { class: "test-flow" }, [
      h("button", { class: "test-empty-canvas vue-flow__pane", type: "button", onClick: (event: MouseEvent) => emit("pane-click", event) }, "canvas"),
      ...props.nodes.map((node) => h("div", {
        class: "vue-flow__node",
        "data-id": node.id,
        "data-node-id": node.id,
        onClick: () => emit("node-click", { node }),
      }, slots[`node-${node.type}`]?.(node) || slots["node-default"]?.(node))),
      ...(slots.default?.() || []),
    ]);
  },
});

const workflow: WorkflowDetail = {
  schema_version: 1,
  workflow_id: "workflow-test",
  owner_user_id: "user-test",
  name: "测试工作流",
  description: "",
  timezone: "Asia/Shanghai",
  status: "draft",
  version: 1,
  active_version: null,
  has_unpublished_changes: true,
  nodes: [
    { id: "trigger", type: "schedule_trigger", title: "开始", position: { x: 80, y: 120 }, config: { trigger_type: "manual", schedule_mode: "manual" } },
    { id: "query", type: "mcp_query", title: "查天气", position: { x: 360, y: 120 }, config: {} },
    { id: "condition", type: "condition", title: "判断天气", position: { x: 640, y: 120 }, config: {} },
    { id: "result", type: "template", title: "发送结果", position: { x: 920, y: 120 }, config: {} },
  ],
  edges: [{ id: "trigger-query", source_node_id: "trigger", target_node_id: "query", source_port: "output", target_port: "input" }],
};

async function mountWorkflowPage(capabilities: WorkflowCapabilities = {}) {
    const pinia = createPinia();
    setActivePinia(pinia);
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: "/", component: { template: "<div />" } },
        { path: "/workflows/:workflowId", name: "workflow-detail", component: WorkflowPage },
      ],
    });
    await router.push("/workflows/workflow-test");
    await router.isReady();

    vi.spyOn(api, "workflows").mockResolvedValue({ items: [] });
    vi.spyOn(api, "workflow").mockResolvedValue(workflow);
    vi.spyOn(api, "workflowRuns").mockResolvedValue({ items: [] });
    vi.spyOn(api, "workflowTools").mockResolvedValue({ items: [] });
    vi.spyOn(api, "workflowEmailConnections").mockResolvedValue({ connections: [] });
    vi.spyOn(api, "workflowCapabilities").mockResolvedValue(capabilities);

    const wrapper = mount(WorkflowPage, {
      global: {
        plugins: [pinia, router],
        stubs: {
          QuickPreferences: { template: "<div />" },
          DateTimePicker: { template: "<div />" },
          VueFlow: VueFlowStub,
          Background: true,
          MiniMap: true,
          Controls: true,
          BaseEdge: true,
          EdgeLabelRenderer: true,
          Handle: HandleStub,
        },
      },
    });
    await flushPromises();
    return { wrapper, store: useWorkflowStore() };
}

describe("WorkflowPage canvas interactions", () => {
  it("offers owner-bound Weixin delivery without asking for a Weixin identifier", async () => {
    const { wrapper } = await mountWorkflowPage({
      weixin_notification: { available: true, bound: true, code: "" },
    });

    await wrapper.get('[data-node-id="result"]').trigger("click");
    await wrapper.vm.$nextTick();
    const deliverySelect = wrapper.get(".workflow-inspector select");
    expect(deliverySelect.text()).toContain("微信通知");
    expect(deliverySelect.text()).toContain("仅作记录");
    expect(deliverySelect.text()).toContain("SMTP 发送");
    await deliverySelect.setValue("weixin");
    await wrapper.vm.$nextTick();
    expect(wrapper.text()).toContain("不需要填写微信号");
    expect(wrapper.text()).toContain("所有结果都会保存在执行记录中");
    expect(wrapper.find('input[placeholder="name@example.com"]').exists()).toBe(false);

    wrapper.unmount();
  });

  it("groups recurring schedules behind one concise run mode", async () => {
    const { wrapper } = await mountWorkflowPage();

    await wrapper.get('[data-node-id="trigger"]').trigger("click");
    await wrapper.vm.$nextTick();
    const runMode = wrapper.findAll(".workflow-inspector select")[0];
    expect(runMode.text()).toContain("手动触发");
    expect(runMode.text()).toContain("周期运行");
    expect(runMode.text()).toContain("定时一次");
    expect(runMode.text()).not.toContain("每天运行");

    await runMode.setValue("recurring");
    await wrapper.vm.$nextTick();
    const repeatMode = wrapper.findAll(".workflow-inspector select")[1];
    expect(repeatMode.text()).toContain("固定间隔");
    expect(repeatMode.text()).toContain("每天运行");
    expect(repeatMode.text()).toContain("每周运行");
    expect(repeatMode.text()).toContain("每月运行");

    await wrapper.findAll(".workflow-inspector select")[0].setValue("once");
    await wrapper.vm.$nextTick();
    expect(wrapper.text()).toContain("在指定时间自动运行一次");
    expect(wrapper.text()).not.toContain("重复周期");

    wrapper.unmount();
  });

  it("shows saved content and delivery receipt, then collapses the same run", async () => {
    vi.spyOn(api, "workflowRun").mockResolvedValue({
      run_id: "run-mail",
      workflow_id: "workflow-test",
      status: "succeeded",
      results: [{
        node_id: "mail",
        node_type: "personal_email",
        status: "succeeded",
        content_summary: "今天带伞，最高 28℃。",
        delivery_summary: '{"status":"sent","provider_message_id":"mail-1"}',
      }],
      nodes: [{
        node_id: "mail",
        node_type: "personal_email",
        status: "succeeded",
        attempt: 1,
        input_summary: '{"content":"今天带伞，最高 28℃。"}',
        output_summary: '{"status":"sent"}',
      }, {
        node_id: "query",
        node_type: "mcp_query",
        status: "succeeded",
        attempt: 1,
        input_summary: '{"arguments":{"city":"杭州"}}',
        output_summary: JSON.stringify({ data: "天".repeat(520) }),
      }, {
        node_id: "result",
        node_type: "template",
        status: "succeeded",
        attempt: 1,
        input_summary: '{"source_ref":{"text":"今天带伞"}}',
        output_summary: '{"text":"今天带伞"}',
      }],
    });
    const { wrapper, store } = await mountWorkflowPage();
    const runsTab = wrapper.findAll(".workflow-view-tabs button").find((button) => button.text().includes("执行记录"));
    await runsTab!.trigger("click");
    store.runs = [{ run_id: "run-mail", workflow_id: "workflow-test", status: "succeeded" }];
    await wrapper.vm.$nextTick();

    await wrapper.get(".workflow-runs button").trigger("click");
    await flushPromises();
    expect(wrapper.get(".run-result-section").text()).toContain("本次运行结果");
    expect(wrapper.get(".run-final-result").text()).toContain("今天带伞，最高 28℃。");
    expect(wrapper.get(".run-final-result").text()).toContain("SMTP 投递结果");
    expect(wrapper.get(".run-final-result").text()).toContain("mail-1");

    const steps = wrapper.findAll(".run-step-detail");
    await steps[0].get("summary").trigger("click");
    expect(steps[0].attributes("open")).toBeDefined();
    expect(steps[1].attributes("open")).toBeUndefined();
    expect(steps[0].text()).toContain("发送内容摘要");
    expect(steps[0].text()).toContain("投递结果");
    expect(steps[0].text()).not.toContain("输入摘要");
    await steps[1].get("summary").trigger("click");
    expect(steps[0].attributes("open")).toBeUndefined();
    expect(steps[1].attributes("open")).toBeDefined();
    expect(steps[1].text()).toContain("查询条件");
    expect(steps[1].text()).toContain("查询结果摘要");
    expect(steps[1].text()).toContain("已省略");
    const expandButton = steps[1].findAll(".run-summary-actions button").find((button) => button.text().includes("展开全部"));
    expect(expandButton).toBeDefined();
    await expandButton!.trigger("click");
    expect(steps[1].text()).toContain("收起");
    await steps[1].get("summary").trigger("click");
    expect(steps[1].attributes("open")).toBeUndefined();
    await steps[2].get("summary").trigger("click");
    expect(steps[2].text()).toContain("结果内容");
    expect(steps[2].text()).not.toContain("输入摘要");
    expect(steps[2].text()).not.toContain("输出摘要");

    await wrapper.get(".workflow-runs button").trigger("click");
    await flushPromises();
    expect(wrapper.find(".run-detail").exists()).toBe(false);
    expect(api.workflowRun).toHaveBeenCalledTimes(1);
    wrapper.unmount();
  });

  it("shows directional arrows and explicit workflow lifecycle actions", async () => {
    const { wrapper } = await mountWorkflowPage();
    const flow = wrapper.findComponent(VueFlowStub);

    expect(flow.props("defaultEdgeOptions")).toEqual({ markerEnd: MarkerType.ArrowClosed });
    expect(wrapper.text()).toContain("已停用");
    expect(wrapper.text()).toContain("保存草稿");
    expect(wrapper.text()).toContain("发布并启用");
    expect(wrapper.text()).toContain("立即试运行");
    expect(wrapper.get(".workflow-power-switch").attributes("disabled")).toBeDefined();

    wrapper.unmount();
  });

  it("confirms a successful manual save and omits the detached node-details opener", async () => {
    const saved = { ...workflow, version: 2 };
    vi.spyOn(api, "saveWorkflowDraft").mockResolvedValue(saved);
    const { wrapper } = await mountWorkflowPage();

    expect(wrapper.find(".inspector-open-button").exists()).toBe(false);
    const saveButton = wrapper.findAll(".workflow-editor-actions > button").find((button) => button.text().includes("保存草稿"));
    expect(saveButton).toBeDefined();
    await saveButton!.trigger("click");
    await flushPromises();

    expect(api.saveWorkflowDraft).toHaveBeenCalled();
    expect(saveButton!.text()).toContain("已保存到工作流");

    await wrapper.get('[data-node-id="trigger"]').trigger("click");
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".workflow-node-bubble-shell").exists()).toBe(true);
    await wrapper.get(".inspector-header-actions .icon-button").trigger("click");
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".workflow-node-bubble-shell").exists()).toBe(false);
    expect(wrapper.find(".inspector-open-button").exists()).toBe(false);

    wrapper.unmount();
  });

  it("closes the node bubble when the empty canvas is clicked", async () => {
    const { wrapper } = await mountWorkflowPage();
    const flow = wrapper.findComponent(VueFlowStub);

    await wrapper.get('[data-node-id="trigger"]').trigger("click");
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".workflow-node-bubble-shell").exists()).toBe(true);

    flow.vm.$emit("click-connect-start", { nodeId: "trigger", handleId: "output" });
    await wrapper.vm.$nextTick();
    expect(wrapper.get(".workflow-canvas").attributes("data-connection-active")).toBe("true");
    expect(wrapper.find(".workflow-click-connection-preview").exists()).toBe(true);

    await wrapper.get(".workflow-canvas").trigger("pointermove", { clientX: 320, clientY: 210 });
    await wrapper.vm.$nextTick();
    expect(wrapper.get(".workflow-click-connection-preview path").attributes("d")).toContain("320 210");

    await wrapper.get(".test-empty-canvas").trigger("click");
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".workflow-node-bubble-shell").exists()).toBe(false);
    expect(wrapper.get(".workflow-canvas").attributes("data-connection-active")).toBeUndefined();
    expect(wrapper.find(".workflow-click-connection-preview").exists()).toBe(false);

    wrapper.unmount();
  });

  it("uses handles instead of next-step buttons and keeps handle clicks out of the inspector", async () => {
    const { wrapper } = await mountWorkflowPage();

    expect(wrapper.find(".workflow-node-connect").exists()).toBe(false);
    expect(wrapper.find(".workflow-node-branch-actions").exists()).toBe(false);

    await wrapper.get('[data-node-id="trigger"] [data-handle-type="source"]').trigger("mousedown");
    await wrapper.get('[data-node-id="trigger"] [data-handle-type="source"]').trigger("pointerdown");
    await wrapper.get('[data-node-id="trigger"] [data-handle-type="source"]').trigger("click");
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".workflow-node-bubble-shell").exists()).toBe(false);

    wrapper.unmount();
  });

  it("connects ordinary and condition handles through Vue Flow", async () => {
    const { wrapper } = await mountWorkflowPage();
    const flow = wrapper.findComponent(VueFlowStub);

    flow.vm.$emit("connect", { source: "trigger", target: "condition", sourceHandle: "output", targetHandle: "input" });
    flow.vm.$emit("connect", { source: "condition", target: "result", sourceHandle: "true", targetHandle: "input" });
    await wrapper.vm.$nextTick();

    expect(flow.props("edges")).toEqual(expect.arrayContaining([
      expect.objectContaining({ source: "trigger", target: "condition", sourceHandle: "output" }),
      expect.objectContaining({ source: "condition", target: "result", sourceHandle: "true" }),
    ]));

    wrapper.unmount();
  });

  it("does not open the add-node menu when a dragged connection ends on the pane", async () => {
    const { wrapper } = await mountWorkflowPage();
    const flow = wrapper.findComponent(VueFlowStub);
    const pane = wrapper.get(".test-empty-canvas").element;

    flow.vm.$emit("connect-start", { nodeId: "trigger", handleId: "output" });
    flow.vm.$emit("connect-end", { target: pane, clientX: 240, clientY: 180 });
    await wrapper.vm.$nextTick();

    expect(wrapper.find(".quick-add-menu").exists()).toBe(false);
    wrapper.unmount();
  });
});
