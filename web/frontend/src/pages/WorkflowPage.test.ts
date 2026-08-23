import { createPinia, setActivePinia } from "pinia";
import { flushPromises, mount } from "@vue/test-utils";
import { createMemoryHistory, createRouter } from "vue-router";
import { defineComponent, h, type PropType } from "vue";
import { describe, expect, it, vi } from "vitest";

import { api } from "@/api/client";
import type { WorkflowDetail } from "@/api/types";
import { useWorkflowStore } from "@/stores/workflows";
import WorkflowPage from "./WorkflowPage.vue";

interface StubNode { id: string; type: string; label: string; data: Record<string, unknown> }
interface StubEdge { id: string; source: string; target: string; sourceHandle?: string }

const VueFlowStub = defineComponent({
  name: "VueFlow",
  props: {
    nodes: { type: Array as PropType<StubNode[]>, default: () => [] },
    edges: { type: Array as PropType<StubEdge[]>, default: () => [] },
  },
  emits: ["pane-click", "node-click", "connect", "connect-start", "connect-end", "edge-click", "move", "node-drag", "node-drag-start", "node-drag-stop", "update:nodes", "update:edges"],
  setup(props, { slots, emit }) {
    return () => h("div", { class: "test-flow" }, [
      h("button", { class: "test-empty-canvas", type: "button", onClick: () => emit("pane-click") }, "canvas"),
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

async function mountWorkflowPage() {
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
    vi.spyOn(api, "workflowCapabilities").mockResolvedValue({});

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
          Handle: true,
        },
      },
    });
    await flushPromises();
    return { wrapper, store: useWorkflowStore() };
}

describe("WorkflowPage canvas interactions", () => {
  it("closes the node bubble when the empty canvas is clicked", async () => {
    const { wrapper } = await mountWorkflowPage();

    await wrapper.get('[data-node-id="trigger"]').trigger("click");
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".workflow-node-bubble-shell").exists()).toBe(true);

    await wrapper.get(".test-empty-canvas").trigger("click");
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".workflow-node-bubble-shell").exists()).toBe(false);

    wrapper.unmount();
  });

  it("connects existing nodes through the visible next-step action", async () => {
    const { wrapper } = await mountWorkflowPage();

    await wrapper.get('[data-node-id="trigger"] .workflow-node-connect').trigger("click");
    expect(wrapper.get(".workflow-connection-banner").text()).toContain("请选择要连接的下一步");
    expect(wrapper.get('[data-node-id="condition"] .workflow-node').attributes("data-connection-target")).toBe("true");

    await wrapper.get('[data-node-id="condition"]').trigger("click");
    await wrapper.vm.$nextTick();
    const flow = wrapper.findComponent(VueFlowStub);
    expect(flow.props("edges")).toEqual(expect.arrayContaining([
      expect.objectContaining({ source: "trigger", target: "condition", sourceHandle: "output" }),
    ]));
    expect(wrapper.find(".workflow-connection-banner").exists()).toBe(false);

    await wrapper.get('[data-node-id="condition"] .workflow-node-branch-actions button').trigger("click");
    await wrapper.get('[data-node-id="result"]').trigger("click");
    await wrapper.vm.$nextTick();
    expect(flow.props("edges")).toEqual(expect.arrayContaining([
      expect.objectContaining({ source: "condition", target: "result", sourceHandle: "true" }),
    ]));

    wrapper.unmount();
  });

  it("cancels next-step selection when the empty canvas is clicked", async () => {
    const { wrapper } = await mountWorkflowPage();
    await wrapper.get('[data-node-id="query"] .workflow-node-connect').trigger("click");
    expect(wrapper.find(".workflow-connection-banner").exists()).toBe(true);

    await wrapper.get(".test-empty-canvas").trigger("click");
    await wrapper.vm.$nextTick();
    expect(wrapper.find(".workflow-connection-banner").exists()).toBe(false);
    expect(wrapper.get('[data-node-id="condition"] .workflow-node').attributes("data-connection-target")).toBeUndefined();

    wrapper.unmount();
  });
});
