import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import type { TravelRequirementDraft } from "@/api/types";
import TravelPlanForm from "./TravelPlanForm.vue";

describe("TravelPlanForm", () => {
  it("emits natural input to the travel intake Agent without local canned replies", async () => {
    const wrapper = mount(TravelPlanForm);

    await send(wrapper, "你是谁");

    expect(wrapper.emitted("intakeMessage")?.[0]).toEqual(["你是谁"]);
    expect(wrapper.text()).not.toContain("不是独立的小模型");
    expect(wrapper.text()).not.toContain("好呀，再告诉我");
  });

  it("renders the Agent natural reply exactly as supplied by the store", () => {
    const wrapper = mount(TravelPlanForm, {
      props: {
        restoredConversation: [
          { role: "user", content: "你是谁" },
          { role: "assistant", content: "我是智策旅行助手，主要帮你整理旅行条件和规划行程。" },
        ],
      },
    });

    expect(wrapper.text()).toContain("我是智策旅行助手，主要帮你整理旅行条件和规划行程。");
    expect(wrapper.text()).not.toContain("工作模式");
  });

  it("renders assistant Markdown with the same safe renderer as main chat", () => {
    const wrapper = mount(TravelPlanForm, {
      props: {
        restoredConversation: [
          { role: "user", content: "你好" },
          { role: "assistant", content: "先告诉我两项：**从哪出发、想去哪里**？" },
        ],
      },
    });

    const assistant = wrapper.get(".travel-requirement-message.assistant");
    expect(assistant.get("strong").text()).toBe("从哪出发、想去哪里");
    expect(assistant.text()).not.toContain("**");
    expect(wrapper.get(".travel-requirement-message.user p").text()).toBe("你好");
  });

  it("updates the review form from a server draft event and confirms with a short public summary", async () => {
    const wrapper = mount(TravelPlanForm, {
      props: {
        restoredConversation: [
          { role: "user", content: "国庆重庆去大理，两个人" },
          { role: "assistant", content: "条件齐了，可以确认开始规划。" },
        ],
        restoredDraft: completeDraft(),
      },
    });

    expect(wrapper.text()).toContain("关键信息已经齐全");
    await wrapper.get(".travel-requirement-ready .primary-button").trigger("click");

    const emitted = wrapper.emitted("submit")?.[0];
    expect(String(emitted?.[0])).toContain("我已确认旅行条件：重庆出发，前往大理");
    expect(String(emitted?.[0])).not.toContain("finalize_travel_plan");
    expect(emitted?.[2]).toEqual(completeDraft());
  });

  it("reveals the tone cards last and submits immediately after one selection", async () => {
    const draft = { ...completeDraft(), budget_level: "" as const };
    const wrapper = mount(TravelPlanForm, {
      props: {
        restoredConversation: [
          { role: "user", content: "重庆去大理五天，两个人" },
          { role: "assistant", content: "还差旅行基调，请选经济、舒适或品质。" },
        ],
        restoredDraft: draft,
      },
    });

    expect(wrapper.findAll(".travel-tone-picker button")).toHaveLength(3);
    expect(wrapper.text()).toContain("经济实惠");
    expect(wrapper.text()).toContain("舒适均衡");
    expect(wrapper.text()).toContain("轻松品质");
    expect(wrapper.text()).toContain("精确预算、交通、住宿、兴趣、节奏或硬约束");
    expect(wrapper.find(".travel-requirement-ready").exists()).toBe(false);
    const dialogChildren = Array.from(wrapper.get(".travel-requirement-dialog").element.children);
    expect(dialogChildren.indexOf(wrapper.get(".travel-tone-picker").element)).toBeGreaterThan(
      dialogChildren.indexOf(wrapper.findAll(".travel-requirement-message")[1].element),
    );

    await wrapper.findAll(".travel-tone-picker button")[1].trigger("click");

    expect(wrapper.text()).toContain("已选择旅行基调：舒适均衡");
    expect(wrapper.find(".travel-tone-picker").exists()).toBe(false);
    expect(wrapper.emitted("submit")).toHaveLength(1);
    expect(wrapper.emitted("submit")?.[0]?.[1]).toEqual([
      { role: "user", content: "重庆去大理五天，两个人" },
      { role: "user", content: "已选择旅行基调：舒适均衡" },
    ]);
    expect((wrapper.emitted("submit")?.[0]?.[2] as TravelRequirementDraft).budget_level).toBe("balanced");
  });

  it("does not show tone choices until every other required field is ready", () => {
    const wrapper = mount(TravelPlanForm, {
      props: {
        restoredConversation: [{ role: "assistant", content: "还需要确认出发日期。" }],
        restoredDraft: { ...completeDraft(), start_date: "", budget_level: "" },
      },
    });

    expect(wrapper.find(".travel-tone-picker").exists()).toBe(false);
  });

  it("keeps tone cards hidden before intake and clears the previous tone for a new plan", async () => {
    const wrapper = mount(TravelPlanForm, {
      props: {
        restoredConversation: [
          { role: "user", content: "重庆去大理" },
          { role: "assistant", content: "请选择旅行基调。" },
        ],
        restoredDraft: { ...completeDraft(), budget_level: "balanced" },
      },
    });

    expect(wrapper.find(".travel-tone-picker").exists()).toBe(false);

    await wrapper.setProps({ restoredConversation: [], restoredDraft: null });

    expect(wrapper.find(".travel-tone-picker").exists()).toBe(false);
    await wrapper.get(".travel-supplement-button").trigger("click");
    expect((wrapper.get("select").element as HTMLSelectElement).value).toBe("");
  });

  it("shows an Agent-triggered main chat handoff and preserves the original question", async () => {
    const wrapper = mount(TravelPlanForm, {
      props: {
        restoredConversation: [
          { role: "user", content: "帮我写 Python" },
          { role: "assistant", content: "这里专注旅行规划，这个问题可以带回主聊天继续。" },
        ],
        handoffQuestion: "帮我写 Python",
      },
    });

    await wrapper.get(".travel-chat-handoff .primary-button").trigger("click");
    await wrapper.get(".travel-chat-handoff button:last-child").trigger("click");

    expect(wrapper.emitted("handoffChat")?.[0]).toEqual(["帮我写 Python"]);
    expect(wrapper.emitted("dismissHandoff")).toHaveLength(1);
  });

  it("disables sending and shows thinking state while the intake Agent is running", async () => {
    const wrapper = mount(TravelPlanForm, { props: { intakeBusy: true } });
    const textarea = wrapper.get("textarea");
    await textarea.setValue("我想去大理");

    expect(wrapper.get('[aria-label="发送旅行需求"]').attributes("disabled")).toBeDefined();
    expect(wrapper.text()).toContain("旅行助手正在思考");
    expect(wrapper.text()).toContain("正在交流");
  });

  it("opens a manual form with Beijing date defaults and keeps invalid ranges blocked", async () => {
    const wrapper = mount(TravelPlanForm);
    await wrapper.get(".travel-supplement-button").trigger("click");

    const today = beijingToday();
    expect((wrapper.get('[aria-label="开始日期"]').element as HTMLInputElement).value).toBe(today);
    expect((wrapper.get('[aria-label="结束日期"]').element as HTMLInputElement).value).toBe(today);
    expect(wrapper.findAll(".travel-date-field.defaulted")).toHaveLength(2);

    await wrapper.findAll("input")[0].setValue("重庆");
    await wrapper.findAll("input")[1].setValue("大理");
    const dates = wrapper.findAll('input[type="date"]');
    await dates[0].setValue("2026-10-05");
    await dates[1].setValue("2026-10-01");
    await wrapper.get('input[type="number"][max="50"]').setValue(2);

    expect(wrapper.text()).toContain("有效日期范围");
    expect(wrapper.get(".travel-inspector-actions .primary-button").attributes("disabled")).toBeDefined();
  });

  it("uses Enter and the paper-plane button for the same Agent message event", async () => {
    const wrapper = mount(TravelPlanForm);
    const textarea = wrapper.get("textarea");
    await textarea.setValue("大理几月合适");
    await textarea.trigger("keydown", { key: "Enter" });

    await textarea.setValue("两个人，国庆出发");
    await wrapper.get('[aria-label="发送旅行需求"]').trigger("click");

    expect(wrapper.emitted("intakeMessage")).toEqual([
      ["大理几月合适"],
      ["两个人，国庆出发"],
    ]);
  });

  it("carries the selected travel tone into the natural-language Agent turn", async () => {
    const wrapper = mount(TravelPlanForm, {
      props: {
        restoredConversation: [{ role: "assistant", content: "请选择旅行基调。" }],
        restoredDraft: { ...completeDraft(), budget_level: "" },
      },
    });
    await wrapper.get('[aria-label="旅行基调"] button').trigger("click");

    await send(wrapper, "8月去重庆两天");

    expect(wrapper.emitted("intakeMessage")?.[0]).toEqual([
      "8月去重庆两天（旅行基调：经济实惠）",
    ]);
  });

  it("restores saved intake conversation in read-only history mode", () => {
    const wrapper = mount(TravelPlanForm, {
      props: {
        historyMode: true,
        restoredConversation: [
          { role: "user", content: "国庆重庆去新疆五天" },
          { role: "assistant", content: "从重庆出发，对吗？" },
        ],
      },
    });

    expect(wrapper.text()).toContain("生成这份计划时的需求问答");
    expect(wrapper.text()).toContain("从重庆出发，对吗？");
    expect(wrapper.find("textarea").exists()).toBe(false);
  });

  it("hides the confirmation card while intake is thinking, planning, and in history", () => {
    const props = {
      restoredConversation: [{ role: "user" as const, content: "国庆重庆去大理" }],
      restoredDraft: completeDraft(),
    };

    const planning = mount(TravelPlanForm, { props: { ...props, busy: true } });
    const intake = mount(TravelPlanForm, { props: { ...props, intakeBusy: true } });
    const history = mount(TravelPlanForm, { props: { ...props, historyMode: true } });

    expect(planning.find(".travel-requirement-ready").exists()).toBe(false);
    expect(intake.find(".travel-requirement-ready").exists()).toBe(false);
    expect(history.find(".travel-requirement-ready").exists()).toBe(false);
  });

  it("blocks confirmation in the review panel while intake conditions are updating", async () => {
    const wrapper = mount(TravelPlanForm, {
      props: {
        restoredConversation: [{ role: "user", content: "北京去沈阳" }],
        restoredDraft: completeDraft(),
      },
    });
    await wrapper.get(".travel-supplement-button").trigger("click");
    await wrapper.setProps({ intakeBusy: true });

    const confirm = wrapper.get(".travel-inspector-actions .primary-button");
    expect(confirm.attributes("disabled")).toBeDefined();
    expect(confirm.text()).toContain("正在更新条件");
    await confirm.trigger("click");
    expect(wrapper.emitted("submit")).toBeUndefined();
  });
});

async function send(wrapper: ReturnType<typeof mount>, text: string) {
  await wrapper.get("textarea").setValue(text);
  await wrapper.get("form").trigger("submit");
}

function completeDraft(): TravelRequirementDraft {
  return {
    intent: "travel_requirement",
    intent_topic: "",
    origin: "重庆",
    destinations: ["大理"],
    start_date: "2026-10-01",
    end_date: "2026-10-05",
    traveller_type: "大学生",
    traveller_count: 2,
    budget_total_cny: 5000,
    budget_level: "balanced",
    transport_preferences: ["铁路优先"],
    stay_preferences: [],
    interest_tags: ["自然风光"],
    pace: "balanced",
    planning_mode: "deep",
    hard_constraints: ["不租车"],
  };
}

function beijingToday() {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(new Date());
  const value = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  return `${value.year}-${value.month}-${value.day}`;
}
