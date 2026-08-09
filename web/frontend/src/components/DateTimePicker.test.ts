import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import DateTimePicker from "./DateTimePicker.vue";

describe("DateTimePicker", () => {
  it("opens from the whole field and confirms an explicit date and time", async () => {
    const wrapper = mount(DateTimePicker, { props: { modelValue: "2026-08-08T14:30", label: "开始时间", language: "zh-CN" } });

    await wrapper.get(".date-picker-trigger").trigger("click");
    expect(wrapper.text()).toContain("2026 年 8 月");
    expect(wrapper.text()).toContain("时");
    expect(wrapper.text()).toContain("分");
    await wrapper.findAll(".date-picker-days button").find((button) => button.text() === "9")!.trigger("click");
    await wrapper.get(".date-picker-time label:first-of-type select").setValue("16");
    await wrapper.get(".date-picker-time label:last-of-type select").setValue("45");
    await wrapper.findAll(".date-picker-actions button").find((button) => button.text() === "确定")!.trigger("click");

    expect(wrapper.emitted("update:modelValue")?.[0]).toEqual(["2026-08-09T16:45"]);
  });

  it("switches to year and month selection from the calendar title", async () => {
    const wrapper = mount(DateTimePicker, { props: { modelValue: "2026-08-08T14:30", label: "开始时间", language: "zh-CN" } });
    await wrapper.get(".date-picker-trigger").trigger("click");
    await wrapper.get(".date-picker-title").trigger("click");

    expect(wrapper.find(".date-picker-months").exists()).toBe(true);
    expect(wrapper.text()).toContain("8 月");
    await wrapper.findAll(".date-picker-months button")[8].trigger("click");
    expect(wrapper.text()).toContain("2026 年 9 月");
  });

  it("prevents a confirmed value earlier than the minimum", async () => {
    const wrapper = mount(DateTimePicker, { props: { modelValue: "", minValue: "2026-08-08T16:30", label: "结束时间", language: "zh-CN" } });
    await wrapper.get(".date-picker-trigger").trigger("click");
    await wrapper.get(".date-picker-time label:first-of-type select").setValue("15");

    expect(wrapper.text()).toContain("结束时间不能早于开始时间");
    expect(wrapper.get(".date-picker-actions .primary-button").attributes("disabled")).toBeDefined();
  });
});
